"""Bluesky adapter via AT Protocol.

Viable substitute for X/Twitter for short-form technical discourse.
Free, with real search.

Endpoints:
    POST /xrpc/com.atproto.server.createSession
    GET /xrpc/app.bsky.feed.searchPosts
"""

from __future__ import annotations

import contextlib
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

_BASE_URL = "https://bsky.social/xrpc"


@register
class BlueskyAdapter(BaseAdapter):
    """Bluesky adapter using the AT Protocol."""

    name: ClassVar[str] = "bluesky"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=True,
        auth_env_vars=["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
        rate_limit=RateLimit(
            requests=30,
            per_seconds=60,
            burst=10,
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=None,
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution="metadata_only",
        tos_url="https://bsky.social/about/support/tos",
        docs_url="https://docs.bsky.app/docs/api/app-bsky-feed-search-posts",
        notes=(
            "Free with real search. Substitute for X/Twitter since X paywalled "
            "read access. Document the substitution in the study paper."
        ),
    )

    _session_token: str | None = None

    def available(self) -> Availability:
        """Check for Bluesky credentials."""
        all_present, missing = self._check_env_vars(self.policy.auth_env_vars)
        if not all_present:
            return Availability(
                status=AvailabilityStatus.UNSUPPORTED,
                reason=f"Bluesky credentials not configured. Missing: {', '.join(missing)}",
                missing_env=missing,
            )
        return Availability(
            status=AvailabilityStatus.OK,
            reason="Bluesky credentials configured.",
        )

    def _create_session(self) -> bool:
        """Authenticate with Bluesky."""
        handle = self._env("BLUESKY_HANDLE")
        password = self._env("BLUESKY_APP_PASSWORD")

        if not handle or not password:
            return False

        try:
            resp = httpx.post(
                f"{_BASE_URL}/com.atproto.server.createSession",
                json={"identifier": handle, "password": password},
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._session_token = data.get("accessJwt")
                return True
            else:
                logger.warning("Bluesky auth failed: %d", resp.status_code)
                return False
        except Exception as e:
            logger.warning("Bluesky auth error: %s", e)
            return False

    def estimate(self, q: Query) -> int | None:
        """Bluesky search does not provide result count estimates."""
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search Bluesky posts."""
        if not self._session_token and not self._create_session():
            logger.warning("Bluesky: cannot authenticate, skipping")
            return

        limit = q.limit or 5000
        total_yielded = 0
        cursor: str | None = None
        query_text = " ".join(q.terms)

        while total_yielded < limit:
            params: dict[str, Any] = {
                "q": query_text,
                "limit": min(self.policy.max_page_size, 100),
            }
            if cursor:
                params["cursor"] = cursor
            if q.since:
                params["since"] = q.since.isoformat()
            if q.until:
                params["until"] = q.until.isoformat()

            headers = {}
            if self._session_token:
                headers["Authorization"] = f"Bearer {self._session_token}"

            resp = self._governed_get(
                f"{_BASE_URL}/app.bsky.feed.searchPosts",
                params=params,
                headers=headers,
            )

            if resp.status_code != 200:
                logger.warning("Bluesky search returned %d", resp.status_code)
                break

            data = resp.json()
            posts = data.get("posts", [])
            if not posts:
                break

            for post in posts:
                if total_yielded >= limit:
                    return
                uri = post.get("uri", "")
                yield self._make_raw_item(
                    source=self.name,
                    native_id=uri,
                    payload=post,
                )
                total_yielded += 1

            cursor = data.get("cursor")
            if not cursor:
                break

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert Bluesky post to canonical Item."""
        p = raw.payload
        record = p.get("record", {})

        created_at = None
        if record.get("createdAt"):
            with contextlib.suppress(ValueError, TypeError):
                created_at = datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00"))

        author = p.get("author", {})
        handle = author.get("handle", "")

        text = record.get("text", "")
        uri = p.get("uri", "")

        # Convert AT URI to web URL
        # at://did:plc:xxx/app.bsky.feed.post/yyy → https://bsky.app/profile/handle/post/yyy
        url = f"https://bsky.app/profile/{handle}/post/{uri.split('/')[-1]}" if uri else ""

        matched = match_terms(terms or [], title=None, body=text)

        return Item(
            id=Item.make_id(self.name, uri),
            source=self.name,
            kind=ItemKind.POST,
            url=url,  # type: ignore[arg-type]
            title=None,
            body=None,  # metadata_only
            body_hash=None,
            author_handle=handle,
            created_at=created_at,
            engagement=Engagement(
                reactions=p.get("likeCount"),
                comments=p.get("replyCount"),
            ),
            tech=TechContext(),
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
