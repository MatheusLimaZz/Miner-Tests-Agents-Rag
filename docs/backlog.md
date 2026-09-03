# Backlog

Items identified as relevant but out of scope for v0.
See specification §18.

## v1 Candidates

- [ ] Near-duplicate detection (MinHash/LSH)
- [ ] Gazetteer of testing tools with alias resolution
- [ ] Entity extraction for tool names and methods
- [ ] LDA / topic modeling on the corpus
- [ ] Embeddings-based semantic search
- [ ] Classification by failure mode, oracle type, evidence level
- [ ] Manual triage interface (web UI)
- [ ] Stemming and lemmatization in keyword matching
- [ ] Snowflake/Substack dedicated adapters (beyond RSS)
- [ ] Discord full implementation (with ethical review framework)
- [ ] X/Twitter implementation when free tier supports search

## Infrastructure

- [ ] Container image (Docker)
- [ ] CI/CD pipeline
- [ ] Scheduled collection runs (cron)
- [ ] Progress persistence for interrupted runs (checkpoint per partition)
- [ ] Parallel collection across sources
- [ ] Connection pooling for httpx
