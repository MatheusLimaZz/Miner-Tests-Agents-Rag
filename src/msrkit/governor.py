"""Rate-limit governor: token bucket, backoff, and quota accounting.

Ensures that no adapter exceeds its declared rate limits. Supports
injectable Clock for deterministic testing.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.models import RateLimit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Clock protocol (injectable for testing)
# ---------------------------------------------------------------------------


class Clock(Protocol):
    """Injectable clock for deterministic testing."""

    def now(self) -> float:
        """Return current time as a Unix timestamp."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for the given number of seconds."""
        ...


class RealClock:
    """Real wall-clock implementation."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ---------------------------------------------------------------------------
# Token Bucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Token bucket rate limiter with configurable refill rate.

    Args:
        rate: Number of tokens added per second.
        capacity: Maximum number of tokens in the bucket.
        clock: Injectable clock for testing.
    """

    def __init__(self, rate: float, capacity: int, clock: Clock | None = None) -> None:
        self._clock = clock or RealClock()
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = self._clock.now()

    def _refill(self) -> None:
        now = self._clock.now()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, tokens: int = 1) -> float:
        """Acquire tokens, blocking if necessary.

        Returns:
            The time waited (in seconds), 0.0 if no wait was needed.
        """
        self._refill()
        waited = 0.0
        while self._tokens < tokens:
            deficit = tokens - self._tokens
            wait_time = deficit / self._rate
            self._clock.sleep(wait_time)
            waited += wait_time
            self._refill()
        self._tokens -= tokens
        return waited

    @property
    def available(self) -> float:
        """Current number of available tokens (may be fractional)."""
        self._refill()
        return self._tokens


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------


class Governor:
    """Rate-limit governor for a single adapter.

    Maintains a token bucket per adapter based on the declared SourcePolicy.rate_limit.
    Tracks daily quota consumption and persists state to disk.

    Args:
        adapter_name: Name of the adapter this governor manages.
        rate_limit: Declared rate limit from the adapter's SourcePolicy.
        clock: Injectable clock for testing.
        state_dir: Directory to persist quota state. None to disable persistence.
    """

    def __init__(
        self,
        adapter_name: str,
        rate_limit: RateLimit,
        clock: Clock | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.adapter_name = adapter_name
        self.rate_limit = rate_limit
        self._clock = clock or RealClock()
        self._state_dir = state_dir

        # Token bucket: rate = requests / per_seconds
        rate = rate_limit.requests / rate_limit.per_seconds
        self._bucket = TokenBucket(
            rate=rate,
            capacity=rate_limit.burst,
            clock=self._clock,
        )

        # Daily quota tracking
        self._daily_count = 0
        self._daily_date: str = ""

        # Load persisted state if available
        if state_dir:
            self._load_state()

    def acquire(self) -> float:
        """Acquire permission to make one request.

        Blocks until the rate limit allows. Raises QuotaExhaustedError
        if daily cap is reached.

        Returns:
            Time waited in seconds.
        """
        # Check daily cap
        self._check_daily_cap()

        # Acquire from token bucket
        waited = self._bucket.acquire(1)

        # Track daily usage
        self._daily_count += 1

        # Persist state
        self._save_state()

        if waited > 0:
            logger.debug(
                "Governor[%s]: waited %.2fs for rate limit",
                self.adapter_name,
                waited,
            )

        return waited

    def handle_rate_limit_response(
        self,
        retry_after: float | None = None,
        remaining: int | None = None,
        reset_at: float | None = None,
        backoff: int | None = None,
    ) -> float:
        """Adjust governor state based on API response headers.

        Handles: Retry-After, x-ratelimit-remaining, x-ratelimit-reset,
        and Stack Exchange's backoff field.

        Args:
            retry_after: Seconds to wait (from Retry-After header).
            remaining: Remaining requests (from x-ratelimit-remaining).
            reset_at: Unix timestamp when limit resets (from x-ratelimit-reset).
            backoff: Seconds to wait (from Stack Exchange backoff field).

        Returns:
            Time waited in seconds (if any immediate sleep was needed).
        """
        waited = 0.0

        # Stack Exchange backoff field takes priority
        if backoff is not None and backoff > 0:
            logger.info(
                "Governor[%s]: API backoff=%ds, sleeping",
                self.adapter_name,
                backoff,
            )
            self._clock.sleep(backoff)
            waited += backoff

        # Retry-After header
        if retry_after is not None and retry_after > 0:
            logger.info(
                "Governor[%s]: Retry-After=%.1fs, sleeping",
                self.adapter_name,
                retry_after,
            )
            self._clock.sleep(retry_after)
            waited += retry_after

        # Adjust bucket based on remaining/reset headers
        if remaining is not None and remaining == 0 and reset_at is not None:
            now = self._clock.now()
            sleep_time = max(0.0, reset_at - now)
            if sleep_time > 0:
                if sleep_time > 3600.0:
                    raise QuotaExhaustedError(
                        f"Rate limit reset for '{self.adapter_name}' is in {sleep_time:.0f}s "
                        f"(exceeds 1h max wait)."
                    )
                logger.info(
                    "Governor[%s]: quota exhausted, sleeping %.1fs until reset",
                    self.adapter_name,
                    sleep_time,
                )
                self._clock.sleep(sleep_time)
                waited += sleep_time

        return waited

    @property
    def daily_count(self) -> int:
        """Number of requests made today."""
        return self._daily_count

    def _check_daily_cap(self) -> None:
        """Check if daily cap has been reached."""
        import datetime

        today = datetime.datetime.now(datetime.UTC).date().isoformat()

        # Reset counter if it's a new day
        if self._daily_date != today:
            self._daily_date = today
            self._daily_count = 0

        if self.rate_limit.daily_cap is not None and self._daily_count >= self.rate_limit.daily_cap:
            raise QuotaExhaustedError(
                f"Daily cap of {self.rate_limit.daily_cap} requests reached "
                f"for adapter '{self.adapter_name}'"
            )

    def _state_path(self) -> Path | None:
        if self._state_dir is None:
            return None
        self._state_dir.mkdir(parents=True, exist_ok=True)
        return self._state_dir / f"{self.adapter_name}_quota.json"

    def _save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        state = {
            "adapter": self.adapter_name,
            "daily_date": self._daily_date,
            "daily_count": self._daily_count,
        }
        path.write_text(json.dumps(state), encoding="utf-8")

    def _load_state(self) -> None:
        path = self._state_path()
        if path is None or not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._daily_date = state.get("daily_date", "")
            self._daily_count = state.get("daily_count", 0)
            logger.info(
                "Governor[%s]: loaded persisted state (date=%s, count=%d)",
                self.adapter_name,
                self._daily_date,
                self._daily_count,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                "Governor[%s]: failed to load state: %s",
                self.adapter_name,
                e,
            )


class QuotaExhaustedError(Exception):
    """Raised when an adapter's daily quota has been exhausted."""
