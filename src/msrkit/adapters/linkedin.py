"""LinkedIn adapter — PERMANENTLY UNSUPPORTED.

LinkedIn does not expose a public content-search API. Research and
analytics on third-party content are not approved use cases, and
scraping is prohibited by the platform terms.

This adapter exists ONLY to:
1. Return UNSUPPORTED with documented justification
2. Record the exclusion in the run manifest

If asked to "find a way", refuse and point to this file and
section 2.2 of the specification.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

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


@register
class LinkedInAdapter(BaseAdapter):
    """LinkedIn adapter — permanently UNSUPPORTED.

    This adapter exists only to document and record the exclusion.
    It will never perform any data collection.
    """

    name: ClassVar[str] = "linkedin"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=[],
        rate_limit=RateLimit(requests=0, per_seconds=1, burst=0),
        max_results_per_query=0,
        max_page_size=0,
        max_pages=0,
        supports_full_text_search=False,
        supports_date_filter=False,
        redistribution="metadata_only",
        tos_url="https://www.linkedin.com/legal/user-agreement",
        docs_url="https://learn.microsoft.com/en-us/linkedin/",
        notes=(
            "Permanently unsupported. LinkedIn does not expose a public "
            "content-search API. Research/analytics on third-party content "
            "is not an approved use case. Scraping is prohibited."
        ),
    )

    def available(self) -> Availability:
        return Availability(
            status=AvailabilityStatus.UNSUPPORTED,
            reason=(
                "LinkedIn does not expose a public content-search API. "
                "Research and analytics on third-party content are not approved "
                "use cases, and scraping is prohibited by the platform terms. "
                "This adapter exists only to record the documented exclusion "
                "in the run manifest."
            ),
        )

    def estimate(self, q: Query) -> int | None:
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        raise SourceUnsupportedError(
            self.name,
            "LinkedIn is permanently unsupported. See adapters/linkedin.py "
            "and specification §2.2.",
        )

    def normalize(self, raw: RawItem) -> Item:
        raise SourceUnsupportedError(
            self.name,
            "LinkedIn is permanently unsupported.",
        )
