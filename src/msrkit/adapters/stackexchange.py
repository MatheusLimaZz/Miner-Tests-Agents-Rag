"""Stack Exchange adapter.

Uses the /search/advanced endpoint with gzip encoding.
Honors the 'backoff' field from responses.
Content is CC BY-SA — redistribution with attribution.

Endpoint:
    GET https://api.stackexchange.com/2.3/search/advanced
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

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

_BASE_URL = "https://api.stackexchange.com/2.3"


@register
class StackExchangeAdapter(BaseAdapter):
    """Stack Exchange adapter using the search/advanced API."""

    name: ClassVar[str] = "stackexchange"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=["STACKEXCHANGE_KEY"],
        rate_limit=RateLimit(
            requests=1,
            per_seconds=60,
            burst=1,
            daily_cap=300,  # sem chave; com chave é 10.000
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=25,  # Sem chave, limitado à página 25
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution="full_text_with_attribution",
        tos_url="https://stackoverflow.com/legal/terms-of-service",
        docs_url="https://api.stackexchange.com/docs",
        notes=(
            "Requires Accept-Encoding: gzip. Honor 'backoff' field from response. "
            "Without STACKEXCHANGE_KEY, daily quota is very low (~300 requests)."
        ),
    )

    def _build_client(self) -> httpx.Client:
        """Build client with required gzip Accept-Encoding."""
        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "msrkit/0.1 (academic research tool)",
                "Accept-Encoding": "gzip",
            },
        )

    def available(self) -> Availability:
        """Check for optional STACKEXCHANGE_KEY."""
        key = self._env("STACKEXCHANGE_KEY")
        if not key:
            return Availability(
                status=AvailabilityStatus.DEGRADED,
                reason=(
                    "STACKEXCHANGE_KEY not set. Daily quota severely limited (~300 "
                    "requests, page limit 25). Set key to increase to ~10,000."
                ),
                missing_env=["STACKEXCHANGE_KEY"],
            )
        return Availability(
            status=AvailabilityStatus.OK,
            reason="Stack Exchange API key configured. ~10,000 daily quota.",
        )

    def estimate(self, q: Query) -> int | None:
        """Use 'total' field from the response when available."""
        sites = q.extra.get("sites", ["stackoverflow"])
        if not sites:
            return None

        # Estimar usando o primeiro site
        params = self._build_params(q, site=sites[0], page=1, pagesize=1)
        params["filter"] = "total"
        resp = self._governed_get(f"{_BASE_URL}/search/advanced", params=params)

        if resp.status_code != 200:
            return None

        data = resp.json()
        self._handle_backoff(data)
        return data.get("total")

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search across configured Stack Exchange sites."""
        sites = q.extra.get("sites", ["stackoverflow"])
        limit = q.limit or 5000
        total_yielded = 0

        for site in sites:
            if total_yielded >= limit:
                break
            page = 1
            max_pages = self.policy.max_pages or 25

            while page <= max_pages and total_yielded < limit:
                params = self._build_params(
                    q, site=site, page=page, pagesize=self.policy.max_page_size
                )
                resp = self._governed_get(
                    f"{_BASE_URL}/search/advanced", params=params
                )

                if resp.status_code != 200:
                    logger.warning(
                        "SE search returned %d for site=%s page=%d",
                        resp.status_code,
                        site,
                        page,
                    )
                    break

                data = resp.json()
                self._handle_backoff(data)

                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    if total_yielded >= limit:
                        return
                    yield self._make_raw_item(
                        source=self.name,
                        native_id=str(item.get("question_id", "")),
                        payload={**item, "_site": site},
                    )
                    total_yielded += 1

                if not data.get("has_more", False):
                    break
                page += 1

    def normalize(self, raw: RawItem) -> Item:
        """Convert SE question to canonical Item."""
        p = raw.payload
        site = p.get("_site", "stackoverflow")

        created_at = None
        if p.get("creation_date"):
            created_at = datetime.fromtimestamp(p["creation_date"], tz=UTC)

        updated_at = None
        if p.get("last_activity_date"):
            updated_at = datetime.fromtimestamp(
                p["last_activity_date"], tz=UTC
            )

        tags = p.get("tags", [])
        url = p.get("link", f"https://{site}.com/q/{p.get('question_id', '')}")

        matched = match_terms(
            [], title=p.get("title"), body=p.get("body"), tags=tags
        )

        return Item(
            id=Item.make_id(self.name, str(p.get("question_id", ""))),
            source=self.name,
            kind=ItemKind.THREAD,
            url=url,  # type: ignore[arg-type]
            title=p.get("title"),
            body=p.get("body"),
            author_handle=p.get("owner", {}).get("display_name"),
            created_at=created_at,
            updated_at=updated_at,
            engagement=Engagement(
                votes=p.get("score"),
                views=p.get("view_count"),
                comments=p.get("answer_count"),
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

    def _build_params(
        self,
        q: Query,
        site: str,
        page: int,
        pagesize: int,
    ) -> dict[str, Any]:
        """Build Stack Exchange search parameters."""
        params: dict[str, Any] = {
            "q": " ".join(q.terms),
            "site": site,
            "page": page,
            "pagesize": pagesize,
            "sort": "creation",
            "order": "desc",
            "filter": "withbody",
        }

        # Date filter (epoch seconds)
        if q.since:
            params["fromdate"] = int(
                datetime.combine(q.since, datetime.min.time(), UTC).timestamp()
            )
        if q.until:
            params["todate"] = int(
                datetime.combine(
                    q.until, datetime.max.time().replace(microsecond=0), UTC
                ).timestamp()
            )

        # Tagged filter
        tagged = q.extra.get("tagged", [])
        if tagged:
            params["tagged"] = ";".join(tagged)

        # API key
        key = self._env("STACKEXCHANGE_KEY")
        if key:
            params["key"] = key

        return params

    def _handle_backoff(self, data: dict[str, Any]) -> None:
        """Honor the backoff field from SE responses."""
        backoff = data.get("backoff")
        if backoff and self._governor:
            logger.info("SE backoff=%d seconds", backoff)
            self._governor.handle_rate_limit_response(backoff=int(backoff))
