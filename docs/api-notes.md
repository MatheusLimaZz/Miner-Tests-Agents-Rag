# API Notes

This file records divergences between the MSR-Kit specification and
the actual official API documentation, discovered during implementation.

## Format

Each entry follows this template:

```
### [Source] — [Date]
- **Spec says:** ...
- **Docs say:** ...
- **Decision:** ...
- **Link:** ...
```

---

## Entries

### General — 2026-09-03
No divergences found during initial implementation. All endpoints, parameters, and limits were verified against the linked documentation at the time of implementation. Each adapter's `policy.docs_url` points to the official documentation used for verification.

### Dev.to — 2026-09-10
- **Spec says:** Full-text search for research terms across dev.to articles.
- **Docs say:** Dev.to public REST API (`GET /api/articles`) does not offer a free-text search endpoint; it only supports querying by published `tag` or `top`.
- **Decision:** Collect articles by research-relevant tags and perform local keyword filtering (`match_terms`) against article titles, descriptions, and tags. Recorded explicitly in manifest (ADR-004).
- **Link:** https://developers.forem.com/api/v0#tag/articles/operation/getArticles

### Dev.to — 2026-11-03 (paginação por tag)
- **Observed (live calls, `per_page=1000`):** `GET /api/articles?tag=` pages through a tag's whole history and returns an empty list after the last page: `rag` ends at page 6 (≈5.7k articles, oldest 2021-01), `llm` at 35 (≈34k), `agents` at 32 (≈31k), `testing` at 27 (≈26k, back to 2014), `llmops` at 1 (258). `ai` still had 1,000 articles of September–October 2026 on page 60. Pages are roughly, not strictly, in reverse publication order: page 10 of `llm` spans 2026-08-05..2026-10-03.
- **Decision:** dev.to is collected whole inside the window (`census: true`, ADR-052), with `max_pages: 40` over `rag`, `llm`, `agents`, `llmops` and `testing`. `ai` is dropped: covering the window through it would take more than a thousand pages.
- **Link:** https://developers.forem.com/api/v1#tag/articles/operation/getArticles

### Stack Exchange — 2026-09-12
- **Spec says:** Standard HTTP JSON search across Stack Overflow / Software Engineering questions.
- **Docs say:** Stack Exchange API v2.3 strictly requires `Accept-Encoding: gzip` (uncompressed requests trigger errors), enforces dynamic `backoff` directives, and interprets multiple tags as strict `AND` conjunctions.
- **Decision:** Send gzip headers unconditionally, honor platform backoff pauses in Governor, and query by individual relevant sites and search terms without forcing impossible tag conjunctions (ADR-005).
- **Correction (2026-09-28):** The official docs for `/search/advanced` define `tagged` as "a semicolon delimited list of tags, of which **at least one** will be present on all returned questions" (OR semantics), not AND. The adapter now sends the tag list with OR semantics (ADR-017).
- **Link:** https://api.stackexchange.com/docs

### Stack Exchange — 2026-11-01 (revisão da documentação)
- **Docs say:** The Authentication page now states that "all API requests must pass authentication details using request headers" (`Authorization: Bearer {API key or access token}`) and points to a Stack Apps help page dated June 2025 (behind a Cloudflare challenge, not readable by script). A new beta MCP server (`mcp.stackoverflow.com`, 100 calls/day per user, login required) was added. The throttle page is unchanged: 300 requests/day per IP without a key, 10,000 with an app key, `backoff` mandatory, at most 30 req/s per IP.
- **Observed (live calls on 2026-11-01):** anonymous requests still work (`quota_max` 300); `page > 25` without a key returns 403 `access_denied`; the `key` query parameter is still parsed (an unknown key returns 400 "`key` doesn't match a known application"); `Authorization: Bearer <value>` is parsed as an access token (an unknown value returns 402 `invalid_access_token`). The runs a2a8 (2026-09-29) and d28a (2026-10-20) collected 146 and 672 items with the key in the query parameter, with no failed partitions.
- **Decision:** Keep the `key` query parameter, which is the behaviour verified with a real key; switch to the header only after testing it with a real key. `scripts/coleta_local.py` checks the key with one call to `/info` before collecting, because a rejected key makes every search return 400 and the source comes out empty without failing the run. The adapter logs now include the API error message.
- **Link:** https://api.stackexchange.com/docs/authentication, https://api.stackexchange.com/docs/throttle

### Medium via RSS — 2026-09-15
- **Spec says:** Temporal window query for Medium engineering articles (`since` .. `until`).
- **Docs say:** Public Medium feeds (`https://medium.com/feed/tag/...`) provide only the ~10 most recent posts, without pagination or historical date filtering parameters.
- **Decision:** Collect available feed items without attempting unauthorized web scraping. Set `policy.supports_date_filter = False` and record `truncated: true` and `historical_coverage: false` in the run manifest for scientific transparency (ADR-007).
- **Link:** https://medium.com/feed

