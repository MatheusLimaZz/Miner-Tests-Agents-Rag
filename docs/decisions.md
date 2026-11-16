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
- **Decision:** In `msrkit/cli/__init__.py`, `sys.stdout` and `sys.stderr` are reconfigured to UTF-8 with `errors="replace"` if running on `win32`.
- **Conservative Principle:** Ensures rock-solid CLI execution on any developer workstation without requiring external shell adjustments.

---

## ADR-014: Reddit Excluded from Study E2 (Responsible Builder Policy)

- **Context:** Since November 2025, Reddit closed self-service creation of OAuth apps. Under the Responsible Builder Policy every new credential requires manual prior approval, with no deadline or guarantee; researchers are routed to the Reddit for Researchers program. Pushshift remains restricted to moderators.
- **Decision:** Reddit is disabled in `protocols/v0_rag_agents_testing.yaml` and excluded from Study E2 (Protocol v2, §6.1). The adapter is kept unchanged so it can be reactivated if an approved credential is obtained before collection, and for reuse in other studies. Third-party dumps (Academic Torrents) are not used because their terms status is uncertain.
- **Conservative Principle:** Never depend on an access path that the platform does not grant through its official process.

---

## ADR-015: Source Feasibility Tracked Against Protocol E2 v2

- **Context:** Access conditions change faster than the code (X moved to pay-per-use, Google Custom Search closes on 2027-01-01, Bing Web Search was retired on 2025-08-11).
- **Decision:** `docs/sources.md` holds the feasibility table (viable / conditional / infeasible) with the verification date and official links. It must be re-verified before each collection run. Execution conditions required by the protocol are tracked in `docs/PLANO.md`.
- **Conservative Principle:** A source's status is a dated claim backed by official documentation, not an assumption baked into code.

---

## ADR-016: Flexible Term Matching and Audited Local Discards (supersedes the exact-phrase rule of ADR-011)

- **Context:** Exact-phrase matching dropped relevant items in sources filtered locally (dev.to, RSS): "Evaluating RAG pipelines" did not match "evaluate RAG", and "LLM-as-a-judge" did not match "LLM as a judge". Discards were silent, breaking PRISMA auditability (Protocol E2 v2, §6.5 and §11).
- **Decision:** `match_terms` defaults to a deterministic `flexible` mode: each word of a term tolerates one inflectional suffix (light stem + `\w*`) and words may be separated by spaces, hyphens, underscores or slashes. Words shorter than four characters (acronyms such as RAG, LLM) stay exact, allowing only a plural "s". Terms with symbols (C++, .NET) keep exact boundaries. `mode="exact"` remains available. Items dropped by a local filter are written to `data/runs/<run_id>/discarded.jsonl` with the reason, and counted in the manifest (`discarded`).
- **Conservative Principle:** Recall losses are made visible and auditable; matching remains rule-based and reproducible. Word order is still significant ("testing the agent" does not match "agent testing").

---

## ADR-017: Stack Exchange `tagged` Uses OR Semantics (supersedes the AND assumption)

- **Context:** The adapter assumed that `;` in `tagged` meant AND and therefore never sent tags together with search terms. The official documentation of `/search/advanced` states the opposite: `tagged` is "a semicolon delimited list of tags, of which at least one will be present on all returned questions".
- **Decision:** Each (site, term) request carries the full tag list joined by `;` (OR), as in Protocol E2 v2 §7.3. The `tagged_mode: and` option was removed, since the endpoint does not offer AND. Sites follow the protocol: stackoverflow, softwareengineering, sqa, datascience, ai.
- **Verification:** the pilot compares, for one term, the result count with the tag list against the union of single-tag requests; a mismatch reopens this ADR.

---

## ADR-018: Per-Language Lexicons and Language Stratum

- **Context:** Protocol E2 accepts English and Portuguese items (criterion I4) and reports a Portuguese exploratory stratum, but the protocol had only English terms and the `languages` field was unused.
- **Decision:** `terms_by_language` adds lexicons per language next to the base `terms` (English); every key must be declared in `languages`. Queries search all lexicons; each `TermHit` carries the `lang` of its lexicon, and the CSV export adds `matched_languages`. Flexible matching folds accents (positions preserved, so context windows keep the original text) and strips Portuguese `-ção/-ções` suffixes. `pt.stackoverflow` is added to the Stack Exchange sites.
- **Conservative Principle:** The stratum is derived from which lexicon matched — a reproducible rule — rather than from automatic language detection.

---

## ADR-019: Opt-in Full-Text Retrieval of Linked Pages (`msrkit fetch`)

- **Context:** Hacker News and RSS items carry only a link and, at most, a summary, but extraction (Protocol E2 v2, §12) codes oracle types and failure modes from the article text. The linked pages are ordinary public web pages, not APIs.
- **Decision:** A separate, opt-in command `msrkit fetch` downloads the linked page for items of a run (by default only items with matched terms, from `hackernews` and `rss`). It honors robots.txt for the msrkit user agent, treats an unreadable robots.txt (5xx or network error) as disallowed, keeps a minimum per-host interval (5 s by default), processes only HTML up to 2 MB, and extracts visible text with the standard library. Results, including refusals, go to `data/fulltext/<run_id>.jsonl`; the command resumes without refetching. **Full text is never exported**: `export` does not read this directory.
- **Conservative Principle:** Collection through official APIs is unchanged; reading linked pages is explicit, rate-limited, robots-aware, local-only and auditable (every refusal is recorded with its reason).

---

## ADR-020: Literal Per-Source Queries (`queries:`)

- **Context:** Protocol E2 v2 §7.3 specifies source-specific searches that a global term list cannot express: GitHub code search for imports and config files (N2), workflow files (N3), issues of anchor tools.
- **Decision:** Each source in `protocol.yaml` may declare `queries:` — literal strings in the source's own search syntax, with optional `kind`, `label` and target `evidence` level — run in addition to the terms (or alone, with `use_terms: false`). Adapters declare `supports_raw_queries`; `validate` rejects `queries:` on sources without a search syntax (dev.to, RSS). GitHub keeps the literal string untouched except for the `created:` window used by partitioning (not added to code search or when the query sets its own `created:`).
- **Conservative Principle:** The exact string sent to the API is the one written in the protocol, so it is recorded verbatim in the manifest and reproducible.

---

## ADR-021: Versioned Gazetteer

- **Context:** Protocol E2 v2 anchors search and extraction in a gazetteer of known tools and methods (§7.2), with aliases and disambiguation rules (§12.2), kept outside the protocol document because it changes faster.
- **Decision:** `protocols/gazetteer.yaml`, named by `gazetteer:` in the protocol and versioned with it. Tools carry canonical id, aliases, family (Annex A), target system (`rag`/`agente`), and the structural signals used for N2/N3 (Python imports, dependency names, config files, CLI commands) plus their own repositories. Methods carry aliases and their usual rung on the oracle ladder. Ids and aliases must be unique across entries. Tools marked `ambiguous` only count with lexicon context or a structural signal; frameworks of the system under test are listed with `anchor: false`. Query templates `{anchor}` and `{repo}` expand over anchor tools (optionally by family).
- **Note:** repositories and package names in the seed must be checked during the pilot; the open discovery pass (§7.6) exists because any gazetteer biases what is found.

---

## ADR-022: Concept Groups and System Label

- **Context:** RQ5 compares RAG and agentic systems, and §3.1 lets a hybrid item carry both labels. Items carried no system label.
- **Decision:** The protocol declares `concepts:` — named lexicons (here `rag`, `agente`, `teste`). After normalization every item gets `concepts`, the list of groups whose lexicon matches (flexible matching, ADR-016), exported as a CSV column. The E2 system label is `concepts ∩ {rag, agente}`. Optional `concept_queries` adds query terms built as the product of concept lexicons (e.g. system × testing). The mechanism is domain-agnostic: another study defines other groups.
- **Conservative Principle:** The label is an automatic pre-classification by lexicon, reproducible and auditable; manual coding (Annex A) remains the reference.

---

## ADR-023: Evidence-Level Extraction Rules

- **Context:** §3.4 separates mention (N1), declared use (N2) and sustained adoption (N3), and every reported count must state its level.
- **Decision:** `msrkit extract` applies the gazetteer. N1: tool or method name in title, body or tags (tool names in exact mode, methods in flexible mode). N2: a code search hit whose literal query is an import of the tool, a tool config file, or a tool package in a root dependency manifest. N3: the tool invoked in a CI workflow **and** the repository has commits in ≥2 distinct months of the window **and** ≥2 contributors; a CI invocation without both thresholds counts as N2. Methods are N1 only. Summaries count each unit (a repository, or an item outside GitHub) once per entry at its highest level, so N1/N2/N3 counts are disjoint. Every detection keeps its signal, location and an evidence excerpt of at most 300 characters.
- **Limit:** a code search hit is trusted to contain its query string; precision is measured on a stratified sample (§12.3, step V2).

