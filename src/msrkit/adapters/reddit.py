"""Reddit adapter via OAuth2 API.

Uses client credentials OAuth2 flow.
Custom User-Agent is mandatory.

Endpoints:
    POST https://www.reddit.com/api/v1/access_token
    GET https://oauth.reddit.com/r/{sub}/search
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
    SourcePolicy,
    TechContext,
)
from msrkit.registry import register

logger = logging.getLogger(__name__)


@register
class RedditAdapter(BaseAdapter):
    """Reddit adapter using the OAuth2 API."""

    name: ClassVar[str] = "reddit"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"],
        rate_limit=RateLimit(
            requests=10,
            per_seconds=60,
            burst=5,
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=None,
        supports_full_text_search=True,
        supports_date_filter=False,  # Busca do Reddit não tem filtro de data nativo robusto
        redistribution="metadata_only",
        tos_url="https://www.redditinc.com/policies/developer-terms",
        docs_url="https://www.reddit.com/dev/api/",
        notes=(
            "Free tier is limited. Custom User-Agent mandatory. "
            "Non-commercial use only on free tier."
        ),
    )

    _access_token: str | None = None

    def available(self) -> Availability:
        """Check Reddit OAuth2 credentials."""
        all_present, missing = self._check_env_vars(self.policy.auth_env_vars)
        if not all_present:
            return Availability(
                status=AvailabilityStatus.UNSUPPORTED,
                reason=(f"Reddit OAuth2 credentials not configured. Missing: {', '.join(missing)}"),
                missing_env=missing,
            )
        return Availability(
            status=AvailabilityStatus.OK,
            reason="Reddit OAuth2 credentials configured.",
        )

    def _authenticate(self) -> str | None:
        """Get an OAuth2 access token using client credentials."""
        client_id = self._env("REDDIT_CLIENT_ID")
        client_secret = self._env("REDDIT_CLIENT_SECRET")
        user_agent = self._env("REDDIT_USER_AGENT") or "msrkit/0.1"

        if not client_id or not client_secret:
            return None

        try:
            resp = httpx.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": user_agent},
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data.get("access_token")
                return self._access_token
            else:
                logger.warning("Reddit auth failed: %d", resp.status_code)
                return None
        except Exception as e:
            logger.warning("Reddit auth error: %s", e)
            return None

    def _build_client(self) -> httpx.Client:
        """Build client with Reddit User-Agent and auth."""
        user_agent = self._env("REDDIT_USER_AGENT") or "msrkit/0.1"
        headers: dict[str, str] = {"User-Agent": user_agent}

        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers=headers,
        )

    def estimate(self, q: Query) -> int | None:
        """Reddit API does not provide result count estimates."""
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search across configured subreddits."""
        if not self._access_token:
            self._authenticate()
            if not self._access_token:
                logger.warning("Reddit: cannot authenticate, skipping")
                return

        # Rebuild client with auth
        self._client = None  # Force rebuild

        subreddits = q.extra.get(
            "subreddits",
            ["LocalLLaMA", "LangChain", "LLMDevs"],
        )
        limit = q.limit or 5000
        total_yielded = 0

        query_text = " OR ".join(q.terms)

        for sub in subreddits:
            if total_yielded >= limit:
                break
            after: str | None = None

            while total_yielded < limit:
                params: dict[str, Any] = {
                    "q": query_text,
                    "restrict_sr": 1,
                    "sort": "new",
                    "limit": self.policy.max_page_size,
                    "t": "all",
                }
                if after:
                    params["after"] = after

                resp = self._governed_get(
                    f"https://oauth.reddit.com/r/{sub}/search",
                    params=params,
                )

                if resp.status_code != 200:
                    logger.warning("Reddit search returned %d for r/%s", resp.status_code, sub)
                    break

                data = resp.json()
                children = data.get("data", {}).get("children", [])
                if not children:
                    break

                for child in children:
                    if total_yielded >= limit:
                        return
                    post = child.get("data", {})
                    yield self._make_raw_item(
                        source=self.name,
                        native_id=post.get("id", ""),
                        payload=post,
                    )
                    total_yielded += 1

                after = data.get("data", {}).get("after")
                if not after:
                    break

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert Reddit post to canonical Item."""
        p = raw.payload

        created_at = None
        if p.get("created_utc"):
            created_at = datetime.fromtimestamp(p["created_utc"], tz=UTC)

        subreddit = p.get("subreddit", "")
        matched = match_terms(
            terms or [],
            title=p.get("title"),
            body=p.get("selftext"),
            tags=[subreddit] if subreddit else [],
        )
        url = f"https://www.reddit.com{p.get('permalink', '')}"

        return Item(
            id=Item.make_id(self.name, p.get("id", "")),
            source=self.name,
            kind=ItemKind.POST,
            url=url,  # type: ignore[arg-type]
            title=p.get("title"),
            body=None,  # metadata_only
            body_hash=None,
            author_handle=p.get("author"),
            created_at=created_at,
            engagement=Engagement(
                votes=p.get("score"),
                comments=p.get("num_comments"),
            ),
            tech=TechContext(tags=[subreddit] if subreddit else []),
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
