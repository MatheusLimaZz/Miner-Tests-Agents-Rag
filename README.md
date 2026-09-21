# MSR-Kit

[![CI](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml/badge.svg)](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml)

**Mining grey literature through official APIs** for empirical software engineering research.

MSR-Kit is a command-line tool that collects, normalizes, deduplicates, and exports items from multiple platforms using only official APIs and public feeds. It is designed for an academic study cataloging **testing tools and methods** used in **LLM+RAG systems** and **agent-based systems**.

## Quick Start (Docker - 100% Isolado)

O MSR-Kit roda dentro de um container Linux isolado baseado em **Ubuntu 24.04 LTS**.
Você **não precisa** instalar Python, mexer em variáveis de ambiente (`PATH`) nem rodar scripts no seu computador host.

### 1. Construir a imagem Docker (apenas na 1ª vez)

Com o Docker Desktop ou Podman aberto, execute no terminal:

```bash
docker compose build
```

### 2. Abrir o Menu Interativo

```bash
docker compose run --rm msrkit
```

O menu interativo com opções numéricas (`1` a `9`) abrirá diretamente na sua tela. Todos os dados minerados e relatórios CSV são salvos automaticamente na pasta `./data` do seu computador.

### 3. Ou executar comandos diretamente via Docker

```bash
# Validar o protocolo de pesquisa (sem gastar rede):
docker compose run --rm msrkit validate protocols/v0_rag_agents_testing.yaml

# Simulação prévia / Orçamento de requisições (Dry Run):
docker compose run --rm msrkit plan protocols/v0_rag_agents_testing.yaml --source hackernews

# Executar coleta rápida (ex: 10 itens do Hacker News):
docker compose run --rm msrkit run protocols/v0_rag_agents_testing.yaml --source hackernews --limit 10

# Executar coleta completa do protocolo:
docker compose run --rm msrkit run protocols/v0_rag_agents_testing.yaml

# Desduplicar itens coletados:
docker compose run --rm msrkit dedupe

# Ver estatísticas da coleta:
docker compose run --rm msrkit stats

# Exportar para planilha CSV (salva direto em data/resultados.csv):
docker compose run --rm msrkit export -f csv -o data/resultados.csv
```

---

### Execução no Host sem Docker (Opcional para Desenvolvedores)

Se preferir rodar nativamente com Python local:

```bash
python -m venv .venv
source .venv/bin/activate    # Linux / macOS
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"
msrkit menu
```


### 2. Configurar Credenciais (Opcional)

Nenhuma credencial é necessária para começar a testar — **Hacker News**, **dev.to** e **RSS** funcionam sem qualquer chave.

Para minerar o GitHub ou Stack Overflow com cotas maiores, copie o arquivo de exemplo e insira seus tokens:

```bash
cp .env.example .env
```

| Variável | Fonte | Obrigatório? |
|----------|-------|--------------|
| `GITHUB_TOKEN` | GitHub | Recomendado (60 req/h sem token, 5.000 com token) |
| `STACKEXCHANGE_KEY` | Stack Exchange | Opcional (eleva a cota diária para 10.000) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Reddit | Obrigatório para o Reddit |
| `HF_TOKEN` | Hugging Face | Opcional |
| `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | Bluesky | Obrigatório para o Bluesky |
| `X_BEARER_TOKEN` | X/Twitter | Plano pago |
| `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_IDS` | Discord | Bot + permissão de admin |

### 3. Execução via Linha de Comando (CLI)

```bash
# 1. Validar o protocolo de pesquisa (sem gastar rede):
msrkit validate protocols/v0_rag_agents_testing.yaml

# 2. Fazer uma simulação prévia / orçamento de requisições (Dry Run):
msrkit plan protocols/v0_rag_agents_testing.yaml --source hackernews

# 3. Executar a coleta (teste rápido com 5 itens):
msrkit run protocols/v0_rag_agents_testing.yaml --source hackernews --limit 5

# 4. Executar coleta completa do protocolo:
msrkit run protocols/v0_rag_agents_testing.yaml

# 5. Desduplicar itens coletados:
msrkit dedupe

# 6. Ver estatísticas da coleta:
msrkit stats

# 7. Exportar resultados para CSV:
msrkit export -f csv -o resultados.csv
```

## Tabela de Comandos

| Comando | Descrição |
|---------|-----------|
| `msrkit` | Exibe a tela de ajuda com todos os comandos |
| `msrkit menu` | Menu interativo no terminal com opções numéricas |
| `msrkit sources [--md]` | Lista todos os adaptadores, disponibilidade e políticas |
| `msrkit validate [protocol]` | Valida o esquema do protocolo e credenciais (sem rede) |
| `msrkit plan [protocol] [-s source]` | Simulação prévia (Dry run): partições e orçamento |
| `msrkit run [protocol] [-s source] [-l limit]` | Executa a coleta dos dados (retomável com `--resume`) |
| `msrkit dedupe [--run <id>]` | Desduplica itens coletados (usa a última coleta por padrão) |
| `msrkit stats [--run <id>]` | Exibe estatísticas de itens, requisições e descartes |
| `msrkit export [--run <id>] [-f fmt]` | Exporta para CSV, JSONL ou DuckDB |
| `msrkit normalize [--run <id>]` | Reprocessa itens a partir dos dados brutos armazenados |

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
