# MSR-Kit

**Mining grey literature through official APIs** for empirical software engineering research.

MSR-Kit is a command-line tool that collects, normalizes, deduplicates, and exports items from multiple platforms using only official APIs and public feeds. It is designed for an academic study cataloging **testing tools and methods** used in **LLM+RAG systems** and **agent-based systems**.

## Quick Start

### 1. Install

```bash
# With uv (recommended)
uv pip install -e ".[dev]"

# Or with pip
pip install -e ".[dev]"
```

### 2. Configure Credentials

Copy `.env.example` to `.env` and fill in the API keys you have:

```bash
cp .env.example .env
```

Only sources whose credentials are configured will be available. At minimum, you need no credentials at all — Hacker News, dev.to, and RSS work without authentication.

Available credentials:

| Variable | Source | Required? |
|----------|--------|-----------|
| `GITHUB_TOKEN` | GitHub | Recommended (60 req/h without, 5000 with) |
| `STACKEXCHANGE_KEY` | Stack Exchange | Optional (raises daily quota) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Reddit | Required for Reddit |
| `HF_TOKEN` | Hugging Face | Optional (raises rate limits) |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Bluesky | Required for Bluesky |
| `X_BEARER_TOKEN` | X/Twitter | Paid plan required |
| `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_IDS` | Discord | Bot + admin auth required |

### 3. Edit the Protocol

Open `protocols/v0_rag_agents_testing.yaml` and customize:

- **`window.since` / `window.until`**: The date range to search
- **`terms`**: Search keywords (edit these for your research question)
- **`sources`**: Enable/disable sources and configure source-specific filters

### 4. Validate & Plan

```bash
# Validate the protocol (no network, checks schema + credentials)
msrkit validate protocols/v0_rag_agents_testing.yaml

# Preview the collection plan (no data collected)
msrkit plan protocols/v0_rag_agents_testing.yaml
```

### 5. Run Collection

```bash
# Execute the collection
msrkit run protocols/v0_rag_agents_testing.yaml

# Resume an interrupted run
msrkit run protocols/v0_rag_agents_testing.yaml --resume <run_id>
```

### 6. Post-Processing

```bash
# Reprocess from raw data (no network)
msrkit normalize --run <run_id>

# Deduplicate items
msrkit dedupe --run <run_id>

# View statistics
msrkit stats --run <run_id>

# Export
msrkit export --run <run_id> --format csv
msrkit export --run <run_id> --format jsonl
msrkit export --run <run_id> --format duckdb
```

## Commands

| Command | Description |
|---------|-------------|
| `msrkit sources [--md]` | List all adapters with availability and policies |
| `msrkit validate <protocol>` | Validate protocol schema + credentials (no network) |
| `msrkit plan <protocol>` | Dry run: show partitions and request budget |
| `msrkit run <protocol>` | Execute collection (retomable with `--resume`) |
| `msrkit normalize --run <id>` | Reprocess from raw data (no network) |
| `msrkit dedupe --run <id>` | Deduplicate items |
| `msrkit stats --run <id>` | Show collection statistics |
| `msrkit export --run <id>` | Export to CSV/JSONL/DuckDB |

## Architecture

```
src/msrkit/
├── cli.py          # Typer CLI with all commands
├── config.py       # YAML protocol → Pydantic models
├── models.py       # Domain models (Item, Query, Manifest, etc.)
├── governor.py     # Token bucket rate limiter
├── partition.py    # Query partitioning algorithm
├── provenance.py   # Run IDs, hashing, manifest management
├── storage.py      # JSONL + DuckDB storage
├── dedupe.py       # URL canonicalization + content hashing
├── keywords.py     # Term matching with context windows
├── registry.py     # Adapter discovery and registration
└── adapters/       # One module per source
    ├── base.py     # BaseAdapter with shared HTTP, retry, governor
    ├── github.py, stackexchange.py, devto.py, ...
    ├── linkedin.py # Permanently UNSUPPORTED (documented)
    └── ...
```

## Data Layout

```
data/
├── raw/{source}/{run_id}/      # Immutable gzip JSONL (raw API responses)
├── items/{run_id}/items.jsonl  # Normalized items
├── runs/{run_id}/manifest.json # Full provenance manifest
└── msrkit.duckdb               # Query database
```

## Ethical Considerations

- **Only official APIs and public feeds** — no scraping
- **LinkedIn is permanently excluded** — no public search API
- **Discord requires explicit admin authorization** and ethical review
- **No personal data** beyond public author handles
- **Rate limits are respected programmatically** — never exceeds declared limits
- **Full provenance** — every item is traceable to its exact API request

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/

# Type check
mypy
```

## License

Apache-2.0