---

## ADR-024: Screening Sheets with Per-Coder Decisions

- **Context:** Screening (§8, §11) is manual and must be auditable per item, with independent double coding (§15).
- **Decision:** Eligibility criteria live in the protocol (`screening.inclusion`/`exclusion`, `batch_size`), so the workflow is study-agnostic. `msrkit screen export` writes a CSV sheet (`;`, UTF-8 BOM) in batches with one column per criterion, `decision`, `reason`, `coder`, `notes`, skipping items the coder already decided. `msrkit screen import` validates the whole sheet before recording anything (unknown item, invalid decision, missing coder, *include* with an exclusion criterion marked, *exclude* without criterion or reason) and appends one record per (item, coder) with the sheet's SHA-256 to `data/screening/<run>/decisions.jsonl`. The latest decision per coder wins; the final decision per item is the agreed one, or `uncertain` when coders disagree (to be resolved by a third coder).

---

## ADR-025: Per-Partition Checkpoint and Exact Resume

- **Context:** The manifest was written only when a run finished, so an interrupted run could not be resumed at all, and resume skipped whole sources. RNF3 requires resuming without loss or duplication.
- **Decision:** Each partition has a stable key (`query_key`: source, kind, literal flag, label, terms, window, extra — not position or limit), used as the raw-storage partition name and item `provenance.partition`. The partition plan is frozen in the manifest (`SourceManifestEntry.planned`) before collecting, and the manifest is saved after every partition with `key` and `completed`. `--resume` reuses the frozen plan (no re-estimation), skips completed partitions, and first deletes the raw file, items and manifest entries of incomplete ones, which are then collected from scratch.

---

## ADR-026: Near-Duplicate Pass (MinHash/LSH)

- **Context:** Cross-posted questions, mirrored READMEs and reposted articles survive URL and content-hash deduplication because a single character differs, inflating counts (RF5, §11).
- **Decision:** `msrkit dedupe` runs a third pass after URL and exact-content passes: MinHash signatures (128 permutations, word 5-shingles, blake2b with a fixed seed) and LSH (32 bands) propose candidate pairs, kept as duplicates when the estimated Jaccard similarity is ≥ `--near-threshold` (default 0.85; `0` disables). Texts with fewer than 30 tokens are skipped, since short titles collide by chance. The earliest-collected item of a group is kept. Counts per reason (url, content, near) and every removed pair with its similarity go to `dedupe_report.json`, which feeds the PRISMA flow (step T6).
- **Calibration:** the threshold is a pilot parameter; the report's similarity values allow checking borderline pairs manually.

---

## ADR-027: Relevance-Ordered Screening in Fixed Batches

- **Context:** §11 asks screening to start with the most informative items and proceed in fixed batches, so pilot calibration and double coding compare the same sets.
- **Decision:** `msrkit screen export` ranks the whole run by an additive, explainable score: 1 point per distinct matched term (max 5), +2 for a system concept (`rag`/`agente`), +2 for the testing concept, +1/+3/+5 for the best evidence level detected by `msrkit extract` (N1/N2/N3), +1 each for `has_tests` and `has_ci`. Ties break by item id. Batches (`screening.batch_size`) are cut on the full ranking before filtering pending items, so an item keeps its batch across coders and re-exports; `--batch` exports selected batches and `--order collected` restores collection order. The sheet shows `rank`, `score` and `evidence`.
- **Conservative Principle:** ranking orders the work only; no item is excluded by score.

---

## ADR-028: Declarative Extraction Form with Separate Suggestions

- **Context:** Annex A defines the per-item extraction form and §9 the gray-literature quality checklist. Both are coded manually, double-coded on a sample (§15), and must use the same identifiers as extraction and analysis.
- **Decision:** The protocol declares the form in `coding.fields` (types `enum`, `multi`, `text`, `bool`; `required`; `max_length`) and the failure-mode catalog in `coding.failure_modes` (FP1–FP13, AF1–AF12). A field's allowed values may come from the gazetteer (`vocabulary: tools|methods`) or the catalog (`failure_modes`). `msrkit coding export` writes the included items (by final screening decision) with read-only `auto_*` columns — system label, best evidence level, detected tools and methods, evidence excerpt, engagement and maintenance signals. `msrkit coding import` validates the whole sheet (unknown value, length, required field, yes/no) before appending one record per (item, coder) to `data/coding/<run>/codings.jsonl`. Values are matched case- and accent-insensitively and stored in their canonical form.
- **Conservative Principle:** suggestions are copied into the form only with `--prefill`. The default keeps coders independent, so agreement (step T5) measures the coders, not the extractor. Emergent failure modes go in the open field `modo_falha_relatado` (§5.2), not in the closed catalog.

---

## ADR-029: Cohen's κ per Dimension over a Stratified Double-Coding Sample

- **Context:** §15 requires independent double coding of ≥20% of eligible items and Cohen's κ per dimension (system, evidence level, failure mode, oracle type), with disagreements resolved by a third coder.
- **Decision:** `msrkit coding sample` draws ⌈rate·n⌉ included items per source (at least one), with a recorded seed, into `double_sample.json`; `coding export --double-sample` restricts the sheet to it. `msrkit agreement` computes κ for every coder pair over the items both coded: the screening decision plus the fields listed in `coding.agreement` (or every categorical field with `--all-fields`). A blank `enum`/`bool` is its own category. For `multi` fields, each (item, value) is a binary decision over the values either coder used; κ is pooled over them and also reported per value. Text fields have no κ. Results go to `agreement.json`, with Landis & Koch bands, and `disagreements.csv` lists one row per item and dimension for the third coder.
- **Limit:** κ is undefined (reported as "—") when both coders used a single identical category; the percent agreement is still reported.

---

## ADR-030: PRISMA Flow Derived Only from Run Records

- **Context:** RF8 requires a PRISMA flow. Hand-counted flows drift from the data and cannot be audited.
- **Decision:** `msrkit prisma` derives every number from files the run already produces:
  - identification per source: items plus manifest `discarded`;
  - an estimate of records not retrieved: `estimated_total` minus what was obtained, over queries truncated by a source cap;
  - local-filter removals by reason, from `discarded.jsonl`; the manifest total is authoritative, and any gap is reported as "unspecified";
  - duplicates by pass, from `dedupe_report.json`;
  - screening outcomes, from the final decisions over the screening pool, with exclusion reasons being the E-criteria marked by the excluding coders;
  - included items per source and coded items.

  The output is `data/reports/<run>/prisma.json` and `prisma.md`, which has a Mermaid diagram.
- **Conservative Principle:** a stage without input (dedupe not run) is shown as "n/a", never as zero. Records not retrieved are labelled as an estimate.

---

## ADR-031: Precision on a Stratified Detection Sample

- **Context:** §12.3 requires the extractor's precision to be measured. Detections are dominated by N1 text mentions, so a simple random sample would barely see N2/N3 or the smaller sources.
- **Decision:** `msrkit precision sample` stratifies detections by source × evidence level. It allocates the sample (default 200) proportionally, with at least one per stratum and largest remainders for the rest, and draws it with a recorded seed. The sheet shows each detection's stable key, stratum, evidence and item, plus a `correct` column. `msrkit precision score` reads the judged sheet. It reports precision per stratum and overall, and within each level. Both estimates weight strata by their population size (Σ Wₕ·pₕ), so over-sampled small strata do not bias them. A Wilson 95% interval on the sample is also reported.
- **Limit:** the Wilson interval ignores the stratified design (it is conservative when strata are homogeneous). Judges should record doubtful cases in `note`.

---

## ADR-032: Offline Contract Tests on Recorded Cassettes

- **Context:** §10.2 asks for contract tests that catch API changes. Unit tests use hand-written mocks, which drift from the real APIs silently.
- **Decision:** `tests/test_cassettes.py` replays vcrpy cassettes from `tests/cassettes/` with record mode `none`. Any request not in the cassette fails, so changes in how an adapter calls its API are caught. For each search adapter (GitHub, Stack Exchange, Hacker News, dev.to, Hugging Face), every recorded result must keep the payload keys its normalizer uses, normalize into a valid Item, and yield the same id twice. A separate case covers `msrkit enrich` on this project's own repository. Recording happens only with `MSRKIT_RECORD=1` (mode `once`). Auth headers, key/token parameters and cookies are filtered, and `test_no_secrets_in_cassettes` scans every cassette for tokens. Missing cassettes are skipped with the recording command.
- **Status:** the enrichment cassette is recorded. The search cassettes must be recorded on a machine with open network access, because the development environment only reaches this repository.

---

## ADR-033: CLI Split into a Package by Study Stage