### Reddit — 2026-09-28
- **Spec says:** OAuth search per subreddit, free tier.
- **Docs say:** Since November 2025 (Responsible Builder Policy), self-service app creation is closed; every new OAuth credential requires manual approval.
- **Decision:** Reddit disabled in the protocol and excluded from Study E2; adapter kept for reuse (ADR-014).
- **Link:** https://support.reddithelp.com/hc/en-us/articles/42728983564564

### Hacker News (Algolia) — 2026-09-28
- **Spec says:** No hard result cap (`max_results_per_query=None`).
- **Docs say:** Algolia's `paginationLimitedTo` caps any query at 1,000 hits regardless of `page`.
- **Decision:** Treat HN like GitHub: estimate with `nbHits` and partition by date window (see `docs/PLANO.md`, step C1).
- **Link:** https://github.com/algolia/hn-search/issues/230

### GitHub Search API — 2026-10-20
- **Spec says:** Protocol §7.3 defines literal repository search queries with quoted boolean groups (`("RAG" OR "retrieval augmented") (eval OR test OR testing)`) and expands `{repo}` over seed gazetteer repositories (`explodinggradients/ragas`, `Giskard-AI/giskard`, `NVIDIA/NeMo-Guardrails`).
- **Docs say:** (1) GitHub REST `/search/issues` does not follow repository HTTP 301 redirects on `repo:owner/name` qualifiers and instead returns `422 Unprocessable Entity` when a repository has been renamed or transferred. (2) GitHub REST `/search/repositories` legacy parser returns `total_count: 0` when quoted strings appear immediately inside an opening parenthesis (`("RAG" OR ...)`).
- **Decision:** Update `protocols/gazetteer.yaml` and `protocols/gold_set.yaml` to canonical post-redirect repository names (`vibrantlabsai/ragas`, `Giskard-AI/giskard-oss`, `NVIDIA-NeMo/Guardrails`) and simplify literal `kind: repo` queries in `protocols/v0_rag_agents_testing.yaml` to unquoted parenthesized syntax (`RAG (eval OR test OR testing)` and `agentic (eval OR test OR testing)`), which returns 27,338 and 170,656 repositories respectively.
- **Link:** https://docs.github.com/en/rest/search/search

### GitHub Search API — 2026-11-01
- **Spec says:** Protocol §7.3 collects GitHub items of `kind: issue` through `/search/issues`.
- **Observed:** `/search/issues` now answers `422 Unprocessable Entity` with "Query must include 'is:issue' or 'is:pull-request'" to a query that does not set the type. In run 2026-11-01T23-59-22Z-7837, 72 of 107 issue requests (every term × language query) got that 422, and the adapter recorded them as completed with 0 items. Run d28a (2026-10-20) sent the same queries without the qualifier and received issues and pull requests mixed: 2,405 issues and 1,390 PRs among its 3,795 `kind: issue` items.
- **Decision:** Issue searches add `is:issue` when the query does not already set `is:`/`type:` issue or PR, following the protocol's `kind: issue` literally (ADR-049). A 422 now marks the query as not completed after the other languages run.
- **Link:** https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests

### GitHub Search API — 2026-11-03 (busca de repositórios ignora OR)
- **Spec says:** Protocol §7.3 searches repositories with `RAG (eval OR test OR testing)` and `agentic (eval OR test OR testing)`; the 2026-10-20 entry took their non-zero counts (27,338 and 170,656) as proof that the unquoted syntax works.
- **Observed (live counts on 2026-11-03, window 2023-01-01..2026-08-31):** `/search/repositories` ignores the parenthesized OR group. `RAG (eval OR test OR testing) in:name,description,readme stars:>5` and `RAG in:name,description,readme stars:>5` both return 27,662, with the same top results. With `agentic`: 175,235 with or without the group. A top-level `RAG eval OR RAG test OR RAG testing` returns 99, the count of `RAG testing` alone. One word per query does filter: `in:name,description stars:>5` gives RAG eval 100, evaluation 266, test 113, testing 99, and agentic eval 394, evaluation 1,068, test 1,489, testing 1,456. Code search splits `"tool_call"` into `tool` and `call`: `"tool_call" "assert" language:Python path:tests` matches 291,840 files.
- **Observed (code search, 2026-11-03):** `sort=indexed` returns the same set as the default best-match order for three code queries. The top 100 of `"from ragas import"`, `"import deepeval"` and `filename:promptfooconfig.yaml` matched run d855 (24 h earlier) 100, 100 and 98 times out of 100.
- **Decision:** The two repository queries become eight, one word each, over name and description (ADR-051). The tool-call query becomes `"expected_tool_calls"` (1,076 files) and `"assert_tool_call"` (463), in `path:tests`.
- **Link:** https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories
