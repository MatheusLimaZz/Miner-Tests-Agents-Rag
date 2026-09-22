"""Hacker News adapter via Algolia API.

High-value source with real full-text search, no auth required,
and native date filtering.

Endpoints:
    GET https://hn.algolia.com/api/v1/search
    GET https://hn.algolia.com/api/v1/search_by_date
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

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

_BASE_URL = "https://hn.algolia.com/api/v1"


@register
class HackerNewsAdapter(BaseAdapter):
    """Hacker News adapter using the Algolia search API."""

    name: ClassVar[str] = "hackernews"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=[],
        rate_limit=RateLimit(
            requests=10,
            per_seconds=60,
            burst=5,
        ),
        max_results_per_query=None,  # Algolia has no hard cap like GitHub
        max_page_size=1000,
        max_pages=None,
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution="metadata_only",
        tos_url="https://hn.algolia.com/api",
        docs_url="https://hn.algolia.com/api",
        notes="Free, no auth. Full-text search with date filters via numericFilters.",
    )

    def available(self) -> Availability:
        """HN Algolia API requires no authentication."""
        return Availability(
            status=AvailabilityStatus.OK,
            reason="Hacker News Algolia API is publicly accessible, no auth required.",
        )

    def estimate(self, q: Query) -> int | None:
        """Estimate results using the nbHits field."""
        if not q.terms:
            params = self._build_params(q, page=0, hits_per_page=1)
            resp = self._governed_get(f"{_BASE_URL}/search", params=params)
            if resp.status_code != 200:
                return None
            return resp.json().get("nbHits")

        total = 0
        for term in q.terms:
            params = self._build_params(q, term=term, page=0, hits_per_page=1)
            resp = self._governed_get(f"{_BASE_URL}/search", params=params)
            if resp.status_code != 200:
                return None
            total += resp.json().get("nbHits", 0)
        return total

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search HN via Algolia, paginating through all results."""
        limit = q.limit or 5000
        total_yielded = 0
        seen_ids: set[str] = set()

        terms: list[str | None] = list(q.terms) if q.terms else [None]
        for term in terms:
            page = 0
            hits_per_page = min(self.policy.max_page_size, 1000)

            while True:
                if total_yielded >= limit:
                    return

                params = self._build_params(q, term=term, page=page, hits_per_page=hits_per_page)
                resp = self._governed_get(f"{_BASE_URL}/search", params=params)

                if resp.status_code != 200:
                    logger.warning(
                        "HN search returned %d for term '%s' page %d",
                        resp.status_code,
                        term,
                        page,
                    )
                    break

                data = resp.json()
                hits = data.get("hits", [])
                if not hits:
                    break

                for hit in hits:
                    oid = str(hit.get("objectID", ""))
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)

                    if total_yielded >= limit:
                        return
                    yield self._make_raw_item(
                        source=self.name,
                        native_id=oid,
                        payload=hit,
                    )
                    total_yielded += 1

                # Check if there are more pages
                nb_pages = data.get("nbPages", 0)
                page += 1
                if page >= nb_pages:
                    break

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert HN Algolia hit to canonical Item."""
        p = raw.payload
        tags = p.get("_tags", [])
        kind = ItemKind.COMMENT if tags and "comment" in tags else ItemKind.THREAD

        title = p.get("title") or p.get("story_title")
        body = p.get("comment_text") or p.get("story_text")

        # Build URL
        url = p.get("url") or f"https://news.ycombinator.com/item?id={p.get('objectID', '')}"

        created_at = None
        created_at_i = p.get("created_at_i")
        if created_at_i:
            created_at = datetime.fromtimestamp(created_at_i, tz=UTC)

        matched = match_terms(terms or [], title=title, body=body)

        return Item(
            id=Item.make_id(self.name, str(p.get("objectID", ""))),
            source=self.name,
            kind=kind,
            url=url,  # type: ignore[arg-type]
            title=title,
            body=body,
            author_handle=p.get("author"),
            created_at=created_at,
            engagement=Engagement(
                votes=p.get("points"),
                comments=p.get("num_comments"),
            ),
            tech=TechContext(tags=p.get("_tags", [])),
            matched_terms=matched,
            provenance=Provenance(
                run_id="",  # Set by the runner
                query_string="",
                partition="",
                adapter=self.name,
                adapter_version=self.version,
                fetched_at=raw.fetched_at,
                response_sha256="",
                raw_ref="",
            ),
        )

    def _build_params(
        self,
        q: Query,
        page: int = 0,
        hits_per_page: int = 1000,
        term: str | None = None,
    ) -> dict[str, Any]:
        query_str = term if term is not None else (" OR ".join(q.terms) if q.terms else "")

        params: dict[str, Any] = {
            "query": query_str,
            "page": page,
            "hitsPerPage": hits_per_page,
            "tags": "story",  # Default to stories
        }

        # Date filter via numericFilters
        filters: list[str] = []
        if q.since:
            since_ts = int(datetime.combine(q.since, datetime.min.time(), UTC).timestamp())
            filters.append(f"created_at_i>={since_ts}")
        if q.until:
            until_dt = datetime.combine(q.until, datetime.max.time().replace(microsecond=0), UTC)
            until_ts = int(until_dt.timestamp())
            filters.append(f"created_at_i<={until_ts}")

        if filters:
            params["numericFilters"] = ",".join(filters)

        return params
