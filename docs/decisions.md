# Architecture & Design Decisions (ADRs)

This document records architectural, design, and technical decisions made during the implementation of MSR-Kit v0. As specified in §20.4, whenever ambiguity arises, the **most conservative option** is chosen: the one that makes fewer requests, retains less data, and fails earlier.

---

## ADR-001: Pre-Emptive Rate Limiting via Token Bucket

- **Context:** APIs enforce rate limits through headers and status codes (429/403). Relying only on reactive backoff (waiting until a 429 occurs) can lead to temporary IP bans, account flags, and quota exhaustion.
- **Decision:** Implement an in-memory Token Bucket governor (`Governor`) configured directly from `policy.rate_limit`. The governor blocks *before* making the HTTP request (`acquire()`). In addition, response headers (`Retry-After`, `x-ratelimit-remaining`, `x-ratelimit-reset`, and Stack Exchange `backoff`) dynamically adjust bucket capacity and sleep intervals.
- **Conservative Principle:** Request pacing is proactive rather than reactive, minimizing API stress and avoiding rate-limit violations.

---

## ADR-002: Disk Persistence of Daily Rate Quotas

- **Context:** A collection process may be interrupted and resumed across different processes on the same day. Without persistent state, daily quota counters would reset to zero, risking quota breaches.
- **Decision:** The `Governor` persists its request count and timestamp to `data/.governor_state.json`. When instantiated, it restores today's count if within the same UTC day, or rolls over if the date has changed.
- **Conservative Principle:** Resuming a run never exceeds daily quotas declared by providers (e.g. Stack Exchange 300 req/day without key, 10,000 with key).

---

## ADR-003: GitHub Search Partitioning under 1,000 Results Cap

- **Context:** GitHub's Search API enforces an absolute limit of 1,000 results per query (10 pages of 100 items). Queries returning >1,000 items silently truncate items beyond 1,000.
- **Decision:** Implement a recursive time-window partitioning algorithm in `partition.py`. If `estimate()` indicates `total_count > 1000`, divide the date window `[since, until]` in half until each partition fits within 1,000 results. If a 1-day interval still exceeds 1,000, split across secondary axes (`language:`, then `stars:a..b`). If no further subdivision is possible, mark `truncated=True` and log estimated loss.
- **Conservative Principle:** Prioritize precision and completeness over speed; detect and explicitly flag sample truncation in the run manifest.

---

## ADR-004: dev.to Tag-Based Retrieval with Local Keyword Matching

- **Context:** dev.to's public API does not offer full-text search endpoints; articles can only be retrieved by `tag`.
- **Decision:** Mark `policy.supports_full_text_search = False`. The adapter queries by configured tags and applies local keyword filtering (`match_terms`) against article titles and descriptions before yielding raw items. The manifest explicitly records that selection was performed locally.
- **Conservative Principle:** Does not invent unapproved query parameters; collects by published tag boundaries and discards non-matching items early.

---

## ADR-005: Stack Exchange Gzip Encoding and 1 req/min Pacing

- **Context:** Stack Exchange API v2.3 strictly requires `Accept-Encoding: gzip` (returning uncompressed responses is not supported) and enforces strict backoff rules. Rapid requests can result in automatic IP throttling.
- **Decision:** The adapter sends `Accept-Encoding: gzip` on every request and sets default rate limiting to 1 request per 60 seconds. Responses are inspected for the `backoff` field, which immediately pauses the governor if present.
- **Conservative Principle:** Respects SE's caching architecture and honors platform backoff directives unconditionally.

---

## ADR-006: Reddit Strict OAuth2 Authentication

