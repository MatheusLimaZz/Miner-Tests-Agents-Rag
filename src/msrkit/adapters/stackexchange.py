"""Stack Exchange adapter.

Uses the /search/advanced endpoint with gzip encoding.
Honors the 'backoff' field from responses.
Content is CC BY-SA — redistribution with attribution.

Endpoint:
    GET https://api.stackexchange.com/2.3/search/advanced
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    RedistributionPolicy,
    SourcePolicy,
    TechContext,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.stackexchange.com/2.3"
_MAX_ANSWER_PAGES = 10  # per batch of 100 questions


@register
class StackExchangeAdapter(BaseAdapter):
    """Stack Exchange adapter using the search/advanced API."""

    name: ClassVar[str] = "stackexchange"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=["STACKEXCHANGE_KEY"],
        rate_limit=RateLimit(
            requests=30,
            per_seconds=60,
            burst=5,
            daily_cap=300,  # sem chave; com chave é 10.000
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=25,  # Sem chave, limitado à página 25
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution=RedistributionPolicy.FULL_TEXT_WITH_ATTRIBUTION,
        supports_raw_queries=True,
        tos_url="https://stackoverflow.com/legal/terms-of-service",
        docs_url="https://api.stackexchange.com/docs",
        notes=(
            "Requires Accept-Encoding: gzip. Honor 'backoff' field from response. "
            "Without STACKEXCHANGE_KEY, daily quota is very low (~300 requests)."
        ),
        has_official_api=True,
        official_api_name="StackExchange API v2.3",
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
        total = data.get("total")
        return int(total) if total is not None else None

    def population(self, q: Query) -> int | None:
        """'total' summed over every configured site (estimate() reads the first one)."""
        total = 0
        for site in q.extra.get("sites", ["stackoverflow"]):
            params = self._build_params(q, site=site, page=1, pagesize=1)
            params["filter"] = "total"
            resp = self._governed_get(f"{_BASE_URL}/search/advanced", params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            self._handle_backoff(data)
            if data.get("total") is None:
                return None
            total += int(data["total"])
        return total

    def partition(self, q: Query) -> list[Query]:
        """Split multi-term queries so each term runs as its own query."""
        from msrkit.partition import split_by_term

        return split_by_term(q)

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search across configured Stack Exchange sites."""
        sites = q.extra.get("sites", ["stackoverflow"])
        limit = 5000 if q.limit is None else q.limit
        total_yielded = 0

        # One request per (site, term, tag filter); see tag_filters (ADR-017, ADR-042).
        terms_to_search: list[str | None] = list(q.terms) or [None]
        combos = [(t, tag) for t in terms_to_search for tag in self.tag_filters(q)]

        seen_ids: set[str] = set()

        for site in sites:
            for term, tagged in combos:
                if total_yielded >= limit:
                    return
                page = 1
                max_pages = self.policy.max_pages or 25

                while page <= max_pages and total_yielded < limit:
                    page_size = min(self.policy.max_page_size, limit - total_yielded)
                    params = self._build_params(
                        q, site=site, page=page, pagesize=page_size, term=term, tagged=tagged
                    )
                    resp = self._governed_get(f"{_BASE_URL}/search/advanced", params=params)

                    if resp.status_code != 200:
                        logger.warning(
                            "SE search returned %d for site=%s page=%d: %s",
                            resp.status_code,
                            site,
                            page,
                            resp.text[:200],
                        )
                        break

                    data = resp.json()
                    self._handle_backoff(data)

                    items = data.get("items", [])
                    if not items:
                        break

                    answers_by_qid: dict[str, list[dict[str, Any]]] = {}
                    if q.extra.get("include_answers", True):
                        ids = [
                            str(it["question_id"])
                            for it in items
                            if it.get("question_id") and it.get("answer_count", 1)
                        ]
                        answers_by_qid = self._fetch_answers(site, ids)

                    for item in items:
                        if total_yielded >= limit:
                            return
                        qid = str(item.get("question_id", ""))
                        seen_key = f"{site}:{qid}"
                        if not qid or seen_key in seen_ids:
                            continue
                        seen_ids.add(seen_key)
                        yield self._make_raw_item(
                            source=self.name,
                            native_id=qid,
                            payload={
                                **item,
                                "_site": site,
                                "_answers": answers_by_qid.get(qid, []),
                            },
                        )
                        total_yielded += 1

                    if not data.get("has_more", False):
                        break
                    page += 1

    @classmethod
    def effective_rate_limit(cls) -> RateLimit:
        """Daily quota is 300 requests without a key and 10,000 with one."""
        base = cls.policy.rate_limit
        if cls._env("STACKEXCHANGE_KEY"):
            return base.model_copy(update={"daily_cap": 10_000})
        return base

    def _fetch_answers(self, site: str, question_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Fetch answers (with body) for up to 100 questions per request.

        The unit of analysis is the thread (question + answers); answers often
        carry the actual testing practice (Protocol E2 v2, §3.5).
        """
        by_qid: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(question_ids), 100):
            batch = ";".join(question_ids[start : start + 100])
            page = 1
            while page <= _MAX_ANSWER_PAGES:
                params: dict[str, Any] = {
                    "site": site,
                    "page": page,
                    "pagesize": 100,
                    "sort": "votes",
                    "order": "desc",
                    "filter": "withbody",
                }
                key = self._env("STACKEXCHANGE_KEY")
                if key:
                    params["key"] = key
                resp = self._governed_get(f"{_BASE_URL}/questions/{batch}/answers", params=params)
                if resp.status_code != 200:
                    logger.warning(
                        "SE answers returned %d for site=%s: %s",
                        resp.status_code,
                        site,
                        resp.text[:200],
                    )
                    break
                data = resp.json()
                self._handle_backoff(data)
                for ans in data.get("items", []):
                    by_qid.setdefault(str(ans.get("question_id", "")), []).append(
                        {
                            "answer_id": ans.get("answer_id"),
                            "body": ans.get("body") or "",
                            "score": ans.get("score"),
                            "is_accepted": bool(ans.get("is_accepted")),
                        }
                    )
                if not data.get("has_more", False):
                    break
                page += 1
        return by_qid

    @staticmethod
    def _thread_body(question_body: str | None, answers: list[dict[str, Any]]) -> str | None:
        """Question body followed by its answers, each with a labelled header."""
        if not answers:
            return question_body
        parts = [question_body or ""]
        for ans in answers:
            label = "Resposta aceita" if ans.get("is_accepted") else "Resposta"
            parts.append(f"--- {label} (score {ans.get('score')}) ---\n{ans.get('body', '')}")
        return "\n\n".join(parts)

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert SE question to canonical Item."""
        p = raw.payload
        site = p.get("_site", "stackoverflow")

        created_at = None
        if p.get("creation_date"):
            created_at = datetime.fromtimestamp(p["creation_date"], tz=UTC)

        updated_at = None
        if p.get("last_activity_date"):
            updated_at = datetime.fromtimestamp(p["last_activity_date"], tz=UTC)

        tags = p.get("tags", [])
        url = p.get("link", f"https://{site}.com/q/{p.get('question_id', '')}")

        body = self._thread_body(p.get("body"), p.get("_answers") or [])
        matched = match_terms(terms or [], title=p.get("title"), body=body, tags=tags)

        qid = str(p.get("question_id") or raw.native_id or "")
        native_key = f"{site}:{qid}" if site != "stackoverflow" else qid

        return Item(
            id=Item.make_id(self.name, native_key),
            source=self.name,
            kind=ItemKind.THREAD,
            url=url,
            title=p.get("title"),
            body=body,
            author_handle=(p.get("owner") or {}).get("display_name"),
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
        term: str | None = None,
        tagged: str | None | object = ...,
    ) -> dict[str, Any]:
        """Build Stack Exchange search parameters.

        `tagged` is one entry of tag_filters(q); left out, the first one is used.
        """
        query_text = term if term is not None else (" ".join(q.terms) if q.terms else "")
        params: dict[str, Any] = {
            "site": site,
            "page": page,
            "pagesize": pagesize,
            "sort": "creation",
            "order": "desc",
            "filter": "withbody",
        }
        if query_text:
            params["q"] = query_text

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

        if tagged is ...:
            tagged = self.tag_filters(q)[0]
        if isinstance(tagged, str) and tagged:
            params["tagged"] = tagged

        # API key
        key = self._env("STACKEXCHANGE_KEY")
        if key:
            params["key"] = key

        return params

    @staticmethod
    def tag_filters(q: Query) -> list[str | None]:
        """Values of `tagged` to search with, by `extra.tagged_mode`.

        - `all` (default): the whole list in one request. The docs describe the
          list as "at least one will be present", but the first test collection
          (Sept/2026) got 0 items in 180 requests with five tags, which is what
          an AND would give (ADR-042).
        - `any`: one request per tag, an explicit OR (costs one request per tag).
        - `off`: no tag filter; the terms alone narrow the search.
        """
        tags = q.extra.get("tagged", [])
        if isinstance(tags, str):
            tags = [t for t in tags.split(";") if t]
        mode = q.extra.get("tagged_mode", "all")
        if mode not in ("all", "any", "off"):
            raise ValueError(f"tagged_mode must be all, any or off, got {mode!r}")
        if not tags or mode == "off":
            return [None]
        if mode == "any":
            return list(tags)
        return [";".join(tags)]

    def _handle_backoff(self, data: dict[str, Any]) -> None:
        """Honor the backoff field from SE responses."""
        backoff = data.get("backoff")
        if backoff and self._governor:
            logger.info("SE backoff=%d seconds", backoff)
            self._governor.handle_rate_limit_response(backoff=int(backoff))
