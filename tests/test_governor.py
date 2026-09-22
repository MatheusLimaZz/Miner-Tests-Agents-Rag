"""Tests for the rate-limit governor with simulated clock."""

from __future__ import annotations

from pathlib import Path

import pytest

from msrkit.governor import Governor, QuotaExhaustedError, TokenBucket
from msrkit.models import RateLimit


class FakeClock:
    """Deterministic clock for testing — never sleeps for real."""

    def __init__(self, start: float = 0.0) -> None:
        self._time = start
        self.total_slept = 0.0

    def now(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self.total_slept += seconds
        self._time += seconds

    def advance(self, seconds: float) -> None:
        """Advance time without recording as sleep."""
        self._time += seconds


class TestTokenBucket:
    """Tests for the TokenBucket rate limiter."""

    def test_initial_capacity(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate=1.0, capacity=5, clock=clock)
        assert bucket.available >= 4.99

    def test_acquire_consumes_tokens(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate=1.0, capacity=5, clock=clock)
        bucket.acquire(3)
        assert bucket.available < 3

    def test_acquire_waits_when_empty(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate=1.0, capacity=1, clock=clock)
        bucket.acquire(1)  # Empties the bucket
        waited = bucket.acquire(1)  # Must wait for refill
        assert waited > 0
        assert clock.total_slept > 0

    def test_refill_over_time(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate=10.0, capacity=10, clock=clock)
        bucket.acquire(10)  # Drain all
        clock.advance(1.0)  # Simulate 1 second passing
        assert bucket.available >= 9.9


class TestGovernor:
    """Tests for the Governor."""

    def test_acquire_never_exceeds_rate(self) -> None:
        """Verify that N acquires over time respects the declared rate."""
        clock = FakeClock()
        rate_limit = RateLimit(requests=10, per_seconds=10, burst=2)
        gov = Governor("test", rate_limit, clock=clock)

        # Make 10 requests — should take about 10 seconds at 1 req/s
        for _ in range(10):
            gov.acquire()

        # Total time should be at least (10 - burst) / rate seconds
        # With burst=2, first 2 are immediate, then 8 more at 1/s
        assert clock.total_slept >= 7.0  # Allow some margin

    def test_daily_cap_enforced(self) -> None:
        """Daily cap raises QuotaExhaustedError."""
        clock = FakeClock()
        rate_limit = RateLimit(requests=100, per_seconds=1, burst=100, daily_cap=5)
        gov = Governor("test", rate_limit, clock=clock)

        for _ in range(5):
            gov.acquire()

        with pytest.raises(QuotaExhaustedError):
            gov.acquire()

    def test_daily_count_tracks(self) -> None:
        clock = FakeClock()
        rate_limit = RateLimit(requests=100, per_seconds=1, burst=100)
        gov = Governor("test", rate_limit, clock=clock)

        gov.acquire()
        gov.acquire()
        assert gov.daily_count == 2

    def test_handle_retry_after(self) -> None:
        """Governor sleeps for Retry-After duration."""
        clock = FakeClock()
        rate_limit = RateLimit(requests=10, per_seconds=60, burst=10)
        gov = Governor("test", rate_limit, clock=clock)

        waited = gov.handle_rate_limit_response(retry_after=5.0)
        assert waited == 5.0
        assert clock.total_slept == 5.0

    def test_handle_backoff(self) -> None:
        """Governor sleeps for Stack Exchange backoff field."""
        clock = FakeClock()
        rate_limit = RateLimit(requests=10, per_seconds=60, burst=10)
        gov = Governor("test", rate_limit, clock=clock)

        waited = gov.handle_rate_limit_response(backoff=3)
        assert waited == 3.0

    def test_state_persistence(self, tmp_path: Path) -> None:
        """Governor persists and restores daily state."""
        clock = FakeClock()
        rate_limit = RateLimit(requests=100, per_seconds=1, burst=100)

        # Create governor and make some requests
        gov1 = Governor("test", rate_limit, clock=clock, state_dir=tmp_path)
        gov1.acquire()
        gov1.acquire()
        gov1.acquire()
        assert gov1.daily_count == 3

        # Create new governor from same state dir
        gov2 = Governor("test", rate_limit, clock=clock, state_dir=tmp_path)
        assert gov2.daily_count == 3

    def test_handle_reset_at(self) -> None:
        """Governor sleeps until reset_at when remaining is 0."""
        clock = FakeClock(start=1000.0)
        rate_limit = RateLimit(requests=10, per_seconds=60, burst=10)
        gov = Governor("test", rate_limit, clock=clock)

        waited = gov.handle_rate_limit_response(remaining=0, reset_at=1045.0)
        assert waited == 45.0
        assert clock.total_slept == 45.0

    def test_handle_reset_at_excessive_raises_quota_exhausted(self) -> None:
        """Excessive reset_at wait (>3600s) raises QuotaExhaustedError to avoid freezing."""
        clock = FakeClock(start=1000.0)
        rate_limit = RateLimit(requests=10, per_seconds=60, burst=10)
        gov = Governor("test", rate_limit, clock=clock)

        with pytest.raises(QuotaExhaustedError):
            gov.handle_rate_limit_response(remaining=0, reset_at=5000.0)

    def test_real_clock_returns_unix_timestamp(self) -> None:
        """RealClock returns Unix timestamp (> 1.7e9), not monotonic uptime."""
        from msrkit.governor import RealClock

        rc = RealClock()
        t = rc.now()
        assert t > 1_700_000_000.0
