"""Domain models for MSR-Kit.

Canonical data models for items, queries, provenance, source policies,
and adapter availability. All models use Pydantic v2 for validation
and serialization.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, HttpUrl, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ItemKind(StrEnum):
    """Kinds of items that can be collected from sources."""

    REPO = "repo"
    CODE = "code"
    ISSUE = "issue"
    DISCUSSION = "discussion"
    THREAD = "thread"
    ARTICLE = "article"
    POST = "post"
    CARD = "card"
    COMMENT = "comment"


class AvailabilityStatus(StrEnum):
    """Adapter availability status."""

    OK = "OK"
    DEGRADED = "DEGRADED"
    UNSUPPORTED = "UNSUPPORTED"


class RedistributionPolicy(StrEnum):
    """Content redistribution policy for a source."""

    METADATA_ONLY = "metadata_only"
    FULL_TEXT_WITH_ATTRIBUTION = "full_text_with_attribution"
    FULL_TEXT = "full_text"


# ---------------------------------------------------------------------------
# Engagement & TechContext
# ---------------------------------------------------------------------------


class Engagement(BaseModel):
    """Engagement metrics for an item."""

    stars: int | None = None
    forks: int | None = None
    votes: int | None = None
    reactions: int | None = None
    comments: int | None = None
    views: int | None = None


class TechContext(BaseModel):
    """Technical context metadata for an item."""

    language: str | None = None
    path: str | None = None
    license: str | None = None
    has_ci: bool | None = None
    contributors: int | None = None
    has_tests: bool | None = None  # test directory or test files in the repository
    active_months: int | None = None  # distinct months with commits inside the window
    tags: list[str] = []


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Full provenance record for an item, ensuring traceability."""

    run_id: str
    query_string: str
    partition: str
    adapter: str
    adapter_version: str
    fetched_at: datetime
    response_sha256: str
    raw_ref: str  # path to raw file + line offset


# ---------------------------------------------------------------------------
# TermHit
# ---------------------------------------------------------------------------


class TermHit(BaseModel):
    """A single keyword match with surrounding context."""

    term: str
    field: Literal["title", "body", "tags", "path"]
    context: str  # ±40 tokens around the occurrence
    lang: str | None = None  # language of the lexicon the term belongs to


# ---------------------------------------------------------------------------
# Item (canonical)
# ---------------------------------------------------------------------------