- **Context:** Reddit disallows unauthenticated scraping and anonymous search API access. An explicit descriptive `User-Agent` is mandatory per platform guidelines.
- **Decision:** Reddit adapter requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT`. In their absence, `available()` returns `UNSUPPORTED`. No fallbacks to unauthenticated web scraping are permitted (§2.1).
- **Conservative Principle:** Fails early during `validate` and `plan` when credentials are not configured, preventing unexpected execution failures.

---

## ADR-007: Medium via RSS and Truncation Transparency

- **Context:** Medium feeds (`/feed/tag/...`) return only the ~10 most recent posts. Historical temporal queries are impossible via RSS.
- **Decision:** The RSS adapter collects only current feeds, marks `policy.supports_date_filter = False`, and sets `truncated: true` and `historical_coverage: false` in the run manifest for RSS sources.
- **Conservative Principle:** Never attempts web scraping or unofficial endpoints to obtain past Medium stories. Document the sampling bias transparently in research artifacts.

---

## ADR-008: Permanent Exclusion of LinkedIn

- **Context:** LinkedIn does not provide a public search API for user posts/articles. Content research on third-party user data is not an approved use case, and scraping violates the User Agreement.
- **Decision:** Implement `LinkedInAdapter` permanently returning `AvailabilityStatus.UNSUPPORTED` with a detailed explanation. Its `search()` method unconditionally raises `SourceUnsupportedError`.
- **Conservative Principle:** Complete refusal to circumvent platform constraints, maintaining legal, ethical, and academic research integrity (§2.2).

---

## ADR-009: Gated Status for Discord and X / Twitter

- **Context:** Discord bot access to message content requires privileged intents, guild installation, and administrator consent. X (Twitter) search API requires paid tiers that vary frequently in pricing and limits.
- **Decision:** In v0, both adapters remain gated:
  - Discord returns `UNSUPPORTED` unless `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_IDS` are set, and documentation notes the requirement for prior ethical review.
  - X/Twitter returns `UNSUPPORTED` without `X_BEARER_TOKEN` and `DEGRADED` with a token, instructing researchers to verify their specific tier's historical search window.
- **Conservative Principle:** Avoid hardcoding assumed tier quotas or harvesting chat messages without administrative and ethical clearance.

---

## ADR-010: Redistribution Policy Enforcement on Export

- **Context:** Different sources permit different redistribution terms:
  - Stack Exchange content is licensed under CC BY-SA (`full_text_with_attribution`).
  - GitHub repositories, Dev.to articles, Reddit posts, Hugging Face cards have varied third-party copyright licenses (`metadata_only`).
- **Decision:**
  - When `--include-body` is passed to `msrkit export`, the CLI inspects all sources present in the items. If any source has `policy.redistribution == "metadata_only"`, export terminates immediately with an error (exit code 1) before creating files.
  - When `--include-body` is omitted (default), body content is stripped from JSONL and CSV exports, and omitted from DuckDB tables and embedded JSON.
- **Conservative Principle:** Protects researchers from unintentional redistribution of copyrighted material.

---

## ADR-011: Post-Normalization Term Matching & Context Windows

- **Context:** Source adapters normalize raw JSON responses into canonical `Item` instances. However, raw responses do not carry the research protocol's search terms.
- **Decision:**
  - `adapter.normalize(raw, terms=...)` accepts optional terms.
  - Both `run` and `normalize` commands enforce post-normalization term matching using `match_terms()` against item title, body, tags, and path.
  - Matches use case-insensitive exact phrase matching with word boundaries, generating a ±40 token context window.
  - Items with zero matches are preserved (not dropped) to record retrieval precision/noise statistics for empirical evaluation.
- **Conservative Principle:** Deterministic keyword detection without non-reproducible ML heuristics or opaque classification models.

---

## ADR-012: Deduplication Strategy (Canonical URL & Content Hash)

- **Context:** Cross-platform literature mining often produces duplicates (e.g. shared articles, mirror posts, cross-posted questions).
- **Decision:** Two-pass deterministic deduplication:
  1. **URL canonicalization:** lowercase scheme and host, stripping tracking query parameters (`utm_*`, `ref`, `source`, `fbclid`, etc.), stripping trailing slashes on non-root paths, and sorting remaining query parameters.
  2. **Content hashing (with strict entity protection):** SHA-256 over normalized `title + " " + body` (collapsed whitespace).
     - **Safety rule 1 (Mandatory non-empty body):** Content hashing is only executed when `body` is present and non-empty (`has_body = bool(item.body and item.body.strip())`).
     - **Safety rule 2 (Code artifact exclusion):** Code files (`ItemKind.CODE`) are strictly excluded from content hashing pass (`item.kind != ItemKind.CODE`).
     - **Rationale:** Code search items (which share common filenames like `test_rag.py`, `eval.py`) and title-only link posts rely exclusively on canonical URL deduplication. This completely prevents false-positive mergers between distinct code files across different repositories or distinct articles sharing generic headlines.
  - MinHash/LSH near-duplicate detection is deferred to v1.
- **Conservative Principle:** Deterministic, reproducible, and verifiable deduplication with zero false-positive entity mergers.

---

## ADR-013: Windows Console UTF-8 Stream Reconfiguration

- **Context:** On Windows PowerShell and Command Prompt environments using legacy codepages (e.g. `cp1252`), Rich terminal formatting using Unicode symbols (such as checkmarks `✓` and crosses `✗`) triggers `UnicodeEncodeError`.
- **Decision:** In `cli.py`, `sys.stdout` and `sys.stderr` are reconfigured to UTF-8 with `errors="replace"` if running on `win32`.
- **Conservative Principle:** Ensures rock-solid CLI execution on any developer workstation without requiring external shell adjustments.
