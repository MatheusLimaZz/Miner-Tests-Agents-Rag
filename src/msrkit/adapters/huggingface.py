"""Hugging Face Hub adapter.

Searches models, datasets, and spaces via the public API.
Auth is optional (HF_TOKEN).

Endpoints:
    GET https://huggingface.co/api/models
    GET https://huggingface.co/api/datasets
    GET https://huggingface.co/api/spaces
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

_BASE_URL = "https://huggingface.co/api"


@register
class HuggingFaceAdapter(BaseAdapter):
    """Hugging Face Hub adapter."""

    name: ClassVar[str] = "huggingface"
    version: ClassVar[str] = "0.1.0"
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        requires_auth=False,
        auth_env_vars=["HF_TOKEN"],
        rate_limit=RateLimit(
            requests=30,
            per_seconds=60,
            burst=10,
        ),
        max_results_per_query=None,
        max_page_size=100,
        max_pages=None,
        supports_full_text_search=True,
        supports_date_filter=False,
        redistribution="metadata_only",
        tos_url="https://huggingface.co/terms-of-service",
        docs_url="https://huggingface.co/docs/hub/api",
        notes="Search across models, datasets, and spaces. Auth optional but increases limits.",
    )

    def _build_client(self) -> httpx.Client:
        """Build client with optional HF token."""
        headers: dict[str, str] = {
            "User-Agent": "msrkit/0.1 (academic research tool)",
        }
        token = self._env("HF_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers=headers,
        )

    def available(self) -> Availability:
        """HF API works without auth but with lower limits."""
        token = self._env("HF_TOKEN")
        if not token:
            return Availability(
                status=AvailabilityStatus.DEGRADED,
                reason="HF_TOKEN not set. Public access with lower rate limits.",
                missing_env=["HF_TOKEN"],
            )
        return Availability(
            status=AvailabilityStatus.OK,
            reason="Hugging Face token configured.",
        )

    def estimate(self, q: Query) -> int | None:
        """HF API does not provide result count estimates."""
        return None

    def search(self, q: Query) -> Iterator[RawItem]:
        """Search models, datasets, and spaces."""
        kinds = q.extra.get("kinds", ["models", "datasets", "spaces"])
        limit = q.limit or 5000
        total_yielded = 0
        search_text = " ".join(q.terms)

        for kind in kinds:
            if total_yielded >= limit:
                break

            endpoint = f"{_BASE_URL}/{kind}"
            params: dict[str, Any] = {
                "search": search_text,
                "limit": min(self.policy.max_page_size, limit - total_yielded),
                "full": "true",
            }

            resp = self._governed_get(endpoint, params=params)
            if resp.status_code != 200:
                logger.warning("HF search returned %d for %s", resp.status_code, kind)
                continue

            items = resp.json()
            if not isinstance(items, list):
                continue

            for item in items:
                if total_yielded >= limit:
                    return
                item_id = item.get("id", item.get("modelId", ""))
                yield self._make_raw_item(
                    source=self.name,
                    native_id=str(item_id),
                    payload={**item, "_hf_kind": kind},
                )
                total_yielded += 1

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        """Convert HF model/dataset/space to canonical Item."""
        p = raw.payload

        item_id = p.get("id", p.get("modelId", ""))
        kind = ItemKind.CARD  # HF items are essentially model/dataset cards

        created_at = self._parse_dt(p.get("createdAt"))
        updated_at = self._parse_dt(p.get("lastModified"))

        tags = p.get("tags", [])
        url = f"https://huggingface.co/{item_id}"

        matched = match_terms(
            terms or [],
            title=item_id,
            body=p.get("description"),
            tags=tags,
        )

        return Item(
            id=Item.make_id(self.name, str(item_id)),
            source=self.name,
            kind=kind,
            url=url,  # type: ignore[arg-type]
            title=item_id,
            body=None,
            author_handle=p.get("author"),
            created_at=created_at,
            updated_at=updated_at,
            engagement=Engagement(
                stars=p.get("likes"),
                views=p.get("downloads"),
            ),
            tech=TechContext(
                language=p.get("pipeline_tag"),
                license=p.get("license") if isinstance(p.get("license"), str) else None,
                tags=tags,
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

    @staticmethod
    def _parse_dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
