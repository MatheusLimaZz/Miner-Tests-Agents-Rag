# MSR-Kit

[![CI](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml/badge.svg)](https://github.com/MatheusLimaZz/Miner-Tests-Agents-Rag/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Docker](https://img.shields.io/badge/docker-Ubuntu%2024.04%20LTS-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

> **Mining grey literature through official APIs** for empirical software engineering research.

O **MSR-Kit** é uma ferramenta de linha de comando científica que coleta, normaliza, desduplica e exporta dados de múltiplas plataformas (Hacker News, Dev.to, Feeds RSS, GitHub, Stack Overflow, Hugging Face, Reddit, etc.) utilizando **exclusivamente APIs oficiais e feeds públicos**, sem web scraping.

Ele foi construído especialmente para apoiar pesquisas acadêmicas (MSR/SLR) que catalogam **ferramentas e métodos de teste** para **sistemas LLM com RAG** e **sistemas baseados em agentes**.

---

## 📑 Sumário

1. [🧭 Escolha a Melhor Forma de Executar](#-escolha-a-melhor-forma-de-executar)
2. [🐳 Opção A: Executar com Docker (Recomendado - 100% Isolado)](#-opção-a-executar-com-docker-recomendado---100-isolado)
3. [🐍 Opção B: Executar com Python Nativo (Host)](#-opção-b-executar-com-python-nativo-host)
4. [📱 O Menu Interativo no Terminal (`menu`)](#-o-menu-interativo-no-terminal-menu)
5. [💻 Referência Detalhada de Comandos CLI](#-referência-detalhada-de-comandos-cli)
6. [🔬 Como Configurar a sua Pesquisa (Protocolo YAML)](#-como-configurar-a-sua-pesquisa-protocolo-yaml)
7. [🔑 Configuração de Credenciais e Tokens (Opcional)](#-configuração-de-credenciais-e-tokens-opcional)
8. [📁 Onde Ficam os Dados Coletados (`data/`)](#-onde-ficam-os-dados-coletados-data)
9. [🛡️ Princípios Éticos e Rigor Científico](#️-princípios-éticos-e-rigor-científico)
10. [🧪 Testes e Qualidade de Código](#-testes-e-qualidade-de-código)

---

## 🧭 Escolha a Melhor Forma de Executar

| Seu Perfil / Objetivo | Melhor Escolha | O que você precisa ter instalado |
| :--- | :--- | :--- |
| **Quer apenas testar ou coletar dados sem mexer no Windows** | [🐳 **Docker**](#-opção-a-executar-com-docker-recomendado---100-isolado) | Apenas o **Docker Desktop** ou **Podman** |
| **Prefere opções visuais sem decorar comandos ou flags** | [📱 **Menu Interativo**](#-o-menu-interativo-no-terminal-menu) | Docker ou Python |
| **Quer desenvolver, alterar código-fonte ou rodar testes unitários** | [🐍 **Python Nativo**](#-opção-b-executar-com-python-nativo-host) | **Python 3.11+** e Git |
| **Quer automatizar coletas em scripts ou pipelines CI/CD** | [💻 **CLI Direta**](#-referência-detalhada-de-comandos-cli) | Docker ou Python |

---

## 🐳 Opção A: Executar com Docker (Recomendado - 100% Isolado)

Esta é a opção mais limpa e moderna. O MSR-Kit roda dentro de um container Linux isolado baseado em **Ubuntu 24.04 LTS**.
- ✅ **Zero poluição no seu Windows:** Não altera `PATH`, não precisa de `.bat` e não instala bibliotecas no seu computador.
- ✅ **Persistência automática:** Todas as planilhas CSV e dados coletados aparecem diretamente na pasta `data/` do seu computador.

### Passo 1: Construir a imagem (apenas na 1ª vez)
Com o Docker Desktop ou Podman aberto, abra o terminal na pasta do projeto e rode:
```bash
docker compose build
```

### Passo 2: Abrir o Menu Interativo
```bash
docker compose run --rm msrkit
```
*(O menu abrirá diretamente no terminal com opções de `0` a `9`).*

### Passo 3: Ou rodar comandos diretos pelo Docker
Você pode executar qualquer comando adicionando os argumentos após `msrkit`:
```bash
# Validar o protocolo sem gastar requisições:
docker compose run --rm msrkit validate protocols/v0_rag_agents_testing.yaml

# Simulação / Orçamento de requisições (Dry Run):
docker compose run --rm msrkit plan protocols/v0_rag_agents_testing.yaml --source hackernews

# Coletar 10 itens de teste do Hacker News:
docker compose run --rm msrkit run protocols/v0_rag_agents_testing.yaml --source hackernews --limit 10

# Desduplicar itens coletados na última rodada:
docker compose run --rm msrkit dedupe

# Exportar para uma planilha CSV pronta para análise:
docker compose run --rm msrkit export -f csv -o data/resultados.csv
```

---

## 🐍 Opção B: Executar com Python Nativo (Host)

Se você é desenvolvedor e prefere rodar o código diretamente no seu sistema operacional (Windows, Linux ou macOS):

### Passo 1: Pré-requisitos
- **Python 3.11** ou **Python 3.12** instalado ([python.org](https://www.python.org/downloads/)).
- *(No Windows, lembre-se de marcar a caixa "Add Python to PATH" durante a instalação).*

### Passo 2: Criar ambiente virtual e instalar dependências
Abra o terminal na pasta do projeto e execute:

```bash
# 1. Criar o ambiente virtual isolado:
python -m venv .venv

# 2. Ativar o ambiente:
.venv\Scripts\activate      # No Windows (PowerShell / CMD)
source .venv/bin/activate    # No Linux / macOS

# 3. Instalar o MSR-Kit em modo de desenvolvimento:
pip install -e ".[dev]"
# (Ou se tiver uv instalado: uv pip install -e ".[dev]")
```

### Passo 3: Usar o comando `msrkit`
Após instalar, o comando `msrkit` estará disponível no terminal:
```bash
# Abrir o menu interativo:
msrkit menu

# Ou ver a tela de ajuda com todos os comandos:
msrkit --help
```

---

## 📱 O Menu Interativo no Terminal (`menu`)

Para quem não quer decorar sintaxe de terminal, o MSR-Kit oferece um menu visual interativo.

Para abrir:
- **No Docker:** `docker compose run --rm msrkit`
- **No Python Nativo:** `msrkit menu`

```text
╭────────────────────────────────────────────────────────╮
│ MSR-Kit v0.1.0                                         │
│ Mining grey literature through official APIs           │
╰────────────────────────────────────────────────────────╯

Escolha uma ação:
  1 - Status das fontes e APIs (sources)
  2 - Coleta rápida no Hacker News (5 itens)
  3 - Coleta rápida no dev.to (5 itens)
  4 - Coleta rápida nos feeds RSS de IA (5 itens)
  5 - Simulação de planejamento / Dry-Run (plan)
  6 - Desduplicar última coleta (dedupe)
  7 - Exportar última coleta em CSV (export -f csv)
  8 - Estatísticas da última coleta (stats)
  9 - Validar arquivo de protocolo (validate)
  0 - Sair

Digite o número da opção [0]:
```

### O que cada opção faz:
- **`1` - Status das fontes:** Mostra uma tabela rica indicando quais fontes estão disponíveis, limites de taxa (rate limits) e se exigem token.
- **`2`, `3`, `4` - Coletas Rápidas (HN, Dev.to, RSS):** Coleta instantaneamente 5 itens de teste sem precisar configurar nada.
- **`5` - Dry-Run (Simulação):** Calcula quantas requisições seriam feitas sem gastar cota de rede.
- **`6` - Desduplicação:** Detecta posts repetidos e URLs equivalentes, gerando um dataset limpo.
- **`7` - Exportar em CSV:** Gera a planilha `resultados.csv` contendo IDs, títulos, links, autores e datas.
- **`8` - Estatísticas:** Exibe o total de requisições, tempo e itens coletados no último run.
- **`9` - Validar protocolo:** Checa a sintaxe do arquivo YAML de pesquisa.
- **`0` - Sair:** Encerra a aplicação.

---

## 💻 Referência Detalhada de Comandos CLI

Para automação de coletas, scripts e usuários avançados, todos os comandos podem ser invocados diretamente via CLI:

| Comando | Descrição | Exemplo de Uso |
| :--- | :--- | :--- |
| **`msrkit`** | Exibe a ajuda geral e comandos disponíveis | `msrkit` ou `msrkit --help` |
| **`msrkit menu`** | Abre o assistente interativo por opções numéricas | `msrkit menu` |
| **`msrkit sources`** | Lista todos os adaptadores, políticas e status das APIs | `msrkit sources` (ou `msrkit sources --md` para Markdown) |
| **`msrkit validate`** | Valida a sintaxe do protocolo e credenciais (sem rede) | `msrkit validate protocols/v0_rag_agents_testing.yaml` |
| **`msrkit plan`** | Simulação (Dry Run): calcula partições e estimativa de requisições | `msrkit plan protocols/v0_rag_agents_testing.yaml -s hackernews` |
| **`msrkit run`** | Executa a mineração real dos dados | `msrkit run protocols/v0_rag_agents_testing.yaml -s hackernews -l 50` |
| **`msrkit dedupe`** | Remove duplicatas por URL canônica e SimHash | `msrkit dedupe` (usa o último run por padrão) |
| **`msrkit stats`** | Exibe resumo de requisições, descartes e itens coletados | `msrkit stats` |
| **`msrkit export`** | Exporta os dados para CSV, JSONL ou DuckDB | `msrkit export -f csv -o data/meus_dados.csv` |
| **`msrkit normalize`** | Reprocessa e reclassifica dados brutos sem refazer chamadas de rede | `msrkit normalize` |

### Parâmetros e Flags Mais Utilizados:
- `-s, --source <nome>`: Executa a ação apenas para uma fonte específica (ex: `hackernews`, `devto`, `rss`, `github`).
- `-l, --limit <número>`: Limita a quantidade máxima de itens a serem coletados (ótimo para testes rápidos).
- `-f, --format <formato>`: Formato de exportação (`csv`, `jsonl` ou `duckdb`).
- `-o, --output <arquivo>`: Caminho de saída do arquivo exportado.
- `--resume <run_id>`: Retoma uma coleta que foi interrompida sem recomeçar do zero.

---

## 🔬 Como Configurar a sua Pesquisa (Protocolo YAML)

O arquivo de protocolo em [protocols/v0_rag_agents_testing.yaml](protocols/v0_rag_agents_testing.yaml) é o contrato científico da sua pesquisa. Você pode editá-lo para definir o escopo exato do seu estudo:

```yaml
version: 0
name: v0_rag_agents_testing
description: >
  Coleta exploratória sobre ferramentas e métodos de teste para sistemas
  LLM com RAG e sistemas baseados em agentes.

# 1. Janela temporal da busca
window:
  since: "2023-01-01"
  until: "2026-08-31"

# 2. Palavras-chave / Termos de busca da sua pesquisa
terms:
  - "RAG testing"
  - "RAG evaluation"
  - "test RAG"
  - "evaluate RAG"
  - "LLM testing"
  - "LLM evaluation"
  - "agent testing"
  - "agent evaluation"
  - "hallucination test"
  - "eval harness"

# 3. Idiomas desejados
languages:
  - en
  - pt

# 4. Habilitar ou desabilitar fontes de acordo com a sua necessidade
sources:
  hackernews:
    enabled: true
  devto:
    enabled: true
  rss:
    enabled: true
  github:
    enabled: true
```

---

## 🔑 Configuração de Credenciais e Tokens (Opcional)

**Você não precisa de nenhuma chave de API para começar a testar.** Fontes como **Hacker News**, **Dev.to** e **RSS** funcionam publicamente sem autenticação.

Para minerar fontes com limites maiores (como o GitHub ou Stack Overflow), copie o arquivo de exemplo:
```bash
cp .env.example .env
```
E preencha as variáveis correspondentes no arquivo `.env`:

| Fonte | Variável no `.env` | Como Obter / Requisitos | Benefício |
| :--- | :--- | :--- | :--- |
| **Hacker News** | *(Nenhuma)* | Nenhuma chave necessária (Público) | Pronto para uso |
| **Dev.to** | *(Nenhuma)* | Nenhuma chave necessária (Público) | Pronto para uso |
| **Feeds RSS** | *(Nenhuma)* | Nenhuma chave necessária (Público) | Pronto para uso |
| **GitHub** | `GITHUB_TOKEN` | Token pessoal gratuito no [github.com/settings/tokens](https://github.com/settings/tokens) | Eleva limite de 60 para **5.000 req/hora** |
| **Stack Exchange** | `STACKEXCHANGE_KEY` | Chave de app gratuita no Stack Apps | Eleva cota de 300 para **10.000 req/dia** |
| **Hugging Face** | `HF_TOKEN` | Token gratuito no perfil do Hugging Face | Maior taxa de requisições em models/papers |
| **Reddit** | `REDDIT_CLIENT_ID`<br>`REDDIT_CLIENT_SECRET`<br>`REDDIT_USER_AGENT` | App OAuth no Reddit Developer Portal | Permite minerar subreddits específicos |
| **Bluesky** | `BLUESKY_HANDLE`<br>`BLUESKY_APP_PASSWORD` | Senha de aplicativo na conta Bluesky | Mineração na rede social AT Protocol |
| **X / Twitter** | `X_BEARER_TOKEN` | Portal de Desenvolvedores do X | Requer plano pago oficial da API |
| **Discord** | `DISCORD_BOT_TOKEN`<br>`DISCORD_GUILD_IDS` | Bot no Discord Developer Portal | Requer autorização prévia de admins de servidores |

---

## 📁 Onde Ficam os Dados Coletados (`data/`)

Todos os dados minerados ficam organizados na pasta `data/` seguindo padrões normativos de reprodutibilidade científica:

```text
data/
├── raw/{source}/{run_id}/       # Respostas originais das APIs em JSONL comprimido (.gz)
├── items/{run_id}/items.jsonl   # Dados normalizados com schema unificado
├── items/{run_id}/items_deduped.jsonl # Dados após desduplicação (URLs únicas)
├── runs/{run_id}/manifest.json  # Manifesto científico com hash SHA-256 e timestamps
└── msrkit.duckdb                # Banco DuckDB local para consultas SQL ultra-rápidas
```

- **Rastreabilidade total:** Cada item possui um identificador de proveniência (`provenance`) registrando a query exata, timestamp em UTC e hash da requisição original.
- **Segurança de redistribuição:** O exportador respeita os termos de serviço das plataformas, exportando metadados de forma segura para citação e publicação acadêmica.

---

## 🛡️ Princípios Éticos e Rigor Científico

1. **Apenas APIs Oficiais:** Não realizamos scraping nem violação de termos de uso de nenhuma plataforma.
2. **Respeito aos Limites de Taxa (Rate Limits):** O MSR-Kit implementa um algoritmo *Token Bucket Governor* que controla o fluxo de requisições de forma determinística, evitando sobrecarga nos servidores das fontes.
3. **Plataformas Fechadas:** O LinkedIn é permanentemente não suportado devido à ausência de API pública de pesquisa para literatura cinza.
4. **Anonimização e Ética:** Não coletamos dados pessoais sensíveis, preservando apenas handles públicos de autores e links de conteúdo aberto.

---

## 🧪 Testes e Qualidade de Código

Para executar os testes automatizados e o linter (no ambiente de desenvolvimento):

```bash
# Executar a suíte completa de testes (200+ testes unitários e de integração):
pytest

# Verificar conformidade com o linter:
ruff check src/ tests/
```

---

## 📄 Licença

Este projeto está licenciado sob a **Apache License 2.0**. Consulte o arquivo [LICENSE](LICENSE) para mais detalhes.
