# MSR-Kit

[![CI](https://github.com/Icar0S/Miner-Tests-Agents-Rag/actions/workflows/ci.yml/badge.svg)](https://github.com/Icar0S/Miner-Tests-Agents-Rag/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

**Minerador de literatura cinza por APIs oficiais**, para pesquisa empírica em engenharia de software.

O MSR-Kit coleta publicações e repositórios de GitHub, Stack Exchange, Hugging Face, Hacker News, dev.to e feeds RSS, sempre por API oficial ou feed público, sem scraping. Depois disso, conduz o estudo até o fim: evidência de uso, triagem e extração manual com dupla codificação, validação do próprio instrumento, análises e o pacote para o Zenodo. Tudo é declarado num protocolo YAML, então o mesmo minerador serve a outros estudos.

O primeiro estudo que ele apoia é o **Estudo E2** de uma revisão multivocal sobre **teste de sistemas LLM com RAG e de sistemas baseados em agentes**.

## O que o minerador responde

| Questão de pesquisa | Como o MSR-Kit chega lá |
|---|---|
| **RQ1** Ferramentas usadas para testar RAG, com nível de evidência | `extract` classifica cada detecção em N1 (menção), N2 (import, config ou dependência) ou N3 (uso em CI sustentado); `analyze frequency` conta por nível e sistema |
| **RQ2** Métodos de teste de RAG e o tipo de oráculo | `analyze frequency` para métodos; `analyze oracles` para a escada de oráculos e o teste de H2 |
| **RQ3** Ferramentas usadas para testar agentes | as mesmas análises da RQ1, filtradas pelo rótulo `agente` |
| **RQ4** Métodos de teste de agentes e os modos de falha que cobrem | `analyze coverage`: matriz modo de falha (FP1–FP13, AF1–AF12) × ferramenta/método |
| **RQ5** O que transfere de RAG para agentes e o que é novo | `analyze compare`: entradas compartilhadas, só de RAG e só de agentes, mais a curva de saturação |

## Início rápido

Requer Python 3.11 ou 3.12.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # acrescente ,analysis para LDA/k-means
cp .env.example .env               # termos, janela e fontes (ver "Parâmetros da busca e fontes")

msrkit                             # abre diretamente o menu interativo guiado
```

O comando `msrkit` conduz todo o fluxo de pesquisa passo a passo (mineração, deduplicação, extração léxica, triagem visual TUI, codificação Anexo A e resultados PRISMA).

Caso prefira executar tarefas diretamente via linha de comando ou em automações de CI:
```bash
msrkit run protocols/v0_rag_agents_testing.yaml --source hackernews --limit 20
msrkit review-tui --coder seu_nome     # triagem interativa visual no terminal (TUI)
msrkit coding-tui --coder seu_nome     # extração técnica do Anexo A (TUI)
msrkit prisma                          # gera relatório e diagrama PRISMA (incluindo prisma.tikz)
msrkit report                          # relatório da mineração: janela, fontes, termos e quantidades
msrkit --help                          # lista todos os comandos disponíveis para automação
```

Prefere não instalar nada no sistema? Use o Docker:

```bash
docker compose build
docker compose create msrkit 
docker start -ai msrkit                                # abre o msrkit pronto no terminal
```

O container monta `./data`, `./protocols` e `./src`: os resultados aparecem na sua pasta `data/` e mudanças no código valem sem rebuild. Ao iniciar, o `msrkit` abre diretamente o menu interativo.

## Fluxo do estudo

Cada etapa grava em `data/` e pode ser refeita sem repetir as anteriores.

```text
coleta ──► evidência ──► triagem ──► extração ──► validação ──► análise ──► publicação
```

| Etapa | Comandos | O que produz |
|---|---|---|
| **1. Coleta** | `validate` → `plan --estimate` → `run` (retomável com `--resume`) → `dedupe` | itens normalizados, manifesto com hashes de cada resposta, duplicatas removidas por URL, conteúdo e quase-duplicata |
| **2. Evidência** | `enrich` → `extract` | sinais de repositório (CI, dependências, commits, contribuidores) e detecções N1/N2/N3 com trecho de evidência |
| **3. Triagem** | `review-tui`, `screen interactive` (ou `screen export` → `screen import`) | triagem interativa com interface TUI (Textual) no terminal, CLI assistido ou via planilha |
| **4. Extração** | `coding sample` → `coding export` → preencher → `coding import` → `agreement` | formulário do Anexo A e qualidade (§9); κ de Cohen por dimensão na dupla codificação |
| **5. Validação** | `recall gold.yaml`, `precision sample` → julgar → `precision score` | recall sobre gold set e precisão ponderada por estrato |
| **6. Análise** | `analyze frequency`, `coverage`, `oracles`, `topics`, `compare` | CSVs em `data/reports/<run>/analysis/` |
| **7. Publicação** | `prisma`, `package` | fluxo PRISMA (JSON, Markdown e TikZ para LaTeX) e pacote Zenodo com checksums |

As análises aceitam `--basis detections` (automática) ou `--basis coding` (manual, por consenso entre codificadores). O passo a passo com exemplos está no [manual](docs/manual.md#16-fluxo-completo-do-estudo-da-coleta-ao-pacote).

## Comandos

| Grupo | Comando | Para quê |
|---|---|---|
| Coleta | `sources` | adaptadores, disponibilidade e políticas (`--md` para Markdown) |
| | `validate` | valida protocolo, gazetteer e credenciais, sem rede |
| | `plan` | partições e orçamento de requisições; `--estimate` consulta os totais reais |
| | `run` | coleta (`-s` fonte, `-l` limite, `--resume <run_id>`) |
| | `normalize` | reprocessa os dados brutos sem rede |
| | `fetch` | texto das páginas linkadas (opt-in, respeita robots.txt, fica só local) |
| Corpus | `dedupe` | três passadas; `--near-threshold` (0,85; `0` desativa) |
| | `stats`, `export` | resumo da coleta; exporta CSV, JSONL ou DuckDB |
| Evidência | `enrich`, `extract` | sinais de repositório (`--graphql` para lote em query única GraphQL v4); detecções com nível de evidência |
| Revisão | `review-tui`, `screen`, `coding`, `agreement` | triagem interativa no terminal (Textual TUI / Rich CLI), formulário de extração, concordância |
| Validação | `recall`, `precision` | recall sobre gold set; amostra e cálculo de precisão |
| Análise | `analyze` | `frequency`, `coverage`, `oracles`, `topics`, `compare` |
| Publicação | `prisma`, `package` | fluxo PRISMA (Markdown, JSON e TikZ LaTeX); pacote Zenodo |

`msrkit <comando> --help` lista todas as opções. As flags mais usadas: `--run <id>` escolhe a execução (padrão: a mais recente), `-a/--all` consolida todas as execuções, `-f/--format` e `-o/--output` controlam a exportação.

## Protocolo

O protocolo ([`protocols/v0_rag_agents_testing.yaml`](protocols/v0_rag_agents_testing.yaml)) é o contrato do estudo. O que o minerador faz vem dele, não do código:

| Seção | Define |
|---|---|
| `window`, `terms`, `terms_by_language`, `languages` | janela temporal e léxico; `terms_by_language` acrescenta léxicos de outros idiomas, e o E2 busca só em inglês |
| `sources` | fontes ativas, tipos de item, consultas literais por fonte (`queries`, com modelos `{anchor}`/`{repo}`) e `census: true` para coletar a fonte inteira na janela, sem o teto por consulta |
| `concepts`, `concept_queries` | grupos de léxico que rotulam cada item (`rag`, `agente`, `teste`) |
| `gazetteer` | catálogo de ferramentas e métodos ([`protocols/gazetteer.yaml`](protocols/gazetteer.yaml)) |
| `screening` | critérios de inclusão e exclusão e tamanho do lote |
| `coding` | campos do formulário, catálogo de modos de falha e parâmetros da dupla codificação |
| `sampling` | `strata: year`: cada consulta das fontes que filtram por data roda uma vez por ano da janela, com o total disponível de cada estrato no manifesto |
| `limits` | tetos de itens e de requisições por fonte e o que fazer quando a cota diária esgota |

`msrkit validate` confere tudo isso antes de qualquer requisição.

## Parâmetros da busca e fontes (`.env`)

`cp .env.example .env` e ajuste. O `msrkit` carrega o `.env` antes de cada comando; variável já definida no shell ou no CI tem precedência. O arquivo tem quatro seções:

**1. Parâmetros da busca.** Sobrepõem o protocolo, e o `.env.example` traz os valores definidos para o Estudo E2, iguais aos do protocolo. O manifesto de cada execução registra os valores efetivos e quais vieram do `.env`; `msrkit validate` avisa se divergem do protocolo versionado.

| Variável | Valor no E2 |
|---|---|
| `MSRKIT_WINDOW_SINCE`, `MSRKIT_WINDOW_UNTIL` | `2023-01-01` a `2026-08-31` (§7.5: RAG e agentes como prática de engenharia, até o data-cutoff) |
| `MSRKIT_TERMS` | 20 termos em inglês, separados por `;`: RAG, LLM em geral (juiz, golden dataset, regressão, red teaming), vocabulário acadêmico (metamorphic testing, test oracle) e agentes (trajectory, tool calling, multi-agent) |

**2–4. Fontes por condição de acesso.** Só as fontes listadas nos grupos pública e manual são coletadas; as pagas e inviáveis ficam desligadas mesmo que o protocolo as habilite.

| Grupo | Variável | Fontes | Credenciais |
|---|---|---|---|
| Prontas, sem configuração | `MSRKIT_SOURCES_PUBLIC` | hackernews, devto, stackexchange, huggingface, rss | opcionais: `STACKEXCHANGE_KEY` (300 → 10.000 req/dia), `HF_TOKEN` |
| Precisam da sua configuração | `MSRKIT_SOURCES_MANUAL` | github, bluesky | `GITHUB_TOKEN` (5.000 req/h e busca de código; necessária para N2/N3), `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`; sem credencial a fonte é pulada e o motivo vai para o manifesto |
| Pagas | `MSRKIT_SOURCES_PAID` | x_twitter | cobra por post lido; adaptador é stub |
| Inviáveis | `MSRKIT_SOURCES_UNAVAILABLE` | linkedin, discord | sem API de busca ou sem acesso sem autorização de administradores |

O Reddit tem variáveis na seção 3, mas fica fora dos grupos: credencial nova exige aprovação prévia do Reddit e foi desativado no E2 (ADR-014). Google Custom Search e Bing não têm adaptador (fechada a novos clientes e aposentada). Viabilidade de cada plataforma, verificada em set/2026: [`docs/sources.md`](docs/sources.md).

### Coleta no GitHub Actions

O workflow **Coleta** (`.github/workflows/collect.yml`, disparo manual) roda o fluxo inicial num runner com internet aberta e publica um dataset de teste como artefato: CSV dos itens, manifesto, detecções, planilha de triagem em branco, PRISMA, relatório da mineração (`relatorio_mineracao.md`) e pacote redigido. O relatório da mineração (janela, fontes, termos buscados, quantidades e avisos) também aparece no resumo da execução, na página do run no Actions. Os termos e a janela vêm do protocolo versionado; credenciais opcionais vêm dos secrets `MSRKIT_GITHUB_TOKEN`, `STACKEXCHANGE_KEY`, `HF_TOKEN`, `BLUESKY_HANDLE` e `BLUESKY_APP_PASSWORD`. Por padrão coleta as fontes públicas com no máximo 10 itens por consulta, uma amostra espalhada pelo léxico (`msrkit run --per-query-limit`).

O mesmo workflow roda **todo dia (07:23 UTC) só com os feeds RSS** — Medium e blogs —, porque cada feed traz apenas os ~10 posts mais recentes e a API do Medium não tem leitura nem busca. Os dados acumulam entre execuções (cache do Actions) e o artefato traz `rss_acumulado.csv` consolidado e deduplicado. O agendamento só vale a partir do `main`; como os posts são novos, a execução agendada estende o fim da janela até a data do dia, o que fica registrado no manifesto.

### Coleta completa local

O job do Actions tem 240 minutos, e uma coleta no tamanho da oficial (100 itens por consulta e todos os repositórios do GitHub enriquecidos) passa disso. O script `scripts/coleta_local.py` roda as mesmas etapas do workflow **Coleta** na sua máquina ou no container, sem teto de tempo: validação, coleta, deduplicação, enriquecimento (GraphQL), extração, planilha de triagem, PRISMA, relatório da mineração, CSV, pacote, análises preliminares A1–A6 e amostra de precisão.

1. Crie o `.env` a partir do `.env.example` e preencha `GITHUB_TOKEN` (obrigatório para o GitHub), `STACKEXCHANGE_KEY` e `HF_TOKEN` (aumentam as cotas). Janela e termos vêm só do protocolo: o script ignora `MSRKIT_WINDOW_*` e `MSRKIT_TERMS*` do `.env`, como no Actions.
2. Rode, na raiz do repositório:

```bash
python scripts/coleta_local.py                                   # nativo (Python com o msrkit instalado)
docker compose run --rm msrkit python scripts/coleta_local.py    # ou no container
```

Padrões: 100 itens por consulta, todos os repositórios enriquecidos, as fontes públicas mais o GitHub, sem o RSS (os feeds só têm posts recentes, sempre fora da janela; o RSS fica na coleta diária do Actions). O enriquecimento reaproveita os sinais de repositórios que outra execução enriqueceu nos últimos 14 dias com a mesma janela (`--reaproveitar-dias 0` enriquece tudo de novo). Veja `--help` para mudar (`--por-consulta`, `--enriquecer`, `--fontes-manuais ""` para deixar o GitHub de fora, `--sem-analises`). A saída vai para `data/` e o log completo para `data/logs/`. No Windows, o script impede a suspensão por inatividade enquanto roda. Se a coleta cair, ele mostra o comando para continuar de onde parou:

```bash
python scripts/coleta_local.py --retomar <run_id>
```

Antes de coletar, o script mostra o commit do código e avisa se há alterações sem commit: o manifesto e o pacote registram esse commit, e com alterações pendentes ele não reproduz a execução (ADR-060). O relatório da mineração traz, além das contagens, a seção **Amostragem** (fração examinada de cada grupo de consultas e o peso dos itens, ADR-058) e o que a extração deixou de fora (ADR-056).

## Dados

```text
data/
├── raw/{fonte}/{run}/        respostas originais das APIs (jsonl.gz, imutáveis)
├── items/{run}/              itens normalizados, deduplicados e dedupe_report.json
├── runs/{run}/               manifest.json (consultas, partições, hashes) e descartes
├── enrich/ extract/          sinais de repositório e detecções
├── screening/ coding/        planilhas, decisões, codificações e concordância
├── validation/ reports/      precisão, PRISMA e análises
└── packages/                 pacotes para o Zenodo
```

Cada item carrega sua proveniência: consulta, partição, versão do adaptador, horário e SHA-256 da resposta bruta.

## Ética e redistribuição

- Coleta só de conteúdo público, por API oficial ou feed público, com limites de taxa aplicados antes de cada requisição e cotas diárias persistidas em disco.
- O texto integral só é redistribuído onde a licença permite (Stack Exchange, CC BY-SA). As demais fontes saem com metadados, URL e campos extraídos. `export --include-body` recusa fontes `metadata_only`.
- O pacote do Zenodo pseudonimiza os identificadores de autor e nunca inclui respostas brutas nem o conteúdo de arquivos de repositório.

## Desenvolvimento

O CI roda exatamente estes passos, em Python 3.11 e 3.12:

```bash
pip install -e ".[dev,analysis]"
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/                        # strict
pytest --cov=src/msrkit tests/   # cobertura mínima de 80%
```

Os testes de contrato reproduzem respostas reais gravadas em `tests/cassettes/`, sem rede. Para gravar um caso novo, rode `MSRKIT_RECORD=1 pytest tests/test_cassettes.py -k <caso>`. Para acrescentar uma fonte, veja o [guia de adaptadores](docs/manual.md#11-guia-de-desenvolvimento-testes-e-extensão).

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/manual.md`](docs/manual.md) | arquitetura, referência completa dos comandos, esquema dos dados |
| [`docs/PLANO.md`](docs/PLANO.md) | plano de implementação, status de cada etapa e pendências da pesquisa |
| [`docs/decisions.md`](docs/decisions.md) | registro de decisões (ADRs) com contexto e justificativa |
| [`docs/sources.md`](docs/sources.md) | viabilidade e limites de cada plataforma |

## Licença

[Apache License 2.0](LICENSE).
