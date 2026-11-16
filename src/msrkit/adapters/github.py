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
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator

    from msrkit.governor import Governor

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
from msrkit.partition import partition_capped
from msrkit.registry import register

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"
_KIND_ENDPOINT = {
    "repo": "/search/repositories",
    "code": "/search/code",
    "issue": "/search/issues",
}
# /search/issues rejects (422) a query that does not say issue or pull request
# (seen on 2026-11-01; d28a ran without it on 2026-10-20). See ADR-049.
_ISSUE_TYPE = re.compile(r"(?<!\S)-?(is|type):(issue|pr|pull-request)\b")


class QueryRejectedError(RuntimeError):
    """GitHub refused one or more search queries with 422 (invalid query)."""


def is_bot_author(author: str | None, account_type: str | None = None) -> bool:
    """Whether an issue author is an automated account.

    GitHub Apps are declared by the API itself (`user.type == "Bot"`, login ending in
    `[bot]`). Machine users are ordinary accounts, known only by the login: the usual
    `-bot` suffix and the dependency tools. A login that merely ends in "bot" is not
    taken as one, since people have such names.
    """
    if account_type == "Bot":
        return True
    if not author:
        return False
    lower = author.lower()
    return (
        lower.endswith("[bot]")
        or lower.endswith("-bot")
        or lower in {"dependabot", "renovate", "github-actions", "snyk-bot", "stale"}
        or "dependabot" in lower
        or "renovate" in lower
    )


def issue_labels(payload: dict[str, Any]) -> list[str]:
    """Label names of an issue as the search API returns them."""
    return [
        label.get("name", "") if isinstance(label, dict) else str(label)
        for label in (payload.get("labels") or [])
    ]