class Item(BaseModel):
    """Canonical item — the normalized, deduplicated unit of the corpus."""

    id: str  # sha256(f"{source}:{native_id}")[:16]
    source: str
    kind: ItemKind
    url: HttpUrl
    title: str | None = None
    body: str | None = None
    body_hash: str | None = None  # sha256 of body, always filled when body exists
    author_handle: str | None = None  # public identifier only
    created_at: datetime | None = None
    updated_at: datetime | None = None
    engagement: Engagement = Engagement()
    tech: TechContext = TechContext()
    matched_terms: list[TermHit] = []
    # Protocol concept groups whose lexicon matched (e.g. ["rag", "teste"]);
    # for Study E2 the system label is concepts ∩ {rag, agente} (§3.1).
    concepts: list[str] = []
    provenance: Provenance

    @model_validator(mode="after")
    def _compute_body_hash(self) -> Item:
        """Auto-compute body_hash from body if not provided."""
        if self.body_hash is None and self.body is not None:
            self.body_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()
        return self

    @staticmethod
    def make_id(source: str, native_id: str) -> str:
        """Compute deterministic item ID from source and native ID."""
        return hashlib.sha256(f"{source}:{native_id}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# RawItem
# ---------------------------------------------------------------------------


class RawItem(BaseModel):
    """Raw API response without transformation."""

    source: str
    native_id: str
    payload: dict[str, Any]
    fetched_at: datetime


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class Query(BaseModel):
    """A search query targeting a specific source."""

    source: str
    terms: list[str]
    kind: str | None = None
    since: date | None = None
    until: date | None = None
    extra: dict[str, Any] = {}
    limit: int | None = None
    # True when terms[0] is a literal query in the source's own search syntax
    # (protocol `queries:`), passed through without quoting or extra qualifiers.
    raw: bool = False
    label: str = ""  # protocol label of a raw query, kept in provenance
    # Set by adapter.partition(): True when the source's result cap still cuts
    # this partition after all possible splits (potential sampling bias).
    truncated: bool = False
    estimated_total: int | None = None


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class Availability(BaseModel):
    """Adapter availability check result."""

    status: AvailabilityStatus
    reason: str  # always populated, even for OK
    missing_env: list[str] = []


# ---------------------------------------------------------------------------
# SourcePolicy
# ---------------------------------------------------------------------------


class RateLimit(BaseModel):
    """Rate limiting parameters for an API source."""

    requests: int
    per_seconds: int
    burst: int = 1
    daily_cap: int | None = None


class SourcePolicy(BaseModel):
    """Declared policy for a source adapter."""

    requires_auth: bool
    auth_env_vars: list[str]
    rate_limit: RateLimit
    max_results_per_query: int | None = None
    max_page_size: int
    max_pages: int | None = None
    supports_full_text_search: bool
    supports_date_filter: bool
    # False when the source only serves recent items (e.g. RSS), so any
    # historical window is necessarily incomplete.
    historical_coverage: bool = True
    # Accepts literal queries in its own syntax (protocol `queries:`).
    supports_raw_queries: bool = False
    # True when each term is its own API query; False when the source is fetched
    # by tag or feed and filtered locally by the terms (dev.to, RSS).
    searches_each_term: bool = True
    redistribution: RedistributionPolicy
    tos_url: str
    docs_url: str
    notes: str = ""
    # Distinção metodológica vs. técnica de API (§6.1 Protocolo E2 e docs/sources.md)
    has_official_api: bool = True
    official_api_name: str = ""
    # Categories: methodological, access_gated, commercial_paywall,
    # no_public_api, private_enclave
    exclusion_category: str | None = None
    exclusion_rationale: str = ""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class QueryManifestEntry(BaseModel):
    """Manifest entry for a single query execution."""

    query_string: str
    partitions: int
    requests: int
    items: int
    truncated: bool
    # Why the query may be incomplete: "source_cap" (result cap not resolved by
    # partitioning), "no_historical_coverage" (source serves only recent items),
    # "item_limit" (collection stopped at the configured item limit).
    truncation_reasons: list[str] = []
    estimated_total: int | None = None
    # Items fetched but dropped by a local filter; details in runs/<id>/discarded.jsonl
    discarded: int = 0
    key: str = ""  # stable partition id (msrkit.partition.query_key)
    completed: bool = False  # finished without error; skipped on --resume
    failed_partitions: list[str] = []
    response_sha256: list[str] = []


class SourceManifestEntry(BaseModel):
    """Manifest entry for a source in a run."""

    name: str
    adapter_version: str
    availability: Availability
    queries: list[QueryManifestEntry] = []
    # Partition plan frozen at the first run, so --resume does not re-estimate.
    planned: list[dict[str, Any]] = []


class Manifest(BaseModel):
    """Full run manifest with provenance for every source and query."""

    run_id: str
    msrkit_version: str
    protocol_path: str
    protocol_sha256: str
    started_at: datetime
    finished_at: datetime | None = None
    sources: list[SourceManifestEntry] = []
    # Search parameters in force (protocol file + .env overrides: window, terms,
    # enabled sources, source groups) and the MSRKIT_* variables that changed them.
    effective_protocol: dict[str, Any] = {}
    env_overrides: list[str] = []
    # Command-line options that shape the sample (e.g. limit, per_query_limit).
    run_options: dict[str, Any] = {}
    # The code that collected: `git_commit` and `git_dirty` (msrkit.provenance.code_state).
    code: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SourceUnsupportedError(Exception):
    """Raised when attempting to use an unsupported source."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Source '{source}' is unsupported: {reason}")
