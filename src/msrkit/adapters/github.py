"""GitHub adapter via REST API v3.

Primary source. Supports repos, code, and issues search.
Critical constraint: max 1,000 results per query (100/page × 10 pages).
Requires partitioning by date and secondary axes.

Endpoints:
    GET /search/repositories
    GET /search/code
    GET /search/issues
"""

from __future__ import annotations

import logging
from datetime import datetime
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
    SourcePolicy,
    TechContext,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"
_KIND_ENDPOINT = {
    "repo": "/search/repositories",
    "code": "/search/code",
    "issue": "/search/issues",
}


@register
class GitHubAdapter(BaseAdapter):
    """GitHub adapter using the REST search API."""

    name: ClassVar[str] = "github"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=["GITHUB_TOKEN"],
        rate_limit=RateLimit(
            requests=30,
            per_seconds=60,
            burst=5,
            daily_cap=None,
        ),
        max_results_per_query=1000,  # Critical cap: 100/page × 10 pages
        max_page_size=100,
        max_pages=10,
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution="metadata_only",
        tos_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        docs_url="https://docs.github.com/en/rest/search",
        notes=(
            "Max 1,000 results per query. Code search requires auth and is "
            "limited to 10 req/min. Partitioning by date and language is essential."
        ),
    )

    def _build_client(self) -> httpx.Client:
        """Build client with GitHub-specific headers."""
        token = self._env("GITHUB_TOKEN")
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "msrkit/0.1 (academic research tool)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers=headers,
        )

    def available(self) -> Availability:
        """Check for GITHUB_TOKEN. Works without but at much lower rate."""
        token = self._env("GITHUB_TOKEN")
        if not token:
            return Availability(
                status=AvailabilityStatus.DEGRADED,
                reason=(
                    "GITHUB_TOKEN not set. Rate limited to 60 req/h (vs 5,000 "
                    "authenticated). Code search unavailable without auth."
                ),
                missing_env=["GITHUB_TOKEN"],
            )
        return Availability(
            status=AvailabilityStatus.OK,
            reason="GitHub token configured. 5,000 req/h, code search available.",
        )

    def estimate(self, q: Query) -> int | None:
        """Use total_count from the search response."""
        kind = q.kind or "repo"
        endpoint = _KIND_ENDPOINT.get(kind, "/search/repositories")
        query_string = self._build_query_string(q)

        params: dict[str, Any] = {"q": query_string, "per_page": 1}
        resp = self._governed_get(f"{_BASE_URL}{endpoint}", params=params)

        if resp.status_code != 200:
            logger.warning("GitHub estimate failed: %d", resp.status_code)
            return None
        return resp.json().get("total_count")

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search GitHub, paginating up to 10 pages (1000 results)."""
        kind = q.kind or "repo"
        if kind == "code" and not self._env("GITHUB_TOKEN"):
            logger.info("GitHub code search requires GITHUB_TOKEN; skipping unauthenticated code.")
            return

        endpoint = _KIND_ENDPOINT.get(kind, "/search/repositories")
        limit = q.limit or 5000
        total_yielded = 0
        seen_ids: set[str] = set()

        terms_to_search = q.terms if (q.terms and len(q.terms) > 1) else [None]
        languages = q.extra.get("languages") or [None]

        for term in terms_to_search:
            for lang in languages:
                if total_yielded >= limit:
                    return

                if term is not None or lang is not None:
                    query_string = self._build_single_query_string(q, term=term, language=lang)
                else:
                    query_string = self._build_query_string(q)

                for page in range(1, (self.policy.max_pages or 10) + 1):
                    if total_yielded >= limit:
                        return

                    page_size = min(self.policy.max_page_size, limit - total_yielded)
                    params: dict[str, Any] = {
                        "q": query_string,
                        "per_page": page_size,
                        "page": page,
                    }
                    if kind == "code":
                        params["sort"] = "indexed"
                        params["order"] = "desc"
                    else:
                        params["sort"] = "updated"
                        params["order"] = "desc"

                    resp = self._governed_get(f"{_BASE_URL}{endpoint}", params=params)

                    if resp.status_code == 422:
                        logger.warning("GitHub: validation error for query=%s", query_string)
                        break
                    if resp.status_code == 401:
                        logger.warning("GitHub: 401 unauthorized for %s (token required)", kind)
                        return
                    if resp.status_code != 200:
                        logger.warning("GitHub search returned %d", resp.status_code)
                        break

                    data = resp.json()
                    items = data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        if total_yielded >= limit:
                            return
                        if kind == "code":
                            repo_name = (item.get("repository") or {}).get("full_name") or ""
                            path = item.get("path") or ""
                            native_id = (
                                f"{repo_name}:{path}"
                                if (repo_name and path)
                                else str(item.get("sha", ""))
                            )
                        else:
                            native_id = str(item.get("id", ""))
                        if not native_id or native_id in seen_ids:
                            continue
                        seen_ids.add(native_id)
                        yield self._make_raw_item(
                            source=self.name,
                            native_id=native_id,
                            payload={**item, "_search_kind": kind},
                        )
                        total_yielded += 1

                    if len(items) < page_size:
                        break

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert GitHub search result to canonical Item."""
        p = raw.payload
        search_kind = p.get("_search_kind", "repo")

        if search_kind == "repo":
            return self._normalize_repo(p, raw, terms)
        elif search_kind == "code":
            return self._normalize_code(p, raw, terms)
        else:
            return self._normalize_issue(p, raw, terms)

    def _normalize_repo(
        self, p: dict[str, Any], raw: RawItem, terms: list[str] | None = None
    ) -> Item:
        created_at = self._parse_dt(p.get("created_at"))
        updated_at = self._parse_dt(p.get("updated_at"))
        topics = p.get("topics", [])

        matched = match_terms(
            terms or [],
            title=p.get("full_name"),
            body=p.get("description"),
            tags=topics,
        )

        return Item(
            id=Item.make_id(self.name, str(p.get("id", ""))),
            source=self.name,
            kind=ItemKind.REPO,
            url=p.get("html_url", ""),  # type: ignore[arg-type]
            title=p.get("full_name"),
            body=p.get("description"),
            author_handle=(p.get("owner") or {}).get("login"),
            created_at=created_at,
            updated_at=updated_at,
            engagement=Engagement(
                stars=p.get("stargazers_count"),
                forks=p.get("forks_count"),
            ),
            tech=TechContext(
                language=p.get("language"),
                license=(
                    (p.get("license") or {}).get("spdx_id")
                    if isinstance(p.get("license"), dict)
                    else None
                ),
                tags=topics,
            ),
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

    def _normalize_code(
        self, p: dict[str, Any], raw: RawItem, terms: list[str] | None = None
    ) -> Item:
        repo = p.get("repository") or {}
        matched = match_terms(terms or [], title=p.get("name"), path=p.get("path"))

        return Item(
            id=Item.make_id(self.name, raw.native_id or p.get("sha", "")),
            source=self.name,
            kind=ItemKind.CODE,
            url=p.get("html_url", ""),  # type: ignore[arg-type]
            title=p.get("name"),
            body=None,  # metadata_only
            author_handle=(repo.get("owner") or {}).get("login"),
            engagement=Engagement(),
            tech=TechContext(
                language=repo.get("language"),
                path=p.get("path"),
            ),
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

    def _normalize_issue(
        self, p: dict[str, Any], raw: RawItem, terms: list[str] | None = None
    ) -> Item:
        created_at = self._parse_dt(p.get("created_at"))
        updated_at = self._parse_dt(p.get("updated_at"))
        labels = [
            label.get("name", "") if isinstance(label, dict) else str(label)
            for label in (p.get("labels") or [])
        ]

        matched = match_terms(terms or [], title=p.get("title"), body=p.get("body"), tags=labels)

        return Item(
            id=Item.make_id(self.name, str(p.get("id", ""))),
            source=self.name,
            kind=ItemKind.ISSUE,
            url=p.get("html_url", ""),  # type: ignore[arg-type]
            title=p.get("title"),
            body=p.get("body"),
            author_handle=(p.get("user") or {}).get("login"),
            created_at=created_at,
            updated_at=updated_at,
            engagement=Engagement(
                comments=p.get("comments"),
                reactions=(p.get("reactions") or {}).get("total_count"),
            ),
            tech=TechContext(tags=labels),
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

    def _build_query_string(self, q: Query) -> str:
        """Build GitHub search query string with qualifiers."""
        kind = q.kind or "repo"
        parts = [" ".join(q.terms)] if q.terms else []

        if kind != "code":
            if q.since and q.until:
                parts.append(f"created:{q.since}..{q.until}")
            elif q.since:
                parts.append(f"created:>={q.since}")
            elif q.until:
                parts.append(f"created:<={q.until}")

        # Extra qualifiers
        extra = q.extra
        if extra.get("languages"):
            for lang in extra["languages"]:
                parts.append(f"language:{lang}")

        if kind == "repo" and extra.get("min_stars"):
            parts.append(f"stars:>={extra['min_stars']}")

        return " ".join(parts)

    def _build_single_query_string(
        self, q: Query, term: str | None = None, language: str | None = None
    ) -> str:
        """Build GitHub search query string for a single term and language qualifier."""
        kind = q.kind or "repo"
        parts = []
        if term:
            parts.append(f'"{term}"' if " " in term else term)
        elif q.terms:
            parts.append(" ".join(q.terms))

        if kind != "code":
            if q.since and q.until:
                parts.append(f"created:{q.since}..{q.until}")
            elif q.since:
                parts.append(f"created:>={q.since}")
            elif q.until:
                parts.append(f"created:<={q.until}")

        if language:
            parts.append(f"language:{language}")

        if kind == "repo" and q.extra.get("min_stars"):
            parts.append(f"stars:>={q.extra['min_stars']}")

        return " ".join(parts)

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
