# Manual Completo e Documentação Técnica do MSR-Kit

> **MSR-Kit: Mining grey literature through official APIs for empirical software engineering research.**  
> *Versão do Software: 0.1.0 | Licença: Apache 2.0*

---

## 📑 Sumário Executivo

1. [Visão Geral e Contexto Científico](#1-visão-geral-e-contexto-científico)
2. [Arquitetura do Sistema e Componentes Internos](#2-arquitetura-do-sistema-e-componentes-internos)
3. [Instalação e Modos de Execução](#3-instalação-e-modos-de-execução)
4. [Guia do Menu Interativo (`msrkit menu`)](#4-guia-do-menu-interativo-msrkit-menu)
5. [Referência Completa de Comandos da CLI](#5-referência-completa-de-comandos-da-cli) — inclui o [fluxo completo do estudo](#16-fluxo-completo-do-estudo-da-coleta-ao-pacote)
6. [Contrato Científico: Protocolo YAML](#6-contrato-científico-protocolo-yaml)
7. [Matriz Detalhada de Fontes e Credenciais](#7-matriz-detalhada-de-fontes-e-credenciais)
8. [Estrutura de Armazenamento e Esquema dos Dados](#8-estrutura-de-armazenamento-e-esquema-dos-dados)
9. [Desduplicação e Processamento de Texto](#9-desduplicação-e-processamento-de-texto)
10. [Políticas Éticas e Termos de Uso (Redistribuição)](#10-políticas-éticas-e-termos-de-uso-redistribuição)
11. [Guia de Desenvolvimento, Testes e Extensão](#11-guia-de-desenvolvimento-testes-e-extensão)

---

## 1. Visão Geral e Contexto Científico

O **MSR-Kit** é uma infraestrutura de software de alta confiabilidade desenvolvida para viabilizar **Estudos de Engenharia de Software Empírica (MSR/SLR)** baseados em **literatura cinza** (*grey literature*: discussões técnicas, postagens em blogs de engenharia, repositórios de código aberto, perguntas em fóruns especializados e feeds de pesquisa).

### 🎯 Pesquisa Alvo
A ferramenta foi projetada para responder a 5 Questões de Pesquisa (Research Questions - RQs) focadas em **ferramentas e métodos de teste para sistemas baseados em LLMs com RAG e Agentes Autônomos**:
- **RQ1 (Ferramentas em RAG e Níveis de Evidência):** Catalogação de ferramentas de teste para RAG e classificação de evidência (N1: anedótico, N2: implementação open-source demonstrada, N3: benchmark empírico formal).
- **RQ2 (Métodos e Oráculos em RAG):** Identificação de técnicas de teste (ex: datasets sintéticos, testes metamórficos, oráculos LLM-as-a-judge vs. asserções determinísticas).
- **RQ3 (Ferramentas em Sistemas Agênticos):** Catalogação de frameworks de teste e observabilidade para agentes autônomos e arquiteturas multiagente.
- **RQ4 (Falhas e Métodos em Agentes):** Mapeamento de falhas típicas (deriva de objetivo, loops infinitos de ferramentas, envenenamento de memória, impasses de coordenação) e suas abordagens de validação.
- **RQ5 (Transferibilidade e Novas Dimensões):** Comparação entre técnicas transferíveis de RAG para Agentes versus dimensões genuinamente novas em agentes (espaços de ação não determinísticos, trajetórias multi-turn).

### 🛡️ Princípios Fundamentais
1. **Exclusividade de APIs Oficiais:** Não é realizado web scraping, parsing de HTML não autorizado ou simulação de navegadores (headless). Toda a comunicação ocorre estritamente por endpoints oficiais.
2. **Princípio do Conservadorismo (§20.4):** Diante de qualquer ambiguidade de rede ou plataforma, o sistema adota a postura que faz menos requisições, retém menos dados e falha o mais cedo possível (*fail-fast*).
3. **Reprodutibilidade e Rastreabilidade Absoluta:** Toda coleta registra parâmetros exatos, hashes criptográficos SHA-256 de requisições e respostas, gerando manifestos imutáveis.

---

## 2. Arquitetura do Sistema e Componentes Internos

O fluxo de dados do MSR-Kit foi desenhado para garantir isolamento em camadas, determinismo e tolerância a falhas.

```mermaid
flowchart TD
    subgraph Entrada
        P[Protocolo YAML] --> CLI[Interface CLI / Menu]
    end

    subgraph Controle & Governança
        CLI --> REG[Adapter Registry]
        CLI --> PART[Partitioning Engine]
        REG --> GOV[Governor Rate Limiter]
        GOV --> STATE[(data/.governor_state.json)]
    end

    subgraph Coleta
        GOV --> ADAPT[Source Adapters 11 Fontes]
        ADAPT --> NET[APIs Oficiais HTTP/REST]
        NET --> RAW[(data/raw/ gzip JSONL)]
    end

    subgraph Processamento
        RAW --> NORM[Normalizer]
        NORM --> KEYW[Keyword Context Matcher]
        KEYW --> DEDUP[Deduplication Engine]
    end

    subgraph Armazenamento & Exportação
        DEDUP --> ITEMS[(data/items/ JSONL)]
        ITEMS --> MANIFEST[(data/runs/ manifest.json)]
        ITEMS --> DUCK[(data/msrkit.duckdb SQL)]
        DUCK --> EXPORT[Exportador CSV / JSONL]
    end

    subgraph Evidência & Revisão
        ITEMS --> ENRICH[enrich: árvore, CI, manifestos, commits]
        ENRICH --> EXTRACT[extract: gazetteer → N1/N2/N3]
        ITEMS --> EXTRACT
        EXTRACT --> SCREEN[screen: triagem por lotes ordenados]
        SCREEN --> CODING[coding: formulário Anexo A + §9]
        CODING --> AGREE[agreement: κ de Cohen]
    end

    subgraph Validação, Análise & Publicação
        EXTRACT --> VALID[recall / precision]
        CODING --> ANALYZE[analyze: A1–A6]
        SCREEN --> PRISMA[prisma]
        ANALYZE --> PKG[package: Zenodo]
        PRISMA --> PKG
    end
```

### Detalhamento dos Componentes Principais:

#### 1. Governor (`src/msrkit/governor.py`) — ADR-001 & ADR-002
- **Token Bucket Proativo:** Bloqueia chamadas *antes* do envio do pacote HTTP (`acquire()`), respeitando a frequência máxima cadastrada na política de cada fonte.
- **Persistência de Quota Diária em Disco:** Salva o volume de requisições do dia em `data/.governor_state.json`. Ao reiniciar a aplicação ou rodar via containers, a contagem diária não é zerada, impedindo o banimento por estouro de cota (ex: limite de 300 req/dia do Stack Exchange sem token).
- **Backoff Reativo:** Inspeciona cabeçalhos `Retry-After`, `x-ratelimit-remaining`, `x-ratelimit-reset` e diretivas `backoff` do Stack Exchange, congelando as requisições pelo tempo determinado pelo servidor remoto.

#### 2. Partitioning Engine (`src/msrkit/partition.py`) — ADR-003
- **Divisão Temporal Binária:** Contorna o limite rígido de 1.000 resultados da API do GitHub. Se uma consulta preliminar (`estimate()`) indica mais de 1.000 itens, o intervalo temporal `[since, until]` é dividido recursivamente pela metade.
- **Eixos Secundários de Partição:** Se um intervalo de 1 dia ainda exceder 1.000 itens, a partição é refinada por linguagens de programação e faixas de estrelas (`stars:a..b`). Caso atinja o limite indivisível, registra `truncated: true` no manifesto científico.

#### 3. BaseAdapter & Registry (`src/msrkit/adapters/`)
- Todos os adaptadores herdam de `BaseAdapter` (`src/msrkit/adapters/base.py`), que provê cliente `httpx` pré-configurado, tratamento de timeouts, cálculo automático de SHA-256 de payloads e retentativas com jitter exponencial (`tenacity`).
- Descoberta dinâmica através de `src/msrkit/registry.py`.

#### 4. Keyword Matcher (`src/msrkit/keywords.py`) — ADR-011
- Realiza busca exata por frases com verificação estrita de fronteiras de palavras (`\b`), insensível a maiúsculas/minúsculas.
- Extrai janelas de contexto de **±40 tokens** ao redor de cada ocorrência do termo nos campos `title`, `body`, `tags` e `path`.

#### 5. Deduplication Engine (`src/msrkit/dedupe.py`) — ADR-012
- **Passo 1 (URL Canônica):** Converte esquema e host para minúsculas, remove parâmetros de rastreamento analítico (`utm_*`, `ref`, `source`) e padroniza barras finais.
- **Passo 2 (Hash de Conteúdo):** Calcula hash SHA-256 sobre a concatenação normalizada de `title + " " + body`.
- Suporta desduplicação da última execução isolada ou de todo o histórico acumulado no corpus.

#### 6. Storage & DuckDB (`src/msrkit/storage.py`)
- **Raw Storage:** Grava respostas brutas das APIs em `data/raw/{source}/{run_id}/{partition_hash}.jsonl.gz` de forma imutável.
- **Items Storage:** Grava itens normalizados em `data/items/{run_id}/items.jsonl` e desduplicados em `items_deduped.jsonl`.
- **DuckDB Layer:** Ingere itens no banco analítico local `data/msrkit.duckdb` (tabela relacional `items`), permitindo queries SQL de alta performance e filtragens complexas.

#### 7. Gazetteer (`src/msrkit/gazetteer.py`, `protocols/gazetteer.yaml`) — ADR-021
- Catálogo versionado de ferramentas e métodos: aliases (menção N1), imports Python, pacotes, arquivos de configuração e comandos de CLI (sinais N2/N3), família, sistema-alvo, degrau típico na escada de oráculos e origem (`seed` ou `discovery`).
- Expande os modelos de consulta `{anchor}` e `{repo}` do protocolo; nomes ambíguos só contam com contexto do léxico ou sinal estrutural.

#### 8. Enriquecimento e extração (`enrich.py`, `extract.py`) — ADR-023, ADR-056
- `enrich` lê, pela API oficial e sob o Governor, árvore, workflows de CI, manifestos de dependência, contribuidores e meses com commits de cada repositório. Os workflows que a busca de código apontou são lidos primeiro (até 20), antes dos dez primeiros da árvore. Com `--reuse-days`, só reaproveita o sinal de outra execução que leu esses arquivos e que ainda traz o conteúdo deles.
- `extract` aplica o gazetteer e atribui o nível de evidência: N1 menção em texto; N2 import, configuração ou dependência; N3 invocação em CI com commits em ≥2 meses e ≥2 contribuidores.
- **O que conta como invocação em CI:** um passo `run:` ou `uses:` do workflow que usa uma ação do repositório da ferramenta (`uses`), chama o comando dela (`cli`), roda ou importa o módulo (`module`) ou instala o pacote (`install`). O nome da ferramenta solto num comando (`name`) vale no máximo N2. Comentários, `name:`, `env:`, `with:`, o que só imprime (`echo`) e o que a execução exclui (`-p no:x`, `-m "not x"`) não contam. A forma fica na coluna `via` da detecção.
- **Onde os sinais do repositório entram:** só no item do repositório ou, sem ele, num hit de código. Repositório conhecido apenas por issues fica com as menções (N1). Repositório que tem o nome da ferramenta (o próprio, uma cópia ou um homônimo) não conta como uso dela.
- `extract` grava `extract_stats.json` com o que cada regra deixou de fora; o relatório da mineração mostra.

#### 8.1 Pesos de amostragem (`weights.py`) — ADR-058
- Consulta que para no limite de itens é amostra. O manifesto guarda, por consulta, os itens mantidos, os descartados por filtro local e o total que a fonte informa; a fração é examinados ÷ disponíveis e o peso de cada item, o inverso. Consulta truncada sem total fica com fração desconhecida e peso 1, registrado como tal.
- O relatório da mineração traz a seção **Amostragem** com as frações por fonte, tipo, consulta e ano; `analysis/artigo2.py` usa os pesos nas leituras ponderadas.

#### 9. Revisão manual (`screening.py`, `coding.py`, `agreement.py`) — ADR-024, 027–029
- Planilhas CSV (`;`, UTF-8 com BOM) validadas por inteiro antes de gravar; uma decisão por (item, codificador).
- Triagem ordenada por relevância em lotes fixos; formulário declarativo no protocolo; κ de Cohen por dimensão sobre amostra estratificada de dupla codificação.

#### 10. Validação do instrumento (`validation.py`) — ADR-031, 032
- Recall sobre gold set curado antes da coleta; precisão sobre amostra estratificada (fonte × nível) ponderada pela população do estrato; testes de contrato sobre cassettes gravados.

#### 11. Análises e descoberta (`analysis.py`, `discovery.py`) — ADR-035–039
- Unidades de análise (repositório ou item) a partir das detecções ou da codificação por consenso; frequência, coocorrência, cobertura de modos de falha, escada de oráculos e H2, comparação RAG × agentes e saturação; LDA e k-means no passe de descoberta aberta (extra opcional `analysis`).

#### 12. PRISMA e pacote (`prisma.py`, `package.py`) — ADR-030, 040, 058, 060
- Fluxo PRISMA derivado só dos registros da execução; pacote Zenodo com redação por política de redistribuição e checksums.
- O PRISMA separa o que o teto da própria fonte cortou do que o limite de itens da execução deixou de examinar (estimativa, limite superior: consultas se sobrepõem).
- O pacote registra o hash do protocolo igual ao do manifesto (os arquivos de `protocol/` vão com fim de linha LF), o commit e se havia alterações sem commit, na coleta e no empacotamento. Pacote feito com alterações pendentes sai marcado: faça o commit e rode `msrkit package --run <id>` de novo.

#### 13. CLI (`src/msrkit/cli/`) — ADR-033
- Pacote por etapa: `collect`, `evidence`, `review`, `validation`, `corpus`, `analyze`, `menu`; `app` e `DATA_DIR` expostos em `msrkit.cli`.

---

## 3. Instalação e Modos de Execução

Você pode executar o MSR-Kit de duas maneiras: utilizando Docker (totalmente isolado, sem poluir o sistema) ou em ambiente Python local (para desenvolvimento).

### Opção A: Executar com Docker (Recomendado)

O container oficial utiliza **Ubuntu 24.04 LTS (Noble Numbat)** e Python 3.12.

#### 1. Preparação (Apenas na 1ª vez):
No terminal da pasta do projeto, execute os 2 passos:
```bash
# 1. Construir a imagem local:
docker compose build

# 2. Criar o container fixo:
docker compose create msrkit
```
> O container fixo `msrkit` é criado mapeando as pastas `./data`, `./protocols` e `./src`. Ele permanece salvo no Docker Desktop e nunca é excluído ao fechar.

#### 2. Execução do Container Fixo:
Sempre que for utilizar o MSR-Kit, ligue o container com:
```bash
docker start -ai msrkit
```
- **Terminal Linux direto (`root@...:/app# `):** O container abre instantaneamente sem forçar a abertura do menu.
- **Execução livre:** Você pode rodar qualquer comando lá dentro:
  ```bash
  msrkit menu        # abre o menu interativo
  msrkit sources     # exibe status das fontes
  msrkit run protocols/v0_rag_agents_testing.yaml -s hackernews -l 10
  ```
- **Atualização ao vivo:** Graças ao volume `./src:/app/src`, qualquer mudança de código feita no computador reflete imediatamente dentro do container.
- **Para sair:** Digite `exit`. O container apenas pausa de forma limpa.

---

### Opção B: Executar com Python Nativo (Ambiente Local)

Requisitos: **Python 3.11+** e Git.

```bash
# 1. Criar ambiente virtual
python -m venv .venv

# 2. Ativar ambiente virtual
.venv\Scripts\activate      # No Windows
source .venv/bin/activate    # No Linux / macOS

# 3. Instalar o projeto em modo editável com dependências completas
pip install -e ".[dev]"
```

Após a instalação, o comando binário `msrkit` estará disponível no seu terminal.

---

## 4. Guia do Menu Interativo (`msrkit menu`)

O menu interativo foi construído com a biblioteca `rich` e foi desenhado para pesquisadores que desejam minerar, gerenciar fontes e exportar dados sem precisar memorizar comandos ou flags de linha de comando.

Para acessar:
- **No container Docker fixo:** `msrkit menu` (após ligar com `docker start -ai msrkit`)
- **No Python nativo:** `msrkit menu`

```text
╭────────────────────────────────────────────────────────╮
│ MSR-Kit v0.1.0                                         │
│ Mining grey literature through official APIs           │
╰────────────────────────────────────────────────────────╯

Escolha uma ação:
  1 - 🎯 Iniciar Mineração (escolher fonte e quantidade flexível)
  2 - ⚙️  Gerenciar Fontes (ativar/desativar com base na disponibilidade)
  3 - Status detalhado das fontes e políticas (sources)
  4 - Simulação de planejamento / Dry-Run (plan)
  5 - Desduplicar última coleta (dedupe)
  6 - Exportar última coleta em CSV (export -f csv)
  7 - Estatísticas da última coleta (stats)
  8 - Validar arquivo de protocolo (validate)
  0 - Sair
```

### Explicação Detalhada das Opções:

- **`1` - 🎯 Iniciar Mineração:**
  Exibe a lista das fontes habilitadas no protocolo e permite escolher se deseja coletar de **Todas as fontes** ou de uma **Fonte específica** (ex: `hackernews`, `devto`, `rss`, `github`). Em seguida, solicita a quantidade limite de itens (ou Enter para coletar tudo da janela temporal). Executa a coleta, grava os dados brutos e normalizados, e **gera imediatamente a planilha com todos os achados brutos** (com timestamp, ex: `data/resultados_YYYYMMDD_HHMMSS_brutos.csv`). Logo em seguida, oferece a opção opcional de rodar a desduplicação para gerar também a planilha desduplicada (`data/resultados_YYYYMMDD_HHMMSS_desduplicados.csv`), permitindo comparar o antes e depois.

- **`2` - ⚙️ Gerenciar Fontes:**
  Permite ativar ou desativar rapidamente fontes específicas dentro do protocolo YAML. Ideal quando uma fonte precisa ser temporariamente desabilitada por ausência de chave de API ou manutenção do serviço.

- **`3` - Status detalhado das fontes (`sources`):**
  Renderiza uma tabela completa contendo o status de cada um dos 11 adaptadores (`OK`, `DEGRADED`, `UNSUPPORTED`), a justificativa técnica, as variáveis de ambiente necessárias e as taxas de rate limit.

- **`4` - Simulação de planejamento / Dry-Run (`plan`):**
  Lê o protocolo e as datas configuradas e calcula previamente quantas partições serão necessárias e quantas requisições de rede serão consumidas, permitindo dimensionar a coleta antes de realizá-la.

- **`5` - Desduplicar última coleta (`dedupe`):**
  Aplica as regras de canonicalização de URL e hash de conteúdo. Oferece a escolha de desduplicar apenas a última coleta ou o histórico consolidado de coletas anteriores, com opção de exportar o CSV desduplicado imediatamente.

- **`6` - Exportar última coleta em CSV (`export -f csv`):**
  Gera a planilha pronta para análise no Excel, Google Sheets ou Pandas. Permite escolher entre a **versão desduplicada** ou a **versão bruta** (achados integrais), salvando com timestamp histórico em `data/resultados_YYYYMMDD_HHMMSS_...csv`.

- **`7` - Estatísticas da última coleta (`stats`):**
  Gera o relatório com o número total de itens, descartes por falta de termos, tempo de processamento e requisições gastas.

- **`8` - Validar arquivo de protocolo (`validate`):**
  Valida a sintaxe do arquivo de protocolo e checa as credenciais do `.env` sem gastar requisições de rede.

- **`0` - Sair:**
  Finaliza a execução do programa de forma limpa.

---

## 5. Referência Completa de Comandos da CLI

Todos os comandos aceitam a flag global `-v` / `--verbose` para saída de logs detalhados em nível `DEBUG`.

### 1. `msrkit sources`
Lista todos os adaptadores registrados, políticas e disponibilidades.
```bash
# Visualização em tabela rica no terminal:
msrkit sources

# Visualização formatada em Markdown (útil para relatórios e documentação):
msrkit sources --md
```

### 2. `msrkit validate`
Valida o esquema do protocolo de pesquisa e checa variáveis de ambiente sem realizar chamadas de rede.
```bash
msrkit validate protocols/v0_rag_agents_testing.yaml
```

### 3. `msrkit plan`
Executa o planejamento científico (Dry Run), calculando partições e orçamento de requisições.
```bash
# Planejar todas as fontes habilitadas:
msrkit plan protocols/v0_rag_agents_testing.yaml

# Planejar apenas para uma fonte específica:
msrkit plan protocols/v0_rag_agents_testing.yaml -s github
```

### 4. `msrkit run`
Executa a mineração real de dados de acordo com o protocolo.
```bash
# Mineração completa de todas as fontes:
msrkit run protocols/v0_rag_agents_testing.yaml

# Mineração filtrando uma fonte com limite máximo de itens:
msrkit run protocols/v0_rag_agents_testing.yaml --source hackernews --limit 50

# Retomar uma coleta interrompida sem recomeçar do zero:
msrkit run protocols/v0_rag_agents_testing.yaml --resume 20260922_153000_abc123
```

### 5. `msrkit dedupe`
Executa a desduplicação em três passadas (URL canônica, hash de conteúdo e quase-duplicatas por MinHash/LSH; `--near-threshold 0` desativa a terceira) e grava `dedupe_report.json` com os motivos.
```bash
# Desduplicar itens da última coleta:
msrkit dedupe

# Desduplicar uma coleta específica pelo Run ID:
msrkit dedupe --run 20260922_153000_abc123

# Desduplicar todo o histórico consolidado:
msrkit dedupe --all
```

### 6. `msrkit export`
Exporta os dados normalizados para diferentes formatos científicos e tabulares.
```bash
# Exportar última coleta para CSV padrão:
msrkit export -f csv -o data/meus_dados.csv

# Exportar para JSONL:
msrkit export -f jsonl -o data/dataset.jsonl

# Exportar para banco relacional DuckDB:
msrkit export -f duckdb -o data/msrkit.duckdb

# Exportar incluindo o texto completo do corpo (apenas para fontes compatíveis com a licença):
msrkit export -f csv -o data/completo.csv --include-body
```

### 7. `msrkit stats`
Exibe métricas detalhadas sobre as requisições, descartes e itens minerados.
```bash
# Estatísticas da última coleta:
msrkit stats

# Estatísticas de uma execução específica:
msrkit stats --run 20260922_153000_abc123
```

### 8. `msrkit normalize`
Reprocessa dados brutos (`data/raw/`) sem gastar novas requisições de rede. Muito útil caso o algoritmo de casamento de palavras-chave ou regras de normalização sejam atualizados.
```bash
msrkit normalize --run 20260922_153000_abc123
```

### 9. `msrkit enrich` e `msrkit extract`
```bash
msrkit enrich                 # sinais de repositório (consome a cota do GitHub)
msrkit extract --top 30       # detecções com nível N1/N2/N3 e resumo por entrada
```

### 10. `msrkit screen`
```bash
msrkit screen export --coder ana            # lotes fixos ordenados por relevância
msrkit screen export --coder ana -b 1 -b 2  # só os lotes 1 e 2
msrkit screen import data/screening/<run>/sheet_ana.csv
msrkit screen status
```
Marque os critérios com `x`; `decision` aceita `include`, `exclude` ou `uncertain`. Uma exclusão exige um critério E marcado ou um motivo.

### 11. `msrkit coding` e `msrkit agreement`
```bash
msrkit coding sample                         # ≥20% dos incluídos, estratificado por fonte
msrkit coding export --coder bia --double-sample
msrkit coding import data/coding/<run>/form_bia.csv
msrkit agreement                             # κ por dimensão do §15
```
As colunas `auto_*` trazem sugestões do `extract`/`enrich`; `--prefill` as copia para o formulário (desativado por padrão para manter a codificação independente).

### 12. `msrkit recall` e `msrkit precision`
```bash
msrkit recall protocols/gold_set.yaml
msrkit precision sample --n 200
msrkit precision score data/validation/<run>/precision_sample.csv
```

### 13. `msrkit prisma` e `msrkit report`
`msrkit prisma` gera `prisma.json` e `prisma.md` (com diagrama Mermaid) a partir do manifesto, dos descartes, do relatório de deduplicação, das decisões e das codificações.

`msrkit report` gera o **relatório da mineração** (`relatorio_mineracao.md` e `.json`) em `reports/<run>/`, legível sem abrir o manifesto:

- **Resumo** da execução em um parágrafo;
- **Espaço temporal:** janela buscada, início, fim e duração da execução, datas dos itens por ano; se `MSRKIT_WINDOW_*` estendeu a janela (como na coleta diária de RSS), também a janela do protocolo e quantos itens ficam fora dela;
- **Locais:** consultas executadas/planejadas, requisições, itens retornados, descartados, únicos e truncamentos por fonte; fontes não consultadas e o motivo (fora desta execução, fonte paga, indisponível, desativada no protocolo); itens por fonte e tipo;
- **Termos buscados:** léxico por idioma, consultas literais por fonte, opções da execução, termos que mais casaram e os que não casaram nenhuma vez;
- **Quantidades:** funil (identificados → filtro local → duplicatas → únicos), conceitos do protocolo e, se houver, enriquecimento e detecções N1/N2/N3; com execuções anteriores em `data/` (a coleta diária de RSS as restaura do cache), quantos itens são novos e o total acumulado;
- **Avisos:** consultas planejadas que não rodaram, falhas, tetos de API, amostra cortada pelo limite de itens, itens fora da janela, deduplicação pendente.

```bash
msrkit report                 # execução mais recente
msrkit report --run <run_id>
```

Rode depois da coleta; as contagens de `dedupe`, `enrich` e `extract` entram quando já existirem. O workflow **Coleta** gera o relatório em toda execução e o mostra no resumo do job.

### 14. `msrkit analyze`
```bash
msrkit analyze frequency --basis detections   # ou --basis coding
msrkit analyze coverage                       # modo de falha × ferramenta/método
msrkit analyze oracles --threshold 0.75       # escada de oráculos e teste de H2
msrkit analyze topics --topics 12 --clusters 12   # requer pip install -e ".[analysis]"
msrkit analyze compare --window 2             # RAG × agentes e saturação
```

### 15. `msrkit package`
```bash
msrkit package --creator "Sobrenome, Nome" --gold protocols/gold_set.yaml
```

### 16. Fluxo completo do estudo: da coleta ao pacote

A ordem abaixo segue o Protocolo E2 v2. Cada etapa grava em `data/` e pode ser refeita sem repetir as anteriores.

| # | Etapa | Comando | Saída principal |
|---|---|---|---|
| 1 | Validar protocolo e credenciais | `msrkit validate` | — |
| 2 | Estimar partições e truncamento | `msrkit plan --estimate` | tabela no terminal |
| 3 | Coletar (retomável) | `msrkit run [--resume]` | `raw/`, `items/`, `runs/<run>/manifest.json` |
| 4 | Deduplicar (URL, conteúdo, quase-duplicata) | `msrkit dedupe` | `items_deduped.jsonl`, `dedupe_report.json` |
| 5 | Enriquecer repositórios GitHub | `msrkit enrich` | `enrich/<run>/github_repos.jsonl` |
| 6 | Extrair ferramentas e métodos | `msrkit extract` | `extract/<run>/detections.jsonl` |
| 7 | Triagem em lotes (dois codificadores) | `msrkit screen export --coder ana` → preencher → `msrkit screen import` | `screening/<run>/decisions.jsonl` |
| 8 | Amostra de dupla codificação | `msrkit coding sample` | `coding/<run>/double_sample.json` |
| 9 | Extração manual (Anexo A + §9) | `msrkit coding export --coder ana` → preencher → `msrkit coding import` | `coding/<run>/codings.jsonl` |
| 10 | Concordância | `msrkit agreement` | `agreement.json`, `disagreements.csv` |
| 11 | Validar o minerador | `msrkit recall gold.yaml`, `msrkit precision sample` → julgar → `msrkit precision score` | `validation/<run>/` |
| 12 | Fluxo PRISMA e relatório da mineração | `msrkit prisma`, `msrkit report` | `reports/<run>/prisma.md`, `reports/<run>/relatorio_mineracao.md` |
| 13 | Análises A1–A6 | `msrkit analyze` (`frequency`, `coverage`, `oracles`, `topics`, `compare`) | `reports/<run>/analysis/` |
| 14 | Pacote Zenodo | `msrkit package --creator "Sobrenome, Nome"` | `packages/msrkit_<run>.zip` |

---

## 6. Contrato Científico: Protocolo YAML

O arquivo de protocolo (ex: [protocols/v0_rag_agents_testing.yaml](../protocols/v0_rag_agents_testing.yaml)) é a especificação formal e reprodutível da sua pesquisa.

### Estrutura do Esquema:

```yaml
version: 0
name: v0_rag_agents_testing
description: >
  Coleta exploratória sobre ferramentas e métodos de teste para sistemas
  LLM com RAG e sistemas baseados em agentes.

# Janela temporal de busca (ISO 8601 YYYY-MM-DD)
window:
  since: "2023-01-01"
  until: "2026-08-31"

# Conjunto de termos / strings de busca exatas
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

# Idiomas desejados (códigos ISO 639-1)
languages:
  - en
  - pt

# Configuração individual das fontes
sources:
  hackernews:
    enabled: true
  devto:
    enabled: true
  rss:
    enabled: true
  github:
    enabled: true
    # Limitações opcionais por fonte:
    extra:
      min_stars: 10
  stackexchange:
    enabled: false
  reddit:
    enabled: false
  huggingface:
    enabled: false
```

---

### Seções adicionadas na Fase 2

| Seção | Para que serve | Decisão |
|---|---|---|
| `sources.<fonte>.queries` | Consultas literais na sintaxe da fonte (ex.: busca de código do GitHub), com modelos `{anchor}`/`{repo}` | ADR-020 |
| `concepts`, `concept_queries` | Grupos de léxico (`rag`, `agente`, `teste`) que rotulam cada item e geram consultas pelo produto dos grupos | ADR-022 |
| `gazetteer` | Caminho do catálogo de ferramentas e métodos, relativo ao protocolo | ADR-021 |
| `screening` | Critérios I/E e tamanho do lote da triagem | ADR-024, 027 |
| `coding` | Campos do formulário (Anexo A + §9), catálogo de modos de falha, taxa de dupla codificação e dimensões do κ | ADR-028, 029 |

## 7. Matriz Detalhada de Fontes e Credenciais

O MSR-Kit implementa adaptadores dedicados para 11 fontes de literatura cinza.

| Fonte | Chave no Protocolo | Autenticação | Rate Limit Padrão | Busca Textual | Filtro Data | Política de Redistribuição | Status |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- | :--- |
| **Hacker News** | `hackernews` | Pública (Sem chave) | 10 req / 1s | ✓ | ✓ | `metadata_only` | `OK` |
| **Dev.to** | `devto` | Pública (Sem chave) | 3 req / 1s | ✗ *(tags + filtro local)* | ✗ | `metadata_only` | `OK` |
| **Feeds RSS** | `rss` | Pública (Sem chave) | 5 req / 1s | ✗ *(posts recentes)* | ✗ | `metadata_only` | `OK` |
| **GitHub** | `github` | `GITHUB_TOKEN` (PAT) | 30 req / 60s (Token) | ✓ | ✓ | `metadata_only` | `OK` / `DEGRADED` |
| **Stack Exchange** | `stackexchange` | `STACKEXCHANGE_KEY` (Opcional) | 1 req / 60s (300/dia sem chave) | ✓ | ✓ | `full_text_with_attribution` | `OK` / `DEGRADED` |
| **Reddit** | `reddit` | OAuth2 (`CLIENT_ID` + `SECRET`) | 10 req / 60s | ✓ | ✗ | `metadata_only` | `OK` |
| **Hugging Face**| `huggingface` | `HF_TOKEN` (Opcional) | 5 req / 1s | ✓ | ✗ | `metadata_only` | `OK` / `DEGRADED` |
| **Bluesky** | `bluesky` | Handle + App Password | 5 req / 1s | ✓ | ✓ | `metadata_only` | `OK` |
| **X / Twitter** | `x_twitter` | `X_BEARER_TOKEN` (Plano pago) | 1 req / 1s | ✓ | ✓ | `metadata_only` | `UNSUPPORTED` / `DEGRADED` |
| **Discord** | `discord` | `DISCORD_BOT_TOKEN` + Guild IDs | 5 req / 1s | ✗ *(leitura de canais)* | ✗ | `metadata_only` | `UNSUPPORTED` |
| **LinkedIn** | `linkedin` | — *(Sem API pública)* | — | — | — | — | `UNSUPPORTED` *(Permanente)* |

### Como Configurar as Credenciais e os Parâmetros da Busca:
Copie o arquivo de exemplo [.env.example](../.env.example) para `.env`:
```bash
cp .env.example .env
```
O `msrkit` carrega `./.env` antes de cada comando; variáveis já definidas no shell ou no CI têm precedência (`MSRKIT_NO_DOTENV=1` desliga a leitura). O arquivo tem quatro seções (ADR-041):

1. **Parâmetros da busca** — `MSRKIT_WINDOW_SINCE`, `MSRKIT_WINDOW_UNTIL`, `MSRKIT_TERMS` e `MSRKIT_TERMS_<IDIOMA>` (termos separados por `;`). Sobrepõem o protocolo; variável vazia mantém o valor do protocolo. O manifesto registra `effective_protocol` (valores em vigor) e `env_overrides` (variáveis aplicadas), e `msrkit validate` avisa quando o `.env` diverge do arquivo versionado.
2. **Fontes prontas** — `MSRKIT_SOURCES_PUBLIC`, mais as chaves opcionais `STACKEXCHANGE_KEY` e `HF_TOKEN`.
3. **Fontes com configuração manual** — `MSRKIT_SOURCES_MANUAL` e as credenciais de GitHub e Bluesky; o Reddit tem variáveis, mas fica fora dos grupos no E2.
4. **Pagas ou inviáveis** — `MSRKIT_SOURCES_PAID` e `MSRKIT_SOURCES_UNAVAILABLE`, nunca coletadas.

Quando algum grupo está definido, exatamente as fontes dos grupos pública e manual são habilitadas; uma fonte precisa estar declarada no protocolo e em um único grupo.

Para uma amostra de teste espalhada por todos os termos, use `msrkit run --per-query-limit 10`: cada consulta (termo ou partição) traz no máximo 10 itens, e o teto fica registrado no manifesto (`run_options` e motivo `item_limit`). Sem rede local, o workflow **Coleta** do GitHub Actions faz o mesmo num runner e publica o dataset como artefato.

---

## 8. Estrutura de Armazenamento e Esquema dos Dados

Todos os dados coletados e processados são armazenados no diretório `data/`:

```text
data/
├── .governor_state.json        # Estado persistente de cotas diárias de requisições
├── raw/                        # Dados brutos imutáveis
│   └── {source}/{run_id}/
│       └── {partition_hash}.jsonl.gz
├── items/                      # Dados unificados e desduplicados
│   └── {run_id}/
│       ├── items.jsonl         # Itens normalizados com esquema canônico
│       └── items_deduped.jsonl # Itens após processo de desduplicação
├── runs/                       # Manifestos de auditoria científica
│   └── {run_id}/
│       ├── manifest.json       # Hashes SHA-256, consultas, partições (checkpoint a cada partição)
│       └── discarded.jsonl     # Itens descartados por filtro local, com motivo
├── enrich/{run_id}/            # Sinais de repositório (msrkit enrich)
├── extract/{run_id}/           # Detecções N1/N2/N3 (msrkit extract)
├── screening/{run_id}/         # Planilhas e decisions.jsonl
├── coding/{run_id}/            # Formulários, codings.jsonl, amostra dupla, concordância
├── validation/{run_id}/        # Amostra e relatório de precisão
├── reports/{run_id}/           # prisma.md/json e analysis/*.csv
├── packages/                   # Pacotes Zenodo (msrkit package)
└── msrkit.duckdb               # Banco de dados DuckDB local para análise SQL
```

### Esquema Canônico do Item (`Item`):
Cada publicação minerada é transformada no modelo Pydantic `Item`:
- **`id`**: Hash SHA-256 único de 16 caracteres (`source:native_id`).
- **`source`**: Identificador da fonte (ex: `"hackernews"`).
- **`kind`**: Tipo do artefato (`repo`, `article`, `post`, `thread`, `issue`, etc.).
- **`url`**: URL canônica da publicação.
- **`title`**: Título da publicação.
- **`body`**: Texto completo (se disponível e retido de acordo com a política).
- **`body_hash`**: Hash SHA-256 do corpo textual para checagem de integridade.
- **`author_handle`**: Identificador público do autor (sem dados pessoais sensíveis).
- **`created_at` / `updated_at`**: Timestamps normalizados em formato UTC.
- **`engagement`**: Métricas de popularidade (`stars`, `forks`, `votes`, `reactions`, `comments`, `views`).
- **`tech`**: Contexto técnico (`language`, `license`, `has_ci`, `has_tests`, `contributors`, `active_months`, `tags`).
- **`concepts`**: Grupos do protocolo cujo léxico casou (ex.: `rag`, `agente`, `teste`); o rótulo de sistema do E2 é `concepts ∩ {rag, agente}`.
- **`matched_terms`**: Lista de termos casados com a janela de contexto de ±40 tokens.
- **`provenance`**: Rastreabilidade científica (Run ID, query executada, partição, versão do adaptador, timestamp UTC, hash da resposta bruta e offset da linha no arquivo `.gz`).

---

## 9. Desduplicação e Processamento de Texto

A literatura cinza apresenta frequentes sobreposições entre diferentes canais (ex: um artigo no Dev.to compartilhado no Hacker News e espelhado via RSS).

### O Algoritmo de Desduplicação do MSR-Kit:
1. **Passo 1 (Normalização de URL):**
   - O esquema (`http`/`https`) e o domínio são transformados para minúsculas.
   - Parâmetros analíticos de rastreamento são completamente expurgados (`utm_source`, `utm_medium`, `utm_campaign`, `ref`, `source`, `fbclid`, etc.).
   - Barras no final de rotas são padronizadas (rotas não raiz perdem a barra final; rota raiz `/` é preservada).
   - Parâmetros de consulta restantes são ordenados alfabeticamente para equivalência determinística.
2. **Passo 2 (Hash de Conteúdo - Apenas com Corpo de Texto):**
   - Para evitar fusões falsas-positivas de entidades (ADR-012), o hash de conteúdo SHA-256 (`content_hash`) só é acionado quando há **corpo textual (`body`) não vazio**.
   - O título e o corpo do texto têm espaços em branco colapsados e são normalizados.
   - **Proteção para Código e Posts sem Corpo:** Arquivos de código (`ItemKind.CODE`) e publicações estritamente baseadas em links/título dependem exclusivamente da canonicalização de URL. Isso garante que arquivos com nomes universais em repositórios diferentes (como `test_rag.py`, `eval.py`, `conftest.py`) ou posts com títulos genéricos em fontes distintas **nunca sejam incorretamente descartados como duplicatas**.
3. **Passo 3 (Quase-duplicatas, ADR-026):**
   - Assinaturas MinHash (128 permutações, 5-shingles de palavras) e LSH (32 bandas) propõem pares candidatos, mantidos como duplicatas quando a similaridade de Jaccard estimada é ≥ `--near-threshold` (0,85 por padrão; `0` desativa).
   - Textos com menos de 30 tokens são ignorados. O relatório `dedupe_report.json` lista cada par removido com a similaridade, para calibrar o limiar no piloto.

---

## 10. Políticas Éticas e Termos de Uso (Redistribuição)

O MSR-Kit foi construído com salvaguardas explícitas de propriedade intelectual (ADR-010):
- **Fontes com política `metadata_only`:** GitHub, Dev.to, Reddit, Hugging Face, RSS, Bluesky e Hacker News são tratadas como `metadata_only`.
- **Fontes com política `full_text_with_attribution`:** Stack Exchange permite redistribuição sob licença Creative Commons (CC BY-SA).
- **Proteção no Exportador:** Se o usuário executar `msrkit export --include-body`, o exportador analisa todas as fontes contidas nos dados minerados. Se houver itens de fontes cuja política seja `metadata_only`, **o comando aborta imediatamente com erro (exit code 1)** antes de criar os arquivos, evitando a distribuição acidental de textos com direitos autorais.
- **Pacote Zenodo (`msrkit package`, ADR-040):** itens de fontes `metadata_only` perdem `body` e contextos (mantêm `body_hash`); identificadores de autor viram pseudônimos com sal não publicado, salvo `--keep-authors`; respostas brutas e conteúdo de arquivos de repositório nunca entram no pacote.

---

## 11. Guia de Desenvolvimento, Testes e Extensão

### Executar a Suíte de Testes
O projeto contém cerca de 400 testes unitários e de integração utilizando `pytest` e cassettes gravados (`vcrpy`). O CI roda `ruff check`, `ruff format --check`, `mypy` (strict) e a suíte com cobertura mínima de 80%:

```bash
# Executar todos os testes com isolamento de rede:
pytest

# Executar testes excluindo aqueles que fazem chamadas reais de rede:
pytest -m "not network"

# Verificar conformidade de estilo com o Ruff:
ruff check src/ tests/

# Formatação:
ruff format --check src/ tests/

# Checagem estrita de tipos com Mypy:
mypy src/

# Análises opcionais (LDA/k-means) e seus testes:
pip install -e ".[dev,analysis]"

# Gravar um cassette de contrato (exige rede; apague o arquivo para regravar):
MSRKIT_RECORD=1 pytest tests/test_cassettes.py -k stackexchange
```

### Como Adicionar um Novo Adaptador de Fonte
Para integrar uma nova API ao MSR-Kit:
1. Crie o arquivo `src/msrkit/adapters/minha_fonte.py`.
2. Herde da classe abstrata `BaseAdapter` (`src/msrkit/adapters/base.py`).
3. Defina os atributos de classe: `name`, `version` e `policy` (`SourcePolicy`).
4. Implemente obrigatoriamente os métodos abstratos:
   - `available() -> Availability`: Checa sem rede se credenciais ou dependências estão satisfeitas.
   - `estimate(q: Query) -> int | None`: Estima a contagem total de itens.
   - `search(q: Query) -> Iterator[RawItem]`: Consome a API utilizando obrigatoriamente `self._governed_get()`.
   - `normalize(raw: RawItem, terms: list[str] | None = None) -> Item`: Mapeia a resposta da API para o modelo canônico `Item`.
5. O adaptador será descoberto e registrado automaticamente no sistema via `src/msrkit/registry.py`.
6. Acrescente um caso em `tests/test_cassettes.py` e grave o cassette com `MSRKIT_RECORD=1`.