- **Context:** `cli.py` had grown past 2,600 lines with 15 commands and 3 command groups, which made review and navigation slow.
- **Decision:** `msrkit.cli` is a package: `collect`, `evidence`, `review`, `validation`, `corpus` and `menu` hold the commands, and `_common` holds the shared helpers. `app`, `DATA_DIR` and `DEFAULT_PROTOCOL` stay in `msrkit.cli`, and command modules read them at call time (`_cli.DATA_DIR`), so the entry point (`msrkit.cli:app`) and every override (tests monkeypatch `msrkit.cli.DATA_DIR`) keep working. Registration order, and so the `--help` order, is fixed explicitly. Behavior is unchanged: the split was done mechanically from the syntax tree, and the full test suite passes unchanged.

---

## ADR-034: Strict Typing and Formatting Enforced in CI

- **Context:** mypy was configured as strict but never ran in CI. The code had accumulated errors: untyped registries, `str` literals where enums were expected, stale `type: ignore` comments, and a CLI import cycle that hid the type of `app`. Formatting was not checked.
- **Decision:** CI runs `ruff format --check src/ tests/` and `mypy src/` (strict) before the tests. Fixes made:
  - adapter policies use `RedistributionPolicy`;
  - the registry is typed `type[BaseAdapter]`;
  - `BaseAdapter.normalize` declares `terms`;
  - the DuckDB connection is `Any`, since the library is untyped;
  - `app` and `DEFAULT_PROTOCOL` moved to `msrkit.cli._common` to break the import cycle;
  - stale ignores were removed;
  - `types-PyYAML` added to dev dependencies, and `feedparser`/`duckdb` declared as untyped imports.

  Tests are linted and formatted but not type-checked.

---

## ADR-035: Analyses over Units, with an Explicit Basis

- **Context:** A1–A6 (§14) count tools, methods and failure modes. The same repository appears in many items (repo, code hits, issues), and the data comes either from automatic extraction or from manual coding.
- **Decision:** Analyses count *units*: a GitHub repository with all its items merged, or a single item elsewhere. Every analysis takes `--basis detections|coding`, and the basis is part of each output file name. On the coding basis, several coders are combined by consensus. An `enum` value counts only when all coders agree, and a `multi` field keeps the intersection. Disputed values stay out until a third coder resolves them, so the reported numbers are conservative. A1/A2 (`msrkit analyze frequency`) reports units per tool and method. For tools the counts are broken down by best evidence level (disjoint N1/N2/N3); for tools and methods they are also given per system label, where a hybrid unit counts in both, and per source. Co-occurrence within units is reported with Jaccard and lift.

---

## ADR-036: Failure-Mode Coverage Reports Absence

- **Context:** A3 asks which failure modes (FP1–FP13, AF1–AF12) the tools and methods address, for RAG and for agents. §5.2 treats the agent model as a seed, where a mode with no occurrences is a finding.
- **Decision:** `msrkit analyze coverage` runs on the coding basis only, because failure modes are coded manually. It writes:
  - a long table (mode × entry × system, in units);
  - one wide matrix per system (`rag`, `agente`, all), with columns ordered by total;
  - a per-mode summary in catalog order: units, rag/agente units, distinct tools and methods, and the most frequent entries.

  Catalog modes nobody addresses are listed as not covered. Coded modes outside the catalog are appended as "(not in catalog)", so emergent categories stay visible. Units without a system label are kept as "unlabelled".

---

## ADR-037: Oracle Ladder Distribution and an Explicit Test of H2

- **Context:** A4 places methods on the oracle ladder (§5.3) and tests H2: the large majority of oracles are atomic, even for stochastic systems.
- **Decision:** `msrkit analyze oracles` counts units per rung for all units, RAG and agents, with each rung's share among the units that have one. On the coding basis the rung is the coded `tipo_oraculo`. On the detections basis each detected method contributes its typical rung from the gazetteer, which is an approximation. H2 needs coded `agregacao`. Among units coded `atomico` or `agregado`, it reports:
  - the share of atomic oracles, with a Wilson 95% CI;
  - a one-sided exact binomial p-value for "share > threshold" (default 0.5, `--threshold` to demand a stronger majority).

  H2 is "supported" when p < 0.05. Units coded `nao-identificado`, or not coded, are counted and reported, never dropped silently.

---

## ADR-038: Discovery Pass with LDA and k-means as an Optional Extra

- **Context:** A gazetteer-anchored miner mostly finds what it already knows. §7.6 requires an open discovery pass, and the report must state which fraction of the catalog came from it.
- **Decision:** `msrkit analyze topics` runs over the text of the included items (`--status all` for every item). Fenced code is removed and bodies are truncated to 5,000 characters. It fits two models:
  - LDA on term counts;
  - k-means on TF-IDF.

  Both use unigrams and bigrams, English plus Portuguese stop words, `min_df` and a recorded seed. Top terms of topics and clusters that share no token with any gazetteer name, alias, id or package, or with the protocol lexicon, are flagged as candidates. Candidates are then inspected manually. Accepted ones enter the gazetteer with `origin: discovery`, and the command reports the catalog fraction by origin. Outputs: `topics.csv`, `clusters.csv` (with the items closest to each centroid), `topic_assignments.csv` and `discovery.json`. scikit-learn is an optional extra (`analysis`), installed in CI. The core miner does not depend on it.

---

## ADR-039: RAG × Agents Comparison and Saturation per Screening Batch

- **Context:** RQ5 asks what transfers from RAG to agents and what is new; A6 answers it. §11 asks to stop coding when new batches stop adding codes (saturation).
- **Decision:** `msrkit analyze compare` classifies every tool, method, failure mode and oracle rung by the systems of the units where it appears:
  - `transfers`: found in both RAG and agent units;
  - `rag-only`;
  - `agent-only`: new for agents.

  Hybrid units are counted in both systems and reported separately. Per kind it reports the Jaccard similarity of the RAG and agent catalogs and the *agent transfer rate*: the share of agent entries also seen in RAG units. Saturation uses the fixed screening batches (ADR-027), recomputed deterministically. A unit belongs to the earliest batch of its items, and each batch records the codes (tools, methods, failure modes) it adds. The run is saturated when the last `--window` batches each add at most `--tolerance` new codes. The curve goes to `saturation_<basis>.csv`.

---

## ADR-040: Zenodo Package with Redaction by Source Policy