@register
class GitHubAdapter(BaseAdapter):
    """GitHub adapter using the REST search API."""

    name: ClassVar[str] = "github"
    # 0.2.0: issue searches add is:issue (ADR-049); 0.2.1: code search without sort (ADR-053);
    # 0.3.0: issue filters (automated authors, labels) and a fixed page size (ADR-057)
    version: ClassVar[str] = "0.3.0"
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
        redistribution=RedistributionPolicy.METADATA_ONLY,
        supports_raw_queries=True,
        tos_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service",
        docs_url="https://docs.github.com/en/rest/search",
        notes=(
            "Max 1,000 results per query. Code search requires auth and is "
            "limited to 10 req/min. Partitioning by date and language is essential."
        ),
        has_official_api=True,
        official_api_name="GitHub REST v3 / GraphQL v4",
    )

    def __init__(self, governor: Governor | None = None) -> None:
        super().__init__(governor)
        # total_count of the queries counted so far: "<kind>|<query string>" -> results.
        self._result_counts: dict[str, int] = {}

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

    @classmethod
    def effective_rate_limit(cls) -> RateLimit:
        """Rate limit to enforce: 30 req/min with token, 10 req/min without token (burst 2)."""
        base = cls.policy.rate_limit
        if cls._env("GITHUB_TOKEN"):
            return base
        return RateLimit(
            requests=10,
            per_seconds=60,
            burst=2,
            daily_cap=None,
        )

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> httpx.Response:
        """Execute a governed GraphQL query against GitHub API v4."""
        return self._governed_post(
            f"{_BASE_URL}/graphql",
            json={"query": query, "variables": variables or {}},
        )

    def estimate(self, q: Query) -> int | None:
        """Largest total_count among the API queries search() will issue.

        search() sends one query per (term, language), each capped at 1,000
        results, so the partition decision depends on the largest of them.
        """
        counts = self._total_counts(q)
        return None if counts is None else max(counts, default=0)

    def population(self, q: Query) -> int | None:
        """Sum of total_count over the (term, language) queries search() issues.

        Languages are disjoint; a query normally holds one term after split_by_term.
        """
        counts = self._total_counts(q)
        return None if counts is None else sum(counts)

    def stratifies_by_date(self, q: Query) -> bool:
        """Repositories and issues take created:; code search has no date qualifier."""
        fixed_dates = q.raw and any("created:" in t for t in q.terms)
        return (q.kind or "repo") != "code" and not fixed_dates

    def countable(self, q: Query) -> bool:
        """Every search reports total_count, code search included (it needs the token)."""
        return (q.kind or "repo") != "code" or bool(self._env("GITHUB_TOKEN"))

    def _total_counts(self, q: Query) -> list[int] | None:
        """total_count of each (term, language) query search() issues; None on failure.

        Each count is kept by query string: search() shares a query's item cap among
        its languages in proportion to them (see _language_quotas).
        """
        kind = q.kind or "repo"
        endpoint = _KIND_ENDPOINT.get(kind, "/search/repositories")
        terms: list[str | None] = list(q.terms) or [None]
        languages = [None] if q.raw else (q.extra.get("languages") or [None])

        counts: list[int] = []
        for term in terms:
            for lang in languages:
                query_string = self._build_single_query_string(q, term=term, language=lang)
                params: dict[str, Any] = {"q": query_string, "per_page": 1}
                resp = self._governed_get(f"{_BASE_URL}{endpoint}", params=params)
                if resp.status_code != 200:
                    logger.warning("GitHub estimate failed: %d", resp.status_code)
                    return None
                count = int(resp.json().get("total_count", 0) or 0)
                self._result_counts[f"{kind}|{query_string}"] = count
                counts.append(count)
        return counts

    def _language_quotas(
        self, q: Query, term: str | None, languages: list[Any], budget: int
    ) -> list[tuple[Any, int]]:
        """(language, share of `budget`) for one term, smallest result count first.

        The shares are proportional to the result counts population() read, so a single
        sampling fraction holds across the query's languages and its sampling weight is
        valid for all of them (ADR-057). Filling the cap in the order of the protocol
        took every item from the first language whenever it alone had enough results.
        A language that cannot fill its share passes the rest on to the next, larger one.
        Without the counts, the first language may take the whole budget, as before.
        """
        kind = q.kind or "repo"
        counts = [
            self._result_counts.get(
                f"{kind}|{self._build_single_query_string(q, term=term, language=lang)}"
            )
            for lang in languages
        ]
        known = [c for c in counts if c is not None]
        if len(languages) < 2 or len(known) < len(counts) or sum(known) == 0:
            return [(lang, budget if i == 0 else 0) for i, lang in enumerate(languages)]
        total = sum(known)
        shares = [budget * c / total for c in known]
        quotas = [int(s) for s in shares]
        by_remainder = sorted(range(len(shares)), key=lambda i: shares[i] - quotas[i], reverse=True)
        for i in by_remainder[: budget - sum(quotas)]:
            quotas[i] += 1
        for i, count in enumerate(known):  # a language with results is never left out
            if count and not quotas[i] and max(quotas) > 1:
                quotas[quotas.index(max(quotas))] -= 1
                quotas[i] = 1
        order = sorted(range(len(languages)), key=lambda i: known[i])
        return [(languages[i], quotas[i]) for i in order]

    def partition(self, q: Query) -> list[Query]:
        """Split by term, then by date window until each query fits the 1,000 cap."""
        kind = q.kind or "repo"
        if kind == "code" and not self._env("GITHUB_TOKEN"):
            return [q]  # search() skips unauthenticated code search

        from msrkit.partition import split_by_term

        # If a small query limit is set (e.g. fast test with limit <= 100),
        # or unauthenticated without monkeypatched estimate, skip pre-flight counting.
        is_default_estimate = getattr(self.estimate, "__func__", None) is GitHubAdapter.estimate
        if (q.limit is not None and q.limit <= 100) or (
            not self._env("GITHUB_TOKEN") and is_default_estimate
        ):
            return split_by_term(q)

        # Code search has no date qualifier; a raw query that fixes its own
        # created: range cannot be split by date either.
        fixed_dates = q.raw and "created:" in (q.terms[0] if q.terms else "")
        return partition_capped(
            q,
            self.policy.max_results_per_query,
            self.estimate,
            split_dates=kind != "code" and not fixed_dates,
        )

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search GitHub, paginating up to 10 pages (1000 results)."""
        kind = q.kind or "repo"
        if kind == "code" and not self._env("GITHUB_TOKEN"):
            logger.info("GitHub code search requires GITHUB_TOKEN; skipping unauthenticated code.")
            return

        endpoint = _KIND_ENDPOINT.get(kind, "/search/repositories")
        limit = 5000 if q.limit is None else q.limit
        total_yielded = 0
        seen_ids: set[str] = set()

        terms_to_search: list[str | None] = list(q.terms) or [None]
        languages = [None] if q.raw else (q.extra.get("languages") or [None])
        rejected: list[str] = []
        # Issue filters (protocol `extra`): automated authors and labels that mark
        # records outside the study. What they drop is recorded as a discard.
        filter_bots = kind == "issue" and bool(q.extra.get("filter_bots", False))
        excluded_labels = (
            {str(label).lower() for label in q.extra.get("exclude_labels") or []}
            if kind == "issue"
            else set()
        )

        for term in terms_to_search:
            carry = 0  # share a language did not use, passed on to the next
            for lang, quota in self._language_quotas(q, term, languages, limit - total_yielded):
                if total_yielded >= limit:
                    return
                lang_limit = quota + carry
                carry = lang_limit
                if lang_limit <= 0:
                    continue
                lang_yielded = 0

                if term is not None or lang is not None:
                    query_string = self._build_single_query_string(q, term=term, language=lang)
                else:
                    query_string = self._build_query_string(q)

                # One page size for every page of a query. Shrinking it as items come in
                # shifts the page boundaries: with a filter dropping items, the next page
                # would return results already seen and count their discards twice.
                page_size = min(self.policy.max_page_size, lang_limit)
                for page in range(1, (self.policy.max_pages or 10) + 1):
                    if lang_yielded >= lang_limit:
                        break

                    params: dict[str, Any] = {
                        "q": query_string,
                        "per_page": page_size,
                        "page": page,
                    }
                    if kind != "code":
                        params["sort"] = "updated"
                        params["order"] = "desc"
                    # Code search keeps the default best-match order: its `sort` is
                    # closing down, and `indexed` returned the same set (ADR-053).

                    resp = self._governed_get(f"{_BASE_URL}{endpoint}", params=params)

                    if resp.status_code == 422:
                        message = self._error_message(resp)
                        logger.warning(
                            "GitHub: validation error for query=%s: %s", query_string, message
                        )
                        rejected.append(f"{query_string} ({message})")
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
                        if lang_yielded >= lang_limit:
                            break
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

                        user = item.get("user") or {}
                        reason = None
                        if filter_bots and is_bot_author(user.get("login"), user.get("type")):
                            reason = "bot_author"
                        elif excluded_labels and excluded_labels.intersection(
                            name.lower() for name in issue_labels(item)
                        ):
                            reason = "excluded_label"
                        if reason is not None:
                            self._record_discard(
                                native_id=native_id,
                                title=item.get("title") or "",
                                url=item.get("html_url") or "",
                                reason=reason,
                            )
                            continue

                        yield self._make_raw_item(
                            source=self.name,
                            native_id=native_id,
                            payload={**item, "_search_kind": kind},
                        )
                        total_yielded += 1
                        lang_yielded += 1
                        carry = lang_limit - lang_yielded

                    if len(items) < page_size:
                        break

        # Raised after the other languages ran, so their items are kept; the query
        # is recorded as not completed, reported, and redone by --resume.
        if rejected:
            raise QueryRejectedError(
                f"GitHub rejected {len(rejected)} query(ies) with 422: {'; '.join(rejected)}"
            )

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        """The API's reason for an error response, or its status code."""
        try:
            data = resp.json()
        except ValueError:
            return f"HTTP {resp.status_code}"
        messages = [e.get("message", "") for e in data.get("errors", []) if isinstance(e, dict)]
        return "; ".join(m for m in [data.get("message", ""), *messages] if m)

    @staticmethod
    def _with_issue_type(kind: str, query: str) -> str:
        """Restrict issue searches to issues: the protocol's kind is `issue` (ADR-049)."""
        if kind != "issue" or _ISSUE_TYPE.search(query):
            return query
        return f"{query} is:issue"

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
            url=p.get("html_url", ""),
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
            url=p.get("html_url", ""),
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
        labels = issue_labels(p)

        matched = match_terms(terms or [], title=p.get("title"), body=p.get("body"), tags=labels)

        return Item(
            id=Item.make_id(self.name, str(p.get("id", ""))),
            source=self.name,
            kind=ItemKind.ISSUE,
            url=p.get("html_url", ""),
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

        return self._with_issue_type(kind, " ".join(parts))

    def _build_single_query_string(
        self, q: Query, term: str | None = None, language: str | None = None
    ) -> str:
        """Build GitHub search query string for a single term and language qualifier."""
        kind = q.kind or "repo"
        if q.raw and term:
            # Literal protocol query: keep it verbatim, add only the date window
            # (and is:issue to issue searches that do not set the type).
            if kind == "code" or "created:" in term or not (q.since or q.until):
                literal = term
            elif q.since and q.until:
                literal = f"{term} created:{q.since}..{q.until}"
            elif q.since:
                literal = f"{term} created:>={q.since}"
            else:
                literal = f"{term} created:<={q.until}"
            return self._with_issue_type(kind, literal)
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

        return self._with_issue_type(kind, " ".join(parts))

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
