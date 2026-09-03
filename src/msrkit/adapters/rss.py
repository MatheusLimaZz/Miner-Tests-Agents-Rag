"""RSS/Atom feed adapter.

Generic adapter covering Medium, Substack, and engineering blogs.
Uses feedparser to parse RSS/Atom feeds.

Severe limitation: RSS feeds only return the most recent items (~10).
No historical coverage. Must be documented in manifest.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import ClassVar

from msrkit.adapters.base import BaseAdapter
from msrkit.keywords import match_terms
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Engagement,
    Item,
    ItemKind,
    Provenance,
    Query,
    RateLimit,
    RawItem,
    SourcePolicy,
    TechContext,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)


@register
class RSSAdapter(BaseAdapter):
    """RSS/Atom feed adapter for Medium, Substack, and blogs."""

    name: ClassVar[str] = "rss"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=[],
        rate_limit=RateLimit(
            requests=10,
            per_seconds=60,
            burst=5,
        ),
        max_results_per_query=None,
        max_page_size=50,
        max_pages=None,
        supports_full_text_search=False,
        supports_date_filter=False,
        redistribution="metadata_only",
        tos_url="",
        docs_url="",
        notes=(
            "RSS feeds return only the most recent items (~10). "
            "No historical coverage. Manifest must record truncated=true "
            "and historical_coverage=false."
        ),
    )

    def available(self) -> Availability:
        """RSS requires no auth."""
        return Availability(
            status=AvailabilityStatus.OK,
            reason=(
                "RSS feeds are publicly accessible. NOTE: feeds only return "
                "recent items (~10). No historical coverage possible."
            ),
        )

    def estimate(self, q: Query) -> int | None:
        """Cannot estimate RSS feed results."""
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        """Fetch and parse configured RSS feeds."""
        import feedparser

        feeds = q.extra.get("feeds", [])
        limit = q.limit or 5000
        total_yielded = 0

        for feed_url in feeds:
            if total_yielded >= limit:
                break

            logger.info("Fetching RSS feed: %s", feed_url)
            resp = self._governed_get(feed_url)

            if resp.status_code != 200:
                logger.warning("RSS feed returned %d: %s", resp.status_code, feed_url)
                continue

            parsed = feedparser.parse(resp.text)

            for entry in parsed.entries:
                if total_yielded >= limit:
                    return

                entry_id = entry.get("id", entry.get("link", ""))
                payload = {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "published": entry.get("published", ""),
                    "author": entry.get("author", ""),
                    "tags": [t.get("term", "") for t in entry.get("tags", [])],
                    "feed_url": feed_url,
                }

                yield self._make_raw_item(
                    source=self.name,
                    native_id=str(entry_id),
                    payload=payload,
                )
                total_yielded += 1

    def normalize(self, raw: RawItem) -> Item:
        """Convert RSS entry to canonical Item."""
        p = raw.payload

        created_at = None
        if p.get("published"):
            try:
                created_at = parsedate_to_datetime(p["published"])
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass

        tags = p.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        matched = match_terms(
            [], title=p.get("title"), body=p.get("summary"), tags=tags
        )

        return Item(
            id=Item.make_id(self.name, str(raw.native_id)),
            source=self.name,
            kind=ItemKind.ARTICLE,
            url=p.get("link", ""),  # type: ignore[arg-type]
            title=p.get("title"),
            body=None,  # metadata_only
            author_handle=p.get("author"),
            created_at=created_at,
            engagement=Engagement(),
            tech=TechContext(tags=tags),
            matched_terms=matched,
            provenance=Provenance(
                run_id="",
                query_string="",
                partition="",
                adapter=self.name,
                adapter_version=self.version,
                fetched_at=raw.fetched_at,
                response_sha256="",
                raw_ref="",
            ),
        )