- **Context:** §18.2 plans the protocol (D1), corpus (D3), gold set (D4) and catalog (D5) for Zenodo. §17 limits redistribution: full text only where the license allows it, no personal data beyond the public author handle (and that only when needed), and the code in each repository stays under that repository's license.
- **Decision:** `msrkit package` writes one zip containing:
  - `protocol/`, with the gazetteer;
  - `run/`: the manifest and discards;
  - `corpus/`: redacted items and the dedupe report;
  - `evidence/`: repository signals with file *paths* only, plus detections;
  - `review/`: decisions, codings, the double-coding sample and agreement results;
  - `validation/`: the gold set and precision/recall records;
  - `reports/`.

  It also adds a generated README, `.zenodo.json` (dataset, CC BY 4.0 for the package's own data, creators from `--creator`), `package_manifest.json` (version, git commit, Python, protocol SHA-256, per-file hashes) and `SHA256SUMS`. Redaction follows each adapter's `redistribution` policy. `metadata_only` items lose `body` and the match contexts but keep `body_hash`. Author handles are replaced by salted pseudonyms, whose salt is not published, unless `--keep-authors` is given. Raw API responses are never packaged. Zip entries carry a fixed timestamp.

---

## ADR-041: Search Parameters and Source Access Groups in `.env`

- **Context:** The researchers want to set the lexicon, the temporal window and the sources in one place, with the sources separated by how they can be accessed: ready to use, needing a manual credential, paid. `.env` was read only by Docker (`env_file`), never by native runs.
- **Decision:**
  - The CLI loads `./.env` before every command. Variables already in the environment win, so CI secrets and shell exports are never overwritten.
  - `MSRKIT_WINDOW_SINCE`/`UNTIL`, `MSRKIT_TERMS` and `MSRKIT_TERMS_<LANG>` (`;`-separated) override the protocol. Empty variables leave the protocol's values.
  - Four variables classify the sources: `MSRKIT_SOURCES_PUBLIC`, `MSRKIT_SOURCES_MANUAL`, `MSRKIT_SOURCES_PAID` and `MSRKIT_SOURCES_UNAVAILABLE`. When any of them is set, exactly the public and manual sources are enabled. A source must be declared in the protocol and belong to a single group, and paid and unavailable sources never run.
  - Each run manifest records `effective_protocol` (window, terms, enabled sources, groups), `env_overrides` (variables applied) and `run_options`. `msrkit validate` warns when `.env` diverges from the protocol file.
  - `.env.example` carries the E2 values, equal to the protocol, and a test keeps them equal.
- **Conservative Principle:** the versioned protocol stays the reference for the registered study. `.env` is a convenience for running and exploring, and nothing it changes goes unrecorded.
- **Related:** `msrkit run --per-query-limit N` caps every query (term or partition), so a test sample spreads over the whole lexicon instead of exhausting the source limit on the first query. The cap is recorded as the truncation reason `item_limit`.

---

## ADR-042: Findings of the First Test Collection (Stack Exchange Tags, Per-Term Cap)

- **Context:** The first test collection ran on GitHub Actions (run 36498904192, 29/Sept/2026), sampling the public sources with at most 10 items per query. 740 records were identified and 224 were left for screening (Hacker News 204, dev.to 10, Hugging Face 10). Two sources misbehaved:
  - **Stack Exchange:** 180 requests (30 terms × 6 sites) all returned HTTP 200 and 0 items. Every request carried the full tag list `langchain;llamaindex;large-language-model;rag;openai-api` in `tagged`. The documentation describes the list as "at least one will be present", but no results for "LLM evaluation" on Stack Overflow since 2023 is what an AND of five tags gives. This is the check ADR-017 left for the pilot.
  - **Hugging Face (and dev.to):** each got a single query containing all 30 terms, so the per-query cap of 10 was used up by the first term.
- **Decision:**
  - The Stack Exchange adapter takes `tagged_mode`:
    - `all` sends the list in one request (the previous behavior, kept as the adapter default);
    - `any` makes an explicit OR with one request per tag;
    - `off` sends no tag filter.

    The E2 protocol uses `off`, because its terms are already specific and screening filters the rest. `any` becomes affordable with `STACKEXCHANGE_KEY` (10,000 req/day).
  - With `--per-query-limit`, queries of sources that search each term separately (`SourcePolicy.searches_each_term`) are split into one query per term. dev.to and RSS are fetched by tag or feed and filtered locally, so they keep one query: splitting them would page through every tag since 2023 once per term.
- **Evidence:** the second test collection used the same parameters with `tagged_mode: off` (run 36501012949). Stack Exchange went from 0 to 146 items in 146 requests (117 after deduplication), within the 300-request daily quota without a key. Hugging Face went from 10 items in one query to 123 items spread over 30 per-term queries. In total, 999 records were identified and 453 left for screening.

---

## ADR-043: Medium via RSS Only, Accumulated Daily

- **Context:** The researchers asked whether Medium's official API could replace RSS. It cannot. Per Medium's own API documentation, the API exposes only the authenticated user, their publications and contributors, and endpoints to create posts and upload images. It has no endpoint to read, list or search other people's posts, and Medium accepts no new integrations. Self-issued tokens are granted on request, by e-mail, for desktop integrations and plugins. Third-party "Medium APIs" scrape the site and are excluded by §17. The public feeds (`/feed/tag/<tag>`, `/feed/<publication>`) are the only official read access, and each returns only the ~10 latest posts.
- **Decision:** The protocol lists 15 feeds: nine Medium tags (RAG, retrieval-augmented generation, LLM evaluation and testing, LLM-as-a-judge, AI agents, agentic AI, LLMOps, AI evaluation), three Medium publications and three tool/ML blogs. `collect.yml` gains a daily schedule that collects RSS only. It restores `data/` from the previous run through the Actions cache and saves it again, then publishes `rss_acumulado.csv`, which is consolidated across runs and deduplicated. Each scheduled run extends `window.until` to its own date, because new posts are necessarily later than the data-cutoff; `env_overrides` in each manifest records this.
- **Consequence:** accumulated RSS items published after the E2 data-cutoff only count for E2 if the cutoff is moved to the end of the collection period. That is a protocol decision for the research team. Until then, the RSS stratum stays exploratory.

---

## ADR-044: Findings of the First Collection with GitHub

- **Context:** The first collection with the researchers' credentials ran as run 36515820085 (29/Sept/2026), capped at 10 items per query and 300 per source. Results: 1,304 records identified and 745 left for screening, 150 GitHub repositories enriched with no errors, and 316 detections. Stack Exchange ran with its key. Bluesky was skipped because its secret is not set.
- **Findings and decisions:**
  1. *GitHub cut short.* The 300-item source cap was reached after 51 of about 165 planned GitHub queries. The remaining queries include the code queries (N2) and all CI queries (`{anchor} path:.github/workflows`, N3), which never ran. Only one N3 detection appeared (garak). The collection workflow now leaves the source cap empty by default, so the per-query cap alone shapes the sample. The job timeout was raised to 240 minutes to fit the full plan.
  2. *Markup in the discovery pass.* LDA topics were dominated by HTML and entities (`quot`, `x2f`, `li`, `pre`, `code`), because Stack Exchange and Hacker News bodies are HTML. The discovery documents now drop code blocks (Markdown and HTML), tags, entities and URLs.
  3. *Empty documents in k-means.* Repositories known only by their name formed singleton clusters with arbitrary top terms. Documents under 15 words after cleaning are now left out of the discovery pass, and their number is reported (`skipped_short`).
  4. *Frameworks vs. test tools.* LangChain (40 units), LangGraph and LlamaIndex topped the tool frequency, but they are frameworks of the system under test (`framework-sut` in the gazetteer). They are mined through their own tests, and RQ1/RQ3 must not count them as test tools. The frequency output now shows each entry's gazetteer family.
- **Preliminary signal (automatic basis, before screening):**
  - The most frequent methods are LLM-as-a-judge (43 units), golden datasets (30), red teaming (30) and eval regression (26).
  - The most frequent test tools are Ragas (22), DeepEval (10) and LangSmith (6).
  - On the oracle ladder, the reference (55%) and pseudo-automatic (45%) rungs dominate.
  - 69% of the tools and 89% of the methods seen in agent units also appear in RAG units. Trajectory evaluation, SWE-bench, OpenAI Evals, Inspect, LLM Guard and Opik appear only in agent units.

  These are automatic detections, not coded data. They serve the pilot and do not answer the RQs.

---

## ADR-045: CI Workflow Code Hits as N3 Evidence, and Enrichment by Priority

- **Context:** The second collection with GitHub (run 36521583989) ran all 181 GitHub queries. That includes the CI queries `{anchor} path:.github/workflows language:YAML` from §7.3. Still, only one N3 detection appeared, for two reasons:
  - Extraction read code hits only for imports and config files, so a workflow file returned by a CI query produced no `ci` signal.
  - `msrkit enrich --limit 150` took the first 150 of 785 repositories in collection order. The repositories with CI hits, which need commit months and contributors to reach N3, were mostly left out.
- **Decision:**
  - A code hit whose path is under `.github/workflows/`, from a query naming a gazetteer tool, yields a `ci` detection at N2. That detection is promoted to N3 when the repository's enrichment shows sustained adoption (commits in ≥2 months and ≥2 contributors, ADR-023); `is_sustained` holds the rule.
  - `enrich` orders repositories by the evidence they can yield: CI hits first, then other code hits, then repositories found as such, then the rest.
  - The collection workflow enriches up to 300 repositories.
- **Second collection (automatic basis, before screening):**
  - 2,255 records identified and 1,641 left for screening.
  - 118 duplicates removed, 15 of them near-duplicates.
  - 889 detections.
  - The catalog of codes saturated at batch 65 of 66.
  - The most frequent test tools are Ragas (50 units), DeepEval (32), LangSmith (20), Langfuse (13) and promptfoo (12).
  - The most frequent methods are LLM-as-a-judge (72), golden datasets (58), eval regression (44) and red teaming (43).
- **Enrichment rate:** Enrichment used the GitHub adapter's governor, which is tuned for the search API (30 requests/minute). The core REST API allows 5,000 requests/hour with a token. At the search rate, 300 repositories × ~12 requests took about two hours of the job. `enrich` now has its own governor (`github-core`, `CORE_RATE_LIMIT` = 4,500/hour, burst 10); 403/429 responses still back off.
- **Third collection (run 36528947297, automatic basis):**
  - 3,668 records identified, 496 filtered, 224 duplicates removed and 2,916 left for screening. GitHub ran 324 queries and returned 2,459 items on disk.
  - All 300 prioritized repositories were enriched without errors.
  - 1,890 detections. N3 rose from 1 to at least 97 (sum over the 25 entries listed). The tools with the most N3 detections are webarena, guardrails-ai and agentdojo (8 each), then tau-bench and garak (7 each).
  - Frameworks of the system under test (LangChain, LangGraph, LlamaIndex) and tracing tools (LangSmith, Langfuse) have no N3 detections: they are imported (N2), but no CI invocation of them was found. N3 therefore marks test tools that run in CI, as intended.
  - The catalog saturated at batch 116 of 117, with 50 codes.

---

## ADR-046: Article 2 Analysis Parameters and Conventions (Provisional)

- **Context:** The Article 2 protocol guide (results paper: how the community tests RAG and agent systems) asks that analysis decisions be recorded before they run on coded data (its rule 5). The authors have already seen preliminary detections, so every test on that corpus is exploratory. The values below are provisional: each one marked [DECIDIR] is an author decision and must be confirmed (or changed) here before the confirmatory run.
- **Decision (implemented in `analysis/artigo2.py`, which reads a `msrkit package` zip):**
  - **Corpus.** One official corpus, declared in the paper: the frozen run of Article 1 (step 4) or, while it does not exist, the d28a mining of the original repository, with its deviations listed. The test runs of this fork (a2a8 and earlier) are calibration only and never mixed in.
  - **Units and counting.** Units come from `msrkit.analysis.units_from_detections` (a GitHub repository with all its items, or one item elsewhere). A unit counts once per tool, at its best level (N3 > N2 > N1). A unit that is the tool's own repository (gazetteer `repos`) is set apart as *self* at every level and reported separately.
  - **Testing tools only.** The 8 `framework-sut` entries never enter the testing-tool rankings; they are reported as context (the stacks tests run on) and in tool × framework co-occurrence.
  - **System label (detections basis).** RAG × agents uses the unit's matched concepts (`rag`, `agente`), not the gazetteer `applies_to`; the paper says so.
  - **QP1.3 / P1.** Kendall τ-b between per-tool unit counts at N1 and at external N3 (also N2 × N3 and N1 × N2), over the testing tools with at least one unit. 95% CI by bootstrap over units (multinomial weights, 10,000 resamples, seed 20261031). P1 is read as supported when τ < 0.5 [DECIDIR].
  - **Co-occurrence.** Jaccard and lift over units, for pairs with at least 5 units together [DECIDIR].
  - **QP2.** Methods per system; oracle ladder from each method's typical rung (gazetteer `oracle`). This is an approximation until `tipo_oraculo` and `agregacao` are coded; H2 needs the coding basis.
  - **QP3 engagement.**
    - Items: discursive items (GitHub issues, Hacker News, Stack Exchange, dev.to, Hugging Face, Bluesky). GitHub repositories (stars) only with `--with-repos`; code hits and RSS have no engagement.
    - Primary metric per source as in the guide §4.4 (issues: reactions; HN and Stack Exchange: votes; dev.to and Bluesky: reactions; Hugging Face: likes).
    - Bug issues returned by the anchor queries (`repo:{repo} is:issue label:bug`) are set apart for the "pain" analysis, not compared with discourse.
    - Automated issues (dependency bumps, lock-file maintenance, vulnerability reports) are removed by title, since packages pseudonymize authors.
    - Minimum age 3 months between creation and collection [DECIDIR].
    - Normalization: percentile of the primary metric within each source × kind, average ranks for ties, E ∈ [0, 1]. Raw counts are never compared across sources.
    - Per tool and per method: reported when n ≥ 10 items [DECIDIR]; median E with a 95% bootstrap CI (10,000 resamples).
    - Kruskal–Wallis across tools, Dunn post hoc with tie correction and Holm adjustment, ε² and Cliff's δ. Items that mention several tools belong to several groups, so these tests are descriptive.
    - P2: Spearman ρ between median E and external N3 per tool; read as supported when |ρ| < 0.4 [DECIDIR]. Sensitivity: without listicles (items with ≥ 4 testing tools [DECIDIR]) and with items from non-truncated queries only.
    - Robustness: negative binomial on raw counts with tool and source × kind fixed effects and offset log(age); the ranking of tool effects is compared with the ranking of median E.
    - P3 (exploratory): items created in 2025–2026 labelled only `agente` or only `rag`; Cliff's δ on E and OLS `E ~ agente + log(age) + stratum` with HC3 errors, restricted to strata where both systems occur.
- **Consequences:** The script writes CSV files, `summary.json` and LaTeX (`macros.tex`, table bodies, the mention × adoption scatter). The paper takes every number from `macros.tex`, which records the package, run id, basis and parameters, so a changed decision is a re-run plus an update of this ADR. `--pending` writes placeholders so the paper compiles before the run.

---

## ADR-047: Mining Report After Each Collection

- **Context:** After a collection, what was mined could only be read from `manifest.json`, `msrkit stats` and the PRISMA flow. None of them shows, in one place, the time window, the sources and their status, the terms searched and the counts. The PRISMA flow also hides one gap: the manifest lists executed queries only, so planned queries that never ran disappear. In run d28a, 4 of 182 GitHub queries never ran after the 10,000-item cap, and its PRISMA reported nothing missing.
- **Decision:**
  - `msrkit report` writes `reports/<run>/relatorio_mineracao.md` and `.json`, in Portuguese, the team's working language. Sections: summary, time window, sources ("locais"), terms searched, quantities and warnings.
  - Identification, local-filter and duplicate counts come from `prisma.build_flow`, so the report and the PRISMA flow never disagree. Evidence (enrichment, N1/N2/N3 detections by unit via `extract.summarize`) is added when those files exist.
  - Warnings list planned − executed queries per source, failed or incomplete queries, `source_cap` truncation, sources without historical coverage, the sample cut by the item limit, unique items dated outside the window, a missing dedupe, a missing manifest and an unfinished run.
  - The Coleta workflow runs `msrkit report` after `msrkit prisma` and before `msrkit package`, so the report ships in the reproducibility package. The job summary shows the report of the latest run only; scheduled RSS runs restore earlier runs from the cache. If the pipeline stops early, the summary step still generates the report for whatever was collected.
- **Consequences:** Every collection, including the daily RSS one, leaves a readable record of what was mined. The report adds no new data: rerunning `msrkit report` on the same files gives the same output.
- **Revision after the first scheduled RSS run (run 2026-10-31T13-43-17Z-64b5):**
  - Sources with no executed query were listed with their availability ("OK: token configured"), which read as if they had been consulted. The reason now comes from `effective_protocol.enabled_sources` and `source_groups`: outside this run, paid, unavailable, disabled in the protocol, or enabled with no query run. Only the last one is a warning.
  - When `MSRKIT_WINDOW_*` replaces the window, the report reads the protocol file without overrides and states its window and how many unique items fall outside it. All 43 items of that run were published in September–October 2026, after the protocol's 2026-08-31 cutoff: the daily RSS stream cannot enter the E2 corpus without a recorded deviation.
  - With earlier runs in data/ (the scheduled job restores them from the cache), the report counts the unique items no earlier run had and the accumulated total. The run alone only shows within-run duplicates, which hides the day-to-day overlap of RSS feeds.
  - The PRISMA flow now also reports planned queries that were not executed, per source.
  - `protocol_sha256` normalizes CRLF to LF before hashing. The same protocol gave `e48faa103868` on a Windows checkout and `d755f05d757b` on the CI runner. Manifests hashed on Windows before this change carry the CRLF hash.

---

## ADR-048: The Official Collection Runs Locally

- **Context:** The Coleta workflow job has a 240-minute timeout. A collection at the official scale does not fit. Run d28a (100 items per query, every repository enriched) took about two hours of collection in Docker, plus the enrichment of 5,500 repositories.
- **Decision:**
  - `scripts/coleta_local.py` runs the workflow's steps locally or in the container, with no time limit and in the same order: validate, run, dedupe, enrich, extract, screening sheet, PRISMA, mining report, CSV, package, preliminary A1–A6 analyses and the precision sample. Every step after the collection is pinned to the run id it created.
  - The script sets the workflow's source groups. It blanks `MSRKIT_WINDOW_*` and `MSRKIT_TERMS*` in the child environment, so the window and lexicon come from the protocol file alone, as on the Actions runner, whatever the local `.env` holds. An empty variable is ignored by the overrides and blocks the `.env` value, since existing variables win.
  - GitHub requires `GITHUB_TOKEN` before anything starts. Without it the source runs at 60 requests/hour and without code search, which would take many hours.
  - Defaults: 100 items per query and every repository enriched through GraphQL (which falls back to REST when it fails).
  - Output goes to `data/` and a full log to `data/logs/`. A fatal step stops the run and prints `--retomar <run_id>`, which resumes the collection (`msrkit run --resume`) and reruns the later steps. Those steps are idempotent, and enrichment resumes from what is saved. Optional steps (analyses, precision sample, stats) do not stop the run.
  - On Windows the script keeps the system awake while it runs (`SetThreadExecutionState`).
- **Consequences:** The workflow stays for test samples and the daily RSS accumulation. Official runs are local, and the manifest records where and how they ran (`env_overrides`, `run_options`). `scripts/` is mounted in docker-compose so the container sees the script.
- **Revision after run 2026-11-02T19-14-30Z-d855:** The script no longer collects RSS. The feeds carry only the ~10 latest posts each, so every local run got 0 items inside the protocol window (2023-01-01 to 2026-08-31). RSS stays in the daily Actions accumulation, as monitoring outside the E2 corpus, and `--fontes` can still add it. The mining report lists it as outside the run.

---

## ADR-049: GitHub Issue Searches Collect Issues Only

- **Context:** GitHub's `/search/issues` now rejects, with 422, a query that does not say `is:issue` or `is:pull-request`. In run 2026-11-01T23-59-22Z-7837, all 72 term × language issue requests got that 422, and the adapter logged a warning and recorded each query as completed with 0 items, so neither the report nor the PRISMA flow showed the loss: 1,440 issues, against 3,795 `kind: issue` items in run d28a. Run d28a, on 2026-10-20, sent the same queries without the qualifier and received issues and pull requests mixed (2,405 issues, 1,390 PRs).
- **Decision:**
  - Issue searches collect issues only, following the protocol's `kind: issue` literally. The adapter appends `is:issue` to every issue query, term or literal, that does not already set `is:`/`type:` issue or PR. The anchor queries (`repo:{repo} is:issue label:bug`) already did.
  - A 422 no longer looks like an empty result. The adapter keeps going through the query's other languages and then raises `QueryRejectedError` with GitHub's message, so the query is recorded as not completed, the mining report lists it under failed queries, and `--resume` redoes it.
  - The GitHub adapter version goes to 0.2.0, so manifests show which runs used the new queries.
- **Consequences:** Pull requests leave the corpus. Runs with adapter 0.2.0 are not directly comparable with d28a on `kind: issue`: d28a's PRs (37% of its issue items) have no counterpart. A comparison should drop URLs containing `/pull/` from d28a first. Run 2026-11-01T23-59-22Z-7837 is superseded for GitHub issues and should be rerun.

---

## ADR-050: English-Only Lexicon

- **Context:** ADR-018 added a Portuguese lexicon (16 terms) as an exploratory stratum for criterion I4. It stayed small: 90 items in run d28a and 63 of 11,316 unique items in run 2026-11-02T19-14-30Z-d855, and 4 of its terms never matched. English is the working language of the field, and the team decided to search in English only.
- **Decision:** The protocol drops `terms_by_language.pt`, `pt` from `languages`, the `pt.stackoverflow` site, and the Portuguese concept terms ("geração aumentada por recuperação", "agente", "teste", "avaliação"). Criterion I4 becomes "English". The `terms_by_language` mechanism and the Portuguese inflection rules in `keywords.py` stay in the code for other studies.
- **Consequences:** The E2 corpus describes the community that publishes in English. Runs before this change are not comparable on the language stratum. The Paper 2 macro `ResItensPt` now counts 0.

---

## ADR-051: Literal Repository Queries Without OR Groups

- **Context:** The protocol's two literal repository queries were meant to find repositories about testing RAG and agentic systems: `RAG (eval OR test OR testing) in:name,description,readme stars:>5` and the same with `agentic`. On 2026-11-03, live counts showed that GitHub's repository search ignores the parenthesized OR group: each query returned exactly what `RAG` or `agentic` alone returns (27,662 and 175,235 repositories). The 100 repositories each one collected in run d855 were a sample of every RAG or agentic repository, not of the ones about testing. The code query `"tool_call" "assert" language:Python path:tests` had a similar problem: code search splits `tool_call` into two words, and the query matched 291,840 test files.
- **Decision:**
  - Each repository query becomes four, one word each, searched in name and description only: `eval`, `evaluation`, `test`, `testing`. The README match is dropped because it pulls in any repository that mentions both words anywhere (`RAG testing` in READMEs returns 14,863 repositories, `ollama/ollama` among the first).
  - The eight queries return 99 to 266 repositories for RAG and 394 to 1,489 for agentic (`stars:>5`, window 2023-01-01..2026-08-31). The RAG ones fit almost entirely in 100 items per query per year, once the queries run by year (PLANO, step O8); the agentic ones remain samples.
  - The tool-call query becomes two queries for names that only appear in tests of tool calls: `"expected_tool_calls"` (1,076 files) and `"assert_tool_call"` (463).
- **Consequences:** This is a recorded deviation from the queries of protocol §7.3. Repository items from runs before this change are not comparable on these labels. The repository term queries (`"RAG evaluation"`, `"agent testing"` and the rest, with `language:` and `stars:>=3`) are unchanged.

---

## ADR-052: dev.to Collected Whole (Census Sources)

- **Context:** The dev.to API has no text search, so the adapter reads tags page by page and keeps articles that match the lexicon, about 1% of them. In run d855 the source ran as a single query with the run's cap of 100 items and `max_pages: 10` per tag. Ten pages of 1,000 articles reach back only to about September 2026, so most of what was read fell after the window, and 93 of the 98 articles kept were from 2026. The `ai` tag adds 60,000 articles in September–October 2026 alone, so its ten pages never reached the window.
- **Decision:**
  - A source can be marked `census: true` in the protocol. The run's per-query cap (`--per-query-limit`) and the fair-share caps then do not apply to it; `--limit` and the protocol's per-source limits still do. The manifest records the census sources in `effective_protocol.census_sources`, and the mining report lists them next to the run options.
  - dev.to is a census source, with `max_pages: 40` over the tags `rag`, `llm`, `agents`, `llmops` and `testing`, which covers every page of each (November 2026). `ai` is dropped.
  - Expected cost: about 100 list requests and one body request per kept article, roughly 1,000 requests at 30/min, or about 30 minutes.
- **Consequences:** dev.to's share of the corpus reflects every matching article of five tags over the whole window, with no sampling. The local-filter discards grow accordingly (tens of thousands of `no_term_match` records), which the PRISMA flow counts as removed before screening. Articles tagged only `ai` are out of the corpus.

---

## ADR-053: Reproducibility of GitHub Code Search

- **Context:** Only 61% of run d855's GitHub code hits were also in run d28a, collected 13 days earlier, while Hacker News, Hugging Face and Stack Exchange matched 99–100%. Code search has no date qualifier, so its results cannot be pinned to the window. The adapter sent `sort=indexed&order=desc`, which the REST docs mark as closing down.
- **Measured on 2026-11-03:**
  - Re-running three code queries 24 h after d855 (`"from ragas import"`, `"import deepeval"`, `filename:promptfooconfig.yaml`) returned 100, 100 and 98 of the same 100 files. The search is stable from one day to the next and drifts over weeks as GitHub re-indexes repositories. Part of the d28a gap also comes from query changes between the runs, such as renamed gazetteer repositories.
  - `sort=indexed` and the default best-match order returned the same 100 files for the same three queries.
- **Decision:**
  - Code searches drop `sort`/`order` and use the default order (GitHub adapter 0.2.1). Repository and issue searches keep `sort=updated`.
  - With earlier runs in `data/`, the mining report adds a per-source table of the unique items an earlier run also had, so each collection measures how much each source repeats.
  - The study corpus is the frozen package of the official run (PLANO, O9), not a re-query. Paper 1 reports the drift as a reliability threat, with these numbers.
- **Consequences:** Two runs a few days apart give nearly the same code hits; runs weeks apart do not, and the per-source table shows by how much.

---

## ADR-054: Enrichment Reused Across Runs

- **Context:** Enrichment reads each repository's tree, workflows, manifests, contributors and commit months, and is bound by GitHub's 5,000 core requests per hour. In run d855 it took 6 h 38 min of the 7 h 23 min run, although 3,977 of its 5,260 repositories (76%) had been enriched by run 7837 the day before. Signals were reused only within a run, to resume it.
- **Decision:**
  - `msrkit enrich --reuse-days N` copies a repository's signals from another run in `data/enrich/` when that run was enriched with the same window and the same gazetteer config files, the signals have no error, and they were fetched at most N days ago. The newest copy wins. Off by default (`N = 0`).
  - A copied signal keeps its `fetched_at` and request hashes and gains `reused_from`, the run whose requests produced it. The mining report counts the reused repositories.
  - Each enrichment writes `data/enrich/<run>/meta.json` with its window and config files. For runs enriched before that file existed, the window comes from the manifest and the config files from the gazetteer of the protocol path the manifest names, which is the file as it is now.
  - `scripts/coleta_local.py` passes `--reuse-days 14`; `--reaproveitar-dias 0` turns it off.
- **Consequences:** A new run within two weeks of the last one enriches only the repositories it did not have; checked on d855, 5,257 of its 5,260 repositories are eligible for the next run. N3 signals depend on the window (commit months, which is closed and fixed) and on the repository's current state (workflows, contributors); 14 days bounds how stale that state can be, and `reused_from` keeps it traceable. Signals produced by an older version of the enrichment code are copied as they are; a change to that code should be released with `--reuse-days 0` for one run.

---

## ADR-055: Queries Stratified by Year

- **Context:** Each query keeps at most 100 items (`--per-query-limit`), and the sources return the newest first: GitHub sorts by `updated`, Stack Exchange by `creation`. In run d855, 2,339 of 3,579 GitHub issues (65%) and 93 of 98 dev.to articles were from 2026, and 84 queries stopped at the cap, often far below what the source held (issues for "LLM evaluation": 100 of 13,304). For Paper 2, which ranks tools and engagement, that sample leans toward what was popular in 2026.
- **Decision:**
  - With `sampling: {strata: year}` in the protocol, `msrkit run` and `msrkit plan` split every query whose source applies its dates into one query per calendar year of the window (2023, 2024, 2025 and 2026 up to August). Each stratum gets the run's per-query cap. Within a stratum, the source's own order still applies.
  - Split: GitHub repository and issue searches (`created:`), Hacker News and Stack Exchange. Not split: GitHub code search (no date qualifier), literal queries that set their own `created:`, Hugging Face (no date filter), and census sources (ADR-052), which have no cap to balance.
  - Before collecting a stratum, the run asks the source how many items the stratum matches and stores it as the query's `estimated_total` in the manifest: GitHub sums `total_count` over the query's languages, Stack Exchange sums `total` over its sites, Hacker News reads `nbHits`. Collected ÷ available gives each stratum's sampling fraction, so analyses can weight frequencies back to the population.
- **Consequences:** Every year of the window is represented, and the manifest holds what is needed to correct for unequal fractions. Weights matter for population-level frequencies; unweighted counts over-represent the older years. The counts cost one extra request per stratum and language or site: about 600 more GitHub search requests (~20 min at 30/min) and 400 more Stack Exchange requests (with a key, out of 10,000 per day). The corpus grows: GitHub queries go from 182 to 213 and from 182 to 384 partitions, with up to 100 items each, so a run at the same per-query cap collects more than d855.
- **Correction (2026-11-05):** The per-source cap was not revised with this decision. In run fb68, GitHub reached `max_items_per_source: 10000` after 320 of 384 partitions, so the free-term issue queries for 2024–2026 never ran and 83% of the issues came from the 24 anchor repositories. The cap is now 20,000; the full plan needs at most about 16,500 items at the 100-item per-query cap.

---

## ADR-056: Structural Evidence Is Read from the Artifact

- **Context:** The review of run 2026-11-06T01-19-19Z-015e found three ways in which N2 and N3 counted what was not there. All numbers below are from that run.
  1. *Issues carried the repository's signals.* Signals read by `msrkit enrich` were attached to the repository item or, without one, to "the first item of the repository", which could be an issue. 533 structural detections (521 dependencies, 6 CI, 6 config files) sat on 319 issues, so an issue with a question counted as a use of every package in the repository's `pyproject.toml`. The Article 2 engagement analysis groups items by their detections, so those issues entered the engagement of tools they never mention.
  2. *A search hit in a workflow file counted as a CI invocation.* The CI queries (`{anchor} path:.github/workflows`) match the raw text of the file. Of the 1,986 CI detections that came from such hits, the workflow named the tool in a `run:` or `uses:` step in 555 (28%). In 1,036 it did not: 616 had no whole-word match at all (code search matches inside words: `pyrit` in `pyrite-dev`, `webarena` in `WebArenaTask_0`), 353 named the tool only in comments, `name:` or other keys, and 67 only in `with:` inputs. For 395 the file had not been read (the enrichment read the first ten workflows of the tree). The repository-level scan had the same flaw: it searched every line, so `- name: Run promptfoo eval` matched.
  3. *The project's own metadata counted as a dependency.* The manifest parser took any quoted word and any `key =` of `pyproject.toml`, `setup.py` and `Cargo.toml`. `name = "lm_eval"` made every copy of lm-evaluation-harness a repository that depends on it; `keywords`, the names of extras groups (`evals = [...]`) and tool tables (`[tool.mypy] files`) did the same. 54 of the 665 external N3 repository–tool pairs (8.1%) were copies of the tool's own repository (19 of 39 for lm-evaluation-harness, 7 of 7 for OpenAI Evals).
- **Decision:**
  - **Repository signals go to artifact items only.** `Extractor.run` attaches them to the repository item or, without one, to a code hit. A repository the run knows only through issues keeps its text mentions (N1) and gets no N2/N3; the count is reported (`repos_without_artifact_item`).
  - **A CI invocation is read from the workflow file.** The file is parsed as YAML and only what a runner executes is searched: the `run:` script and the `uses:` reference of each step, and the `uses:` of a job that calls a reusable workflow. Comment lines of a script are dropped, and so are commands that only print (`echo`, `print`, a bare string) and what a test run excludes (`-p no:langsmith`, `-m "not ragas"`, `--ignore=`). A file that is not valid YAML yields nothing. The search hit alone yields nothing either: `import` and `config` hits still trust the query, as before (ADR-023).
  - **Forms of invocation,** strongest first, recorded in the detection (`via`): `uses` (an action of the tool's own repository, or one its organization names after it), `cli` (a gazetteer command where a command goes, so `flake8 lm_eval.py` is not one), `module` (`python -m <module>` or an inline import), `install` (a package manager installs the tool's package or repository; `uv sync --extra evals` and `pip install -e ".[evals]"` install no package called `evals`), and `name` (the anchor token elsewhere in a command: a path, an argument).
  - **The bare name supports N2, never N3.** It shows that the workflow refers to the tool, not that it runs it. It is not accepted at all for tools marked `ambiguous`, and it is reserved for anchor tools. N3 therefore needs one of the four strong forms plus the thresholds of ADR-023.
  - **The enrichment reads the workflows the search hit.** `msrkit enrich` reads those files first, up to 20 per repository, before the first ten of the tree. A signal copied from another run (ADR-054) is used only if it read, or already tried to read, every such file, and only if it still carries file contents: the reproducibility package keeps paths only, and a signal copied from it would yield no detection. A repository enriched before a hit was known is redone, also inside the same run.
  - **Dependencies come from the tables that declare them.** `pyproject.toml` and `Cargo.toml` are parsed as TOML and only tables whose key ends in `dependencies` or `dependency-groups` are read (PEP 621 and 735, Poetry, uv, PDM, Hatch, pixi); a group's name is not a package. `setup.py` gives the lists of `install_requires` and `extras_require`; `setup.cfg` is read as INI. A file that does not parse falls back to quoted requirement specs outside metadata keys.
  - **A repository that carries the tool's name is not a user of it.** When the repository's own name, or the project name its manifests declare, is the tool's id, package or repository name, no structural detection of that tool is emitted: it is the tool, a copy or a namesake (`self_copies`).
  - `extract` writes `extract_stats.json` with what each rule left out; the mining report and the package carry it.
- **Effect on run 015e (re-extracted, same items and signals):** detections 13,923 → 11,234; structural detections on issues 533 → 0; CI detections 2,186 → 473 (uses 25, cli 76, module 41, install 143, name 188), of which 135 at N3; workflow hits of the CI queries: 404 verified, 1,187 rejected, 395 unverified (their files are read in the next collection); 130 repository–tool pairs set aside as the tool itself; units with an external N3 testing tool: 102, or 181 if the bare name counted.
- **Limits:** N3 measures explicit invocation. A library used only inside the test suite (`pytest` running tests that import Ragas) leaves no trace in the workflow and stays at N2, so N3 favors tools with a command line or an action; benchmarks without a package (WebArena, AgentBench) can reach N3 only through `uses`. The bare-name form keeps noise (URLs, file paths), which is why it stops at N2; Article 2 reports the looser rule as a sensitivity analysis. Precision of each level is to be measured on the stratified sample (ADR-031, step O10).

---

## ADR-057: Issue Filters, Concept Lexicon, Gazetteer Names and Language Shares

- **Context:** Findings of the same review, on what the collection brings in.
  - 812 of the 7,339 unique issues of run 015e (11.1%) were opened by automation: `github-actions[bot]` 582 (daily lists of arXiv papers), `fire-bot` 131, Mend, Renovate. The adapter already had `filter_bots`, off by default.
  - The concept `teste` listed the bare word "oracle". 260 items matched it and 31 matched "test oracle"; the others were about Oracle the company (OpenRAE/rae#315, an issue about an Oracle Cloud deploy key, was labelled `teste`).
  - Short tool names collide with other words and other software: `pyrit` (a WPA cracker, and `pyrite`), `ranx` (another library of that name), `beir`, `mteb`, `opik`. 14 third-party repositories of the run are called `agentbench`, and other projects have their own `agentevals` module. The PyPI package `bfcl` is not the Berkeley Function Calling Leaderboard, whose package is `bfcl-eval` (module `bfcl_eval`, command `bfcl`; checked on pypi.org on 2026-11-14). `Azure/PyRIT` moved to `microsoft/PyRIT`: the anchor query for the old name returned nothing.
  - Reading the adapter showed two sampling defects. A query fills its item cap in the order of the protocol's languages, so a capped query took every item from Python repositories and none from TypeScript ones. And the page size shrank as items came in (`per_page = limit − yielded`), which shifts page boundaries: with a filter dropping items, the next page returned results already seen.
- **Decision:**
  - `filter_bots: true` in the protocol. An issue is dropped when the API declares its author a bot (`user.type == "Bot"`) or the login ends in `[bot]` or `-bot` (plus the dependency tools). A login that merely ends in "bot" is not enough. The filter applies to issues, not to repositories. Each discard is recorded with the reason `bot_author`, so the PRISMA flow counts it, and the query goes on until the cap is filled with issues from people.
  - `exclude_labels: [dependencies, requirement]`: the default label of Dependabot and Renovate, and the label of issues used as requirement records. Reason `excluded_label`. **`question` is not in the list:** 105 issues of run 015e carry it (27 in Ragas, 16 in LlamaIndex), such as "How to evaluate Agent?", and a user's question is the discourse the study looks for; excluding it would also contradict having Stack Exchange as a source.
  - "oracle" becomes "test oracle" in the concept `teste`.
  - Gazetteer 0.2: `ambiguous: true` for opik, pyrit, ranx, beir, mteb, agentbench and agentevals; BFCL gains its package, module and commands; PyRIT's repository is `microsoft/PyRIT`.
  - **One page size per query**, and a result is marked as seen before the filters, so a discard is counted once.
  - **The cap is shared among languages in proportion to their result counts** (largest remainder, at least one item for a language that has results, smallest first, and a share not used passes on). The counts are the ones `population()` reads before the search. One sampling fraction then holds for the whole query, which is what its weight assumes (ADR-058).
  - With `sampling.strata: year`, the run now asks the source for the size of every query it can count, code search included (`BaseAdapter.countable`), not only of the queries it stratifies by year.
  - GitHub adapter 0.3.0. `max_requests_per_source` goes from 2,500 to 4,000: a query dominated by automation pages up to ten times to fill its cap.
- **Consequences:** The collection differs from run 015e in what it samples: issues from automation leave, TypeScript repositories enter the capped queries, and code queries gain totals. Runs before this change are not comparable on issue counts. Bulletins published by ordinary accounts (about 90 in run 015e) pass the author filter; Article 2 sets them aside by title (ADR-059).
- **Open point for the authors:** repositories of a tool's own organization count as external adoption (the validators under `guardrails-ai/*` use an action of `guardrails-ai/guardrails`). Treating an organization as the vendor is right for `guardrails-ai` or `promptfoo` and wrong for `microsoft` or `NVIDIA`, so it needs a per-tool decision in the gazetteer and was left as it is.

---

## ADR-058: Sampling Fractions, Design Weights and Sampled Queries in the PRISMA Flow

- **Context:** With a cap of 100 items, a query takes a large share of a small result set and a small share of a large one. In run 015e the issue queries by term examined 45.2% of what GitHub held for 2023, 26.9% for 2024, 8.3% for 2025 and 2.1% for 2026. ADR-055 stored the totals so that analyses could weight, and no analysis did. The PRISMA flow reported 0 records not retrieved, because it counted only queries cut by a source's own cap, while 122 queries had stopped at the run's cap.
- **Decision:**
  - `msrkit.weights` derives the sample design from the manifest. Each executed query is a stratum (source, kind, term or literal query, year). Its fraction is examined ÷ available, where examined is the items kept plus the items a local filter dropped (with the bot filter, 100 kept out of 1,000 examined stand for a tenth of the people's issues) and available is the source's count. A query that was not cut short has fraction 1, whatever the estimate said. A truncated query without a count has an unknown fraction: its items weigh 1 and are reported as such, never guessed.
  - An item weighs the inverse of the fraction of the query that kept it (`provenance.partition`).
  - A unit counts once per tool at its best level (ADR-035), so `pair_weights` gives each unit–entry pair the weight of the most probable route to the evidence: the lightest item carrying the mention for N1, the lightest artifact item of the repository for N2/N3.
  - The mining report gains a section with the fractions by source, kind, query family and year, the largest weight and Kish's effective sample size; a group with one stratum of unknown size has an unknown fraction.
  - The PRISMA flow gains "not examined (item limit)": for the queries cut by the run's cap, the results they match beyond what was examined, with the number of such queries and how many have no total.
- **What the weights do not do:** they undo the unequal allocation among strata, not the order in which a source returns results inside one (GitHub: most recently updated first; code search: best match). A record that several queries match is counted in the total of each, so weighted totals and the PRISMA figure are upper bounds; shares and rankings depend on it less. Weights of multi-item units lean high, since a unit with several routes is more likely to be observed than by any single one.
- **Run 015e:** 122 queries stopped at the cap, 41 of them without a total (code search and Hugging Face); the others left about 120,513 results unexamined; the largest weight is 251 and the effective sample size 1,446 of 19,592 items kept.

---

## ADR-059: Article 2 — Mean of E for P2, Two Readings of P1 and P2, Automated Issues (revises ADR-046)

- **Context:** ADR-046 summarized a tool's engagement by the median of E, the percentile of the item's metric inside its source × kind. For GitHub issues the metric is reactions, and most issues have none: they tie at the floor of the stratum (E = 0.43 in run 015e, 0.47 in a later one). In run 015e, 18 of 20 tools had that floor as their median; in the next run all 21 had it, Spearman's ρ of P2 was undefined, and yet Kruskal–Wallis (H = 53.8, p < 0.001) showed that the tools differ. The difference lies above the median.
- **Decision (taken before the official collection, on statistical grounds, not on which value it gives):**
  - **P2 uses the mean of E per tool.** E is a mid-rank percentile: the share of the other items of the stratum that the item beats, ties counting half. Its mean over a stratum is 0.5 for any distribution, so the mean over a tool's items is the chance that one of them beats another item of its stratum: a stratified probability of superiority. It is the statistic that Kruskal–Wallis and Dunn already compare (mean ranks), it uses the whole distribution, and it is bounded, so no item can dominate it. The alternatives were set aside: the 75th percentile is also on the floor when more than 75% of the items tie there, and the share of items with a reaction has a different baseline in each source, which breaks the rule that engagement is compared only after normalization.
  - The median stays in the tables, with the share of items at the floor, and ρ with the median is reported as the statistic first registered (`ResRhoMediana`). Sensitivity: ρ with the share of a tool's items in the top 10% of their stratum (`--top-share`), which has the same baseline everywhere; without listicles; non-truncated queries only; the negative binomial model, now compared with the mean. ρ gains a bootstrap 95% interval over the items, with N3 held fixed.
  - **Two readings.** P1 and P2 are computed on the sample and on design-weighted statistics (ADR-058): weighted unit counts for Kendall's τ, the weighted percentile and weighted mean of E with weighted N3 for ρ. A proposition is read as supported only when both readings agree (`ResPUmConclusao`, `ResPDoisConclusao`); otherwise the result is "inconclusive (depends on the weighting)". When the package's manifest has no totals, only the sample reading exists.
  - **N3 rule.** The main analysis uses the N3 of ADR-056. τ with the bare name counted as adoption is reported as a sensitivity analysis (`ResTauUmTresNome`).
  - **Automated issues leave every analysis,** not only engagement: dependency upkeep and alerts, as before, plus periodic bulletins that ordinary accounts publish (a date in the title with a word of the genre, such as "papers", "digest", "daily", or a title that is only a date). On run 015e the rule matches 493 of the 812 issues by automated authors and 46 by ordinary accounts.
  - The thresholds (τ < 0.5, |ρ| < 0.4) and the other parameters of ADR-046 are unchanged.
- **Run 015e, re-extracted (calibration only, not the official corpus):** τ(N1, N3) = 0.34 [0.17; 0.44] on the sample, 0.38 weighted and 0.40 with the bare name as N3: P1 supported in the three. ρ(mean E, N3) = −0.36 [−0.57; 0.10] over 17 tools, −0.37 weighted: under the 0.4 threshold, with an interval that crosses it. ρ with the median is −0.08 and with the top share 0.01.
- **Consequences:** The paper's text and the header of `tab_qp3` must say "mean", not "median". `tab_qp1_pond.tex` is new. The macro names are unchanged; new ones were added.

---

## ADR-060: One Protocol Hash, and the Commit of the Code That Collected

- **Context:** The package of run 015e showed protocol hash `b330350c…` and its report `b87c6f3a…` for the same file: the package hashed the bytes of the Windows checkout (CRLF) and the manifest hashes the LF form (ADR-047). The package also recorded the commit at packaging time, `e30414f`, while a later commit had changed the protocol before the collection ended.
- **Decision:**
  - The package records `protocol_sha256` as the manifest does, and stores the files of `protocol/` with LF line endings, so the SHA-256 of the packaged protocol is that hash. `protocol_matches_run` is false when the protocol file changed after the collection.
  - `msrkit run` writes the code's state to the manifest (`code`: `git_commit`, `git_dirty`). The package records the state at packaging time and the one of the collection (`collected_with`), and its README says in words whether the commit reproduces the run.
  - The state is read from the checkout the package runs from, not from the current directory: a run started from another folder, or from a data folder that is a repository of its own, still records the commit of the code. An installed copy that git does not track (a virtual environment inside the project folder) records no commit.
  - `scripts/coleta_local.py` shows the commit before it starts and warns when there are uncommitted changes.
- **Consequences:** A package built from a working tree with uncommitted changes says so; the remedy is to commit and run `msrkit package --run <id>` again.
