"""dev.to (Forem) adapter.

The public API does NOT offer full-text search. Strategy:
collect by tag, then filter locally by terms.
policy.supports_full_text_search = False.

Endpoints:
    GET https://dev.to/api/articles?tag=&page=&per_page=
    GET https://dev.to/api/articles/{id}  (for body_markdown)
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
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

_BASE_URL = "https://dev.to/api"


@register
class DevToAdapter(BaseAdapter):
    """dev.to adapter using the Forem public API."""

    name: ClassVar[str] = "devto"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=[],
        rate_limit=RateLimit(
            requests=30,
            per_seconds=60,
            burst=10,
        ),
        max_results_per_query=None,
        max_page_size=1000,
        max_pages=None,
        supports_full_text_search=False,
        supports_date_filter=False,
        redistribution="metadata_only",
        tos_url="https://dev.to/terms",
        docs_url="https://developers.forem.com/api/v1",
        notes=(
            "No full-text search API. Collection by tag, then local term filtering. "
            "The manifest must record that selection was performed locally."
        ),
    )

    def available(self) -> Availability:
        """dev.to API is publicly accessible."""
        return Availability(
            status=AvailabilityStatus.OK,
            reason="dev.to public API requires no authentication.",
        )

    def estimate(self, q: Query) -> int | None:
        """dev.to API does not provide result counts."""
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        """Collect articles by tag, paginating through results.

        Since dev.to has no full-text search, we collect by each tag
        in the protocol config and yield all articles.
        """
        tags = q.extra.get("tags", ["rag", "llm", "ai", "agents", "testing"])
        limit = q.limit or 5000
        total_yielded = 0
        seen_ids: set[str] = set()

        for tag in tags:
            if total_yielded >= limit:
                break
            page = 1
            per_page = min(self.policy.max_page_size, 1000)

            while total_yielded < limit:
                params: dict[str, Any] = {
                    "tag": tag,
                    "page": page,
                    "per_page": per_page,
                }

                resp = self._governed_get(f"{_BASE_URL}/articles", params=params)

                if resp.status_code != 200:
                    logger.warning(
                        "dev.to articles returned %d for tag=%s page=%d",
                        resp.status_code,
                        tag,
                        page,
                    )
                    break

                articles = resp.json()
                if not articles:
                    break

                hit_older_than_since = False
                for article in articles:
                    if total_yielded >= limit:
                        return

                    art_id = str(article.get("id", ""))
                    if not art_id or art_id in seen_ids:
                        continue

                    pub_str = article.get("published_at") or article.get("created_at")
                    if pub_str and (q.since or q.until):
                        try:
                            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                            pub_date = pub_dt.date()
                            if q.until and pub_date > q.until:
                                continue
                            if q.since and pub_date < q.since:
                                hit_older_than_since = True
                                break
                        except (ValueError, TypeError):
                            pass

                    if q.terms:
                        art_title = article.get("title") or ""
                        art_desc = article.get("description") or ""
                        if not match_terms(q.terms, title=art_title, body=art_desc):
                            continue

                    seen_ids.add(art_id)
                    yield self._make_raw_item(
                        source=self.name,
                        native_id=art_id,
                        payload=article,
                    )
                    total_yielded += 1

                if hit_older_than_since:
                    break

                # dev.to returns empty list when no more pages
                if len(articles) < per_page:
                    break
                page += 1

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert dev.to article to canonical Item."""
        p = raw.payload

        created_at = None
        if p.get("published_at"):
            with contextlib.suppress(ValueError, TypeError):
                created_at = datetime.fromisoformat(p["published_at"].replace("Z", "+00:00"))

        tags = p.get("tag_list", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        matched = match_terms(
            terms or [], title=p.get("title"), body=p.get("description"), tags=tags
        )

        return Item(
            id=Item.make_id(self.name, str(p.get("id", ""))),
            source=self.name,
            kind=ItemKind.ARTICLE,
            url=p.get("url", ""),  # type: ignore[arg-type]
            title=p.get("title"),
            body=p.get("description"),  # metadata_only: don't store full body
            author_handle=p.get("user", {}).get("username"),
            created_at=created_at,
            engagement=Engagement(
                reactions=p.get("public_reactions_count"),
                comments=p.get("comments_count"),
            ),
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
