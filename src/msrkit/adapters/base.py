"""Base adapter: shared HTTP client, pagination, raw recording, and hashing.

Concrete adapters inherit from BaseAdapter and only implement
source-specific logic. The base provides:
- httpx client with configurable timeouts
- Integration with the Governor rate limiter
- Generic pagination helpers
- Raw response recording
- Response hash computation
"""

from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from msrkit.governor import Governor
from msrkit.models import (
    Availability,
    Item,
    Query,
    RawItem,
    SourcePolicy,
)

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract base class for all source adapters.

    Class attributes (must be set by subclasses):
        name: Unique adapter identifier.
        version: Adapter version string.
        policy: Declared SourcePolicy for this source.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    policy: ClassVar[SourcePolicy]

    def __init__(self, governor: Governor | None = None) -> None:
        self._governor = governor
        self._client: httpx.Client | None = None
        self._request_count = 0

    @property
    def client(self) -> httpx.Client:
        """Lazy-initialized httpx client with default timeout."""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> httpx.Client:
        """Build the httpx client. Override for custom headers/auth."""
        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "msrkit/0.1 (academic research tool)"},
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # -------------------------------------------------------------------
    # Abstract methods (must be implemented by subclasses)
    # -------------------------------------------------------------------

    @abstractmethod
    def available(self) -> Availability:
        """Check if this adapter is available (credentials, etc.).

        MUST NOT make any network requests.
        """

    @abstractmethod
    def estimate(self, q: Query) -> int | None:
        """Estimate total results for a query, if the API provides this.

        Returns None if unknown.
        """

    @abstractmethod
    def search(self, q: Query) -> Iterator[RawItem]:
        """Iterate raw results for ONE partition, paging internally.

        Must use self._governed_get() for all HTTP requests.
        """

    @abstractmethod
    def normalize(self, raw: RawItem) -> Item:
        """Convert a raw API response to a canonical Item."""

    # -------------------------------------------------------------------
    # Default implementations
    # -------------------------------------------------------------------

    def partition(self, q: Query) -> list[Query]:
        """Default partition: return query unchanged.

        Subclasses with result caps should override this using
        the partition module.
        """
        return [q]

    # -------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------

    def _check_env_vars(self, env_vars: list[str]) -> tuple[bool, list[str]]:
        """Check which required environment variables are set.

        Returns:
            Tuple of (all_present, list_of_missing_vars).
        """
        missing = [v for v in env_vars if not os.environ.get(v)]
        return len(missing) == 0, missing

    def _governed_get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make a GET request, respecting the rate governor.

        Applies rate limiting before the request. On 429/403 responses,
        handles Retry-After and rate limit headers.
        """
        if self._governor:
            self._governor.acquire()

        response = self._request_with_retry(url, params=params, headers=headers)
        self._request_count += 1

        # Handle rate limit response headers
        if self._governor and response.status_code in (429, 403):
            retry_after = response.headers.get("Retry-After")
            remaining = response.headers.get("x-ratelimit-remaining")
            reset_at = response.headers.get("x-ratelimit-reset")

            self._governor.handle_rate_limit_response(
                retry_after=float(retry_after) if retry_after else None,
                remaining=int(remaining) if remaining else None,
                reset_at=float(reset_at) if reset_at else None,
            )

        return response

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential_jitter(initial=1, max=60, jitter=5),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request_with_retry(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Execute HTTP GET with exponential backoff retry."""
        return self.client.get(url, params=params, headers=headers)

    @staticmethod
    def _hash_response(content: bytes) -> str:
        """Compute SHA-256 hash of response content."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _make_raw_item(source: str, native_id: str, payload: dict[str, Any]) -> RawItem:
        """Create a RawItem with current timestamp."""
        return RawItem(
            source=source,
            native_id=str(native_id),
            payload=payload,
            fetched_at=datetime.now(UTC),
        )

    @staticmethod
    def _env(var_name: str) -> str | None:
        """Get an environment variable, returning None if empty."""
        val = os.environ.get(var_name, "")
        return val if val else None
