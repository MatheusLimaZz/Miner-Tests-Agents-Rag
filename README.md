# MSR-Kit

[![CI](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml/badge.svg)](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml)

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
# Using the shortcut (Windows PowerShell or CMD):
.\msr validate
.\msr plan --source hackernews

# Or using the msrkit CLI:
msrkit validate
msrkit plan --source hackernews
```

### 5. Run Collection

```bash
# Quick test: collect 5 items from a single source
.\msr run -s hackernews -l 5

# Full collection using the default protocol:
.\msr run

# Resume an interrupted run:
.\msr run --resume <run_id>
```

### 6. Post-Processing & Export

```bash
# Deduplicate (auto-selects the latest run):
.\msr dedupe

# View statistics:
.\msr stats

# Export to CSV / JSONL / DuckDB:
.\msr export -f csv -o resultados.csv
.\msr export -f jsonl
.\msr export -f duckdb
```

### 7. Interactive Terminal Menu

Simply run without arguments to open the interactive wizard:

```powershell
.\msr
```

## Commands

| Command | Description |
|---------|-------------|
| `msrkit menu` (or `.\msr`) | Interactive terminal menu to run commands easily |
| `msrkit sources [--md]` | List all adapters with availability and policies |
| `msrkit validate [protocol]` | Validate protocol schema + credentials (defaults to sample protocol) |
| `msrkit plan [protocol] [-s source]` | Dry run: show partitions and request budget |
| `msrkit run [protocol] [-s source] [-l limit]` | Execute collection (retomable with `--resume`) |
| `msrkit dedupe [--run <id>]` | Deduplicate items (defaults to latest run) |
| `msrkit stats [--run <id>]` | Show collection statistics (defaults to latest run) |
| `msrkit export [--run <id>] [-f fmt]` | Export to CSV/JSONL/DuckDB (defaults to latest run) |
| `msrkit normalize [--run <id>]` | Reprocess from raw data (defaults to latest run) |

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
