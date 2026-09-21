"""X / Twitter adapter — GATED.

Read/search access requires a paid API plan. This adapter returns
UNSUPPORTED when X_BEARER_TOKEN is absent, and DEGRADED when present
(since the accessible historical window depends on the plan tier).

Does not fix plan numbers or prices — these change frequently.
Check docs/api-notes.md for the latest assessment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

from msrkit.adapters.base import BaseAdapter
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    Query,
    RateLimit,
    RawItem,
    SourcePolicy,
    SourceUnsupportedError,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)


@register
class XTwitterAdapter(BaseAdapter):
    """X/Twitter adapter — gated behind paid API access."""

    name: ClassVar[str] = "x_twitter"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=["X_BEARER_TOKEN"],
        rate_limit=RateLimit(
            requests=1,
            per_seconds=60,
            burst=1,
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=None,
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution="metadata_only",
        tos_url="https://developer.x.com/en/developer-terms/agreement-and-policy",
        docs_url="https://developer.x.com/en/docs/twitter-api",
        notes=(
            "Read/search access requires a paid plan. The free tier does not "
            "include search. Do not fix plan numbers or prices in code — "
            "they change frequently. See docs/api-notes.md."
        ),
    )

    def available(self) -> Availability:
        """Check for X_BEARER_TOKEN."""
        token = self._env("X_BEARER_TOKEN")
        if not token:
            return Availability(
                status=AvailabilityStatus.UNSUPPORTED,
                reason=(
                    "X_BEARER_TOKEN not set. The X/Twitter API requires a paid "
                    "plan for search/read access. The free tier does not include "
                    "search endpoints. See docs/api-notes.md for current pricing."
                ),
                missing_env=["X_BEARER_TOKEN"],
            )
        return Availability(
            status=AvailabilityStatus.DEGRADED,
            reason=(
                "X_BEARER_TOKEN configured. Historical search window depends on "
                "the contracted plan tier. Check the X API documentation for "
                "current capabilities and record in docs/api-notes.md."
            ),
        )

    def estimate(self, q: Query) -> int | None:
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        avail = self.available()
        if avail.status == AvailabilityStatus.UNSUPPORTED:
            raise SourceUnsupportedError(self.name, avail.reason)
        # Stub: yield nothing for now
        return iter([])

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        raise NotImplementedError("X/Twitter normalization not implemented in v0.")
