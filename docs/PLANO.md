# Plano de trabalho — Protocolo E2 v2

Acompanhamento da implementação das **condições de execução** do MSR-Kit definidas na
§6.5 do Protocolo E2 v2 (setembro/2026). A coleta (semanas 8–10 do cronograma) só começa
quando todas as condições estiverem concluídas.

Legenda: ✅ concluído · 🔄 em andamento · ⏳ pendente

| Etapa | Descrição | Status |
|---|---|---|
| E0 | Documentação alinhada ao protocolo v2 (fontes, viabilidade, ADRs, README) | ✅ |
| C1 | **Particionamento acionado** — GitHub e Hacker News particionam por data quando a estimativa passa de 1.000 resultados | ✅ |
| C2 | **Truncamento registrado** — manifesto registra `truncated=true` em consultas cortadas e em fontes sem cobertura histórica (RSS) | ✅ |
| C3 | **Filtro local sem perda** — casamento de termos tolera flexões e hífens; descartes de dev.to/RSS são registrados no manifesto | ✅ |
| C6 | **Filtro de tags do Stack Exchange** — `tagged` com semântica OU, como documentado pela API | ✅ |
| C5 | **Léxico em português** — termos em pt no protocolo, com estrato identificável | ✅ |
| C4a | **Conteúdo integral — Stack Exchange:** respostas das threads coletadas junto com a pergunta | ✅ |
| C4b | **Conteúdo integral — dev.to:** corpo completo do artigo via API oficial (`/api/articles/{id}`) | ✅ |
| C4c | **Conteúdo integral — links (HN, RSS):** comando `msrkit fetch`, opcional, que baixa o texto do artigo apontado respeitando robots.txt; texto guardado só localmente, nunca exportado | ✅ |

## Fase 2 — Ajustes e melhorias do minerador

Fase 1 (acima, concluída) deixou a **coleta** confiável. A Fase 2 cobre o que o Protocolo
E2 v2 ainda exige do MSR-Kit para responder às RQs: consultas por fonte, rotulagem
RAG/agente, evidência N2/N3, triagem, extração, análise e validação do próprio instrumento.

**Prioridades**, casadas com o cronograma do protocolo (§18.1) e com o risco "minerador
virar o produto" (§19):

- **P0 — antes do piloto (semana 5):** o piloto de 30 itens precisa delas.
- **P1 — antes da coleta (até a semana 7):** entram na v0.2; depois disso o escopo do
  minerador congela e nenhum requisito novo é aceito até o catálogo fechar.
- **P2 — depois da coleta:** análise e empacotamento; podem ser scripts em `analysis/`,
  fora do núcleo, sem reabrir o minerador.

### Bloco A — Coleta alinhada ao protocolo

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| A0 | **Governador de taxa ligado ao `run`** (bug encontrado na implementação): o `run` criava os adaptadores sem o token bucket nem as cotas diárias; cota do Stack Exchange travada em 300 mesmo com chave; cota esgotada não parava a fonte | RNF2 | P0 | ✅ |
| A1 | **Consultas por fonte** no `protocol.yaml` (`queries:`), além dos termos globais: busca de código (`"from ragas import"`, `filename:promptfooconfig.yaml`), workflows (`path:.github/workflows <âncora>`) e issues das ferramentas-âncora (`repo:<owner>/<repo>`) | §7.3 | P0 | ✅ |
| A2 | **Grupos de conceito e rótulo de sistema**: `concepts: {rag, agente, teste}` no protocolo; cada item recebe `sistema: rag \| agente \| rag+agente` pelos termos que casaram; consultas combinam sistema E teste | §3.1, RQ5 | P0 | ✅ |
| A3 | **Gazetteer versionado** (`gazetteer.yaml`): id canônico, aliases, família, padrões de import/dependência/config/CLI e regras de desambiguação; validado pelo `msrkit validate` | §7.2, §12.2 | P0 | ✅ |
| A4 | **`plan` realista**: opção `--estimate` que consulta as estimativas, mostra partições, truncamentos previstos e custo em requisições por fonte (hoje é heurística fixa) | §6.4, §7.4 | P1 | ✅ |
| A5 | **Retomada por partição**: checkpoint granular para que `--resume` continue da partição interrompida, não da fonte inteira | RNF3 | P1 | ✅ |

### Bloco B — Evidência artefatual (N2/N3), núcleo de RQ1 e RQ3

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| B1 | **Enriquecimento de repositórios GitHub**: árvore de arquivos (diretórios de teste, configs de avaliação), workflows de CI, nº de contribuidores e meses distintos com commits; preenche `has_ci` e `contributors`, hoje nunca preenchidos | §3.4 (N3) | P0 | ✅ |
| B2 | **Extrator de ferramentas e métodos** (`msrkit extract`): gazetteer + regex + sinais estruturais (import, dependência em pyproject/requirements/package.json, arquivo de config, invocação em CI), com nível N1/N2/N3 e trecho de evidência por detecção | RF6, §12.1 | P0 | ✅ |
| B3 | **Desambiguação**: nomes que colidem com palavras comuns só contam com co-ocorrência do léxico ou sinal estrutural; aliases convergem para o id canônico | §12.2 | P1 | ✅ |

### Bloco T — Triagem e extração com humano no laço

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| T1 | **Quase-duplicatas** (MinHash/LSH) como 3ª passada da deduplicação, com limiar configurável e calibrado no piloto | RF5, §11 | P1 | ✅ |
| T2 | **Planilha de triagem** exportada (critérios I1–I5/E1–E6, decisão, justificativa, codificador) e **importada de volta** para o corpus | §8, §11 | P0 | ✅ |
| T3 | **Ordenação da triagem** por relevância (termos casados, sinais N2/N3), em lotes fixos | §11 | P1 | ✅ |
| T4 | **Formulário de extração** (Anexo A) e colunas de **qualidade da literatura cinza** (§9), exportados e importados do mesmo modo | Anexo A, §9 | P1 | ✅ |
| T5 | **Concordância entre codificadores** (`msrkit agreement`): κ de Cohen por dimensão sobre a amostra de dupla codificação | §15 | P1 | ✅ |
| T6 | **Fluxo PRISMA** gerado das contagens de manifesto, descartes, deduplicação e decisões de triagem | RF8 | P1 | ✅ |

### Bloco V — Validação do instrumento (Design Science)

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| V1 | **Recall sobre gold set**: formato do gold set (~50 artefatos) e comando que mede quantos o minerador recuperou | §10.3 | P0 | ✅ |
| V2 | **Amostra de precisão**: exporta 200 detecções estratificadas por fonte e nível para revisão manual e calcula a precisão | §12.3 | P1 | ✅ |
| V3 | **Testes de contrato com cassettes**: gravar respostas reais por adaptador (vcrpy já é dependência; `tests/cassettes/` está vazio) e checar o esquema das APIs. *Feito: infraestrutura, 5 casos de busca e o caso `enrich` gravado; os casos de busca precisam ser gravados na máquina do pesquisador (`MSRKIT_RECORD=1`), pois este ambiente só alcança o próprio repositório* | §10.2 | P1 | ✅ |
| V4 | **Pacote de reprodutibilidade** (`msrkit package`): protocolo, manifestos, hashes, versão e corpus de metadados prontos para o Zenodo | §10.3, D2–D3 | P2 | ✅ |

### Bloco N — Análises (A1–A6 do protocolo)

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| N1 | Frequência por ferramenta e método, por nível de evidência, e co-ocorrência ferramenta × método | A1, A2 | P2 | ✅ |
| N2 | Matriz de cobertura modo de falha × ferramenta/método, para RAG e agentes | A3 | P2 | ✅ |
| N3 | Distribuição na escada de oráculos e por agregação (teste de H2) | A4 | P2 | ✅ |
| N4 | LDA + clusterização para o passe de descoberta aberta, com a fração do catálogo vinda dele | A5, §7.6 | P2 | ✅ |
| N5 | Comparação RAG × agentes e curva de saturação por lote | A6, §11 | P2 | ✅ |

### Bloco M — Manutenção e qualidade de código

| Etapa | Descrição | Prioridade | Status |
|---|---|---|---|
| M1 | **Dividir `cli.py`** (1.647 linhas, 10 comandos) em módulos por comando, sem mudar comportamento | P1 | ✅ |
| M2 | **mypy no CI** (strict; zerado e no CI) e `ruff format --check` | P1 | ✅ |
| M3 | **Documentação** (`docs/manual.md`, README) acompanhando cada etapa | contínua | ✅ |

### Marcos

1. **Piloto (P0):** A1–A3, B1–B2, T2 e V1 prontos. No piloto: conferir a semântica OU de
   `tagged` no Stack Exchange (ADR-017), decidir a inclusão do Hacker News e calibrar o
   limiar de quase-duplicata.
2. **v0.2 congelada (P1):** restante de A, B, T e V1–V3, mais M1–M2. Reverificar
   `docs/sources.md` antes da coleta.
3. **Pós-coleta (P2):** bloco N e V4.

### Pendências que dependem da equipe de pesquisa

O código da Fase 2 está completo. Ficam fora dele, por exigirem rede aberta ou decisão
de pesquisa:

- **Cassettes de busca (V3):** gravados com sucesso via APIs reais para os adaptadores GitHub, Stack Exchange, Hacker News, Dev.to e Hugging Face, com testes de contrato 100% passando offline (`test_cassettes.py`).
- **Gold set (V1):** curado em `protocols/gold_set.yaml` (53 artefatos cobrindo ferramentas de avaliação RAG, benchmarks de agentes, guardrails, métricas de recuperação e discussões da comunidade).
- **Piloto:** executado com triagem cega independente (Revisor A e B), atingindo concordância substancial ($\kappa = 0.70$), 100% das divergências resolvidas pelo supervisor via `msrkit resolve` (0 incertezas residuais), e fluxo PRISMA atualizado. Limiar de quase-duplicata calibrado em 0.85 e Hacker News validado para o corpus discursivo.
- **Zenodo (V4):** informar os criadores (`--creator`) e confirmar a licença do pacote.

## Fase 3 — Primeira coleta de teste

Datasets de teste para o piloto (semana 5), antes da coleta oficial.

| Etapa | Descrição | Status |
|---|---|---|
| R1 | **`.env` com parâmetros da busca e grupos de fontes:** janela e léxico (§7.1, §7.5) sobrepondo o protocolo, fontes separadas em prontas, com configuração manual, pagas e inviáveis; valores efetivos no manifesto (ADR-041) | ✅ |
| R2 | **Workflow de coleta no GitHub Actions:** o ambiente de desenvolvimento não alcança as APIs das fontes; o runner do Actions tem rede aberta e publica o dataset como artefato | ✅ |
| R3 | **Primeiros datasets de teste** das fontes públicas (até 10 itens por consulta). 1ª coleta (run 36498904192): 224 itens para triagem; Stack Exchange 0 com filtro de tags e HF cortado no 1º termo (ADR-042). 2ª coleta, corrigida (run 36501012949): 999 identificados, 453 para triagem — HN 204, Stack Exchange 117, Hugging Face 122, dev.to 10, RSS 0; detecções só N1 (sem GitHub). Próxima: incluir GitHub quando o secret `MSRKIT_GITHUB_TOKEN` existir | ✅ |
| R4 | **Medium por RSS com acúmulo diário:** a API oficial do Medium só publica (sem leitura/busca, sem integrações novas); 15 feeds (9 tags do Medium, 3 publicações, 3 blogs) coletados todo dia no Actions, com dados acumulados em cache e CSV consolidado (ADR-043). Ativa após o merge no `main` | ✅ |
| R5 | **Coleta com credenciais e análises preliminares.** Execução 1 (run 36515820085): 745 itens para triagem, GitHub cortado pelo teto por fonte (ADR-044). Execução 2 (run 36521583989), sem teto por fonte: **2.255 identificados, 1.641 para triagem** (GitHub 1.185 de 181 consultas, HN 220, HF 123, Stack Exchange 146, dev.to 10, RSS 6); 118 duplicatas (15 quase-duplicatas); 889 detecções; saturação do catálogo no lote 65 de 66. N3 ainda 1: a extração ignorava as buscas em CI e o enrich não priorizava esses repositórios — corrigido (ADR-045). Execução 3 (run 36528947297): **3.668 identificados, 2.916 para triagem** (GitHub 2.459 de 324 consultas, HN 202, HF 122, Stack Exchange 117, dev.to 10, RSS 6); 224 duplicatas; 300 repositórios enriquecidos sem erro; **1.890 detecções, N3 de 1 para ≥97** (webarena, guardrails-ai, agentdojo 8 cada; tau-bench, garak 7); saturação no lote 116 de 117 (50 códigos); amostra de precisão de 200 detecções gerada. Enrich levou ~1h40 por usar o ritmo da API de busca — corrigido (governor `github-core`, ADR-045) | ✅ |
| R6 | **Relatório da mineração a cada coleta:** `msrkit report` escreve `reports/<run>/relatorio_mineracao.md` (e `.json`) com resumo, espaço temporal, fontes, termos buscados, quantidades e avisos — inclusive consultas planejadas que não rodaram, que o PRISMA não mostra. O workflow Coleta gera o relatório antes do `package` (entra no pacote) e o publica no resumo do job (ADR-047) | ✅ |
| R7 | **Ajustes após a 1ª coleta diária de RSS no Actions (run 2026-10-31T13-43-17Z-64b5):** o relatório dava o motivo errado para as fontes fora da execução (mostrava a disponibilidade, "OK"); agora usa os grupos de fontes. Novos: janela do protocolo quando `MSRKIT_WINDOW_*` a estende (os 43 itens daquela execução são de set–out/2026, fora da janela 2023-01..2026-08), itens novos e total acumulado frente às execuções anteriores, consultas planejadas não executadas também no PRISMA. O hash do protocolo passa a normalizar CRLF/LF: Windows e CI davam hashes diferentes para o mesmo arquivo (ADR-047) | ✅ |
| R8 | **Coleta completa local** (`scripts/coleta_local.py`): as etapas do workflow Coleta sem o teto de 240 min do Actions, que uma coleta oficial (100 por consulta, enriquecimento de todos os repositórios) excede. Mesmos grupos de fontes do workflow; janela e termos só do protocolo; GitHub exige `GITHUB_TOKEN` antes de começar; enriquecimento por GraphQL; log em `data/logs/`; `--retomar <run_id>` continua uma execução interrompida; no Windows, impede a suspensão. Também roda no container (`scripts/` montado no compose) (ADR-048) | ✅ |
| R9 | **1ª coleta completa local (run 2026-11-01T23-59-22Z-7837, 5 h 44 min):** 19.143 identificados, 9.161 únicos, 4.157 repositórios enriquecidos; Stack Exchange com chave (671). O GitHub passou a recusar (422) busca de issues sem `is:issue`/`is:pull-request`: 72 de 107 requisições de issues falharam e o adapter as registrava como concluídas com 0 itens (1.440 issues contra 3.795 issues+PRs na d28a). Corrigido: buscas de issues levam `is:issue` (só issues, como diz o protocolo) e um 422 marca a consulta como falha, que o relatório mostra e o `--retomar` refaz (adapter GitHub 0.2.0, ADR-049). A coleta precisa ser refeita | ✅ |

## Fase 4 — Artigo 2: resultados sobre a prática de teste

Segue o guia de protocolo do Artigo 2 (QP1–QP5). O Artigo 1 descreve e valida o MSR-Kit; este
publica os achados.

| Etapa | Descrição | Status |
|---|---|---|
| A2-1 | **Script das análises QP1–QP3** (`analysis/artigo2.py`): pirâmide N3 externo/N2/N1 sem `framework-sut` e sem *self*, Kendall τ com bootstrap, coocorrência, métodos e oráculos, engajamento normalizado por fonte × tipo (medianas com IC, Kruskal–Wallis/Dunn/Holm, Spearman, sensibilidades, binomial negativa, P3, dor), macros e tabelas LaTeX; testes com pacote sintético. Parâmetros provisórios no ADR-046 | ✅ |
| A2-2 | **Rodar no corpus oficial** (execução congelada do Artigo 1 ou d28a com desvios declarados) e preencher o artigo | ⏳ depende do pacote |
| A2-3 | **Triagem** em duas trilhas (ordenada até saturar + amostra aleatória estratificada) com κ em ≥ 20% | ⏳ equipe |
| A2-4 | **Codificação** pelo Anexo A com dupla codificação, κ por dimensão e síntese temática; habilita H2, QP4 e QP5 | ⏳ equipe |
| A2-5 | **Artigo** reestruturado em torno dos resultados (método, uma subseção por QP, discussão, ameaças) | 🔄 |

## Fase 5 — Ajustes antes da coleta oficial

A execução 2026-11-02T19-14-30Z-d855 (7 h 23 min) foi a primeira completa sem consultas com
falha: 21.694 identificados, 11.316 únicos, 5.260 repositórios enriquecidos, 9.750 detecções.
A análise dela mostrou o que falta para a coleta oficial, a que o Artigo 1 congela e o Artigo 2
analisa. As etapas estão em ordem de complexidade, cada uma num commit `O<n>: ...`. As que
mudam o protocolo são desvios registrados em ADR.

| Etapa | Descrição | Status |
|---|---|---|
| O1 | **Léxico só em inglês.** Sai o estrato em português: 16 termos que trouxeram 63 itens na d855, 4 deles sem nenhum casamento. Saem também `pt.stackoverflow`, os termos em português dos conceitos e "português" do critério I4. O mecanismo `terms_by_language` continua no código | ✅ |
| O2 | **RSS fora da coleta oficial.** Na janela do protocolo o RSS dá sempre 0, porque os feeds só têm posts recentes. `scripts/coleta_local.py` deixa de consultá-lo; o monitoramento diário no Actions continua | ✅ |
| O3 | **Execução 7837 arquivada** fora de `data/` (issues quebradas, ADR-049), para que o acúmulo entre execuções e as estatísticas não a misturem | ✅ |
| O4 | **Consultas literais de repositório corrigidas.** O GitHub ignora o grupo `(eval OR test OR testing)`: `RAG (eval OR test OR testing) in:name,description,readme stars:>5` dá 27.662 resultados, o mesmo que `RAG in:name,description,readme stars:>5`, e com `agentic` dá 175.235. As duas viram 8 consultas sem OU, só em nome e descrição. RAG: eval 100, evaluation 266, test 113, testing 99. agentic: eval 394, evaluation 1.068, test 1.489, testing 1.456. `"tool_call" "assert"` (291.840 arquivos de código) vira `"expected_tool_calls"` (1.076) e `"assert_tool_call"` (463), em `path:tests` | ✅ |
| O5 | **dev.to pela janela inteira.** Com 10 páginas por tag, a coleta para por volta de set/2026. As tags `rag`, `llm`, `agents`, `testing` e `llmops` têm todas as páginas acessíveis até 2021, ≈97 mil artigos em ~100 requisições, então `max_pages` sobe para 40. A fonte vira censo (`census: true`): o teto por consulta não se aplica, e ~1% dos artigos casa, ~1 mil requisições no total (ADR-052). A tag `ai` sai: tem 60 mil artigos só em set–out/2026, suas 10 páginas não chegavam à janela, e cobrir a janela exigiria mais de mil páginas | ✅ |
| O6 | **Reprodutibilidade da busca de código registrada.** Em 24 h, o top-100 de 3 consultas de código ficou 98–100% igual. A diferença com a d28a (61% em comum) vem de 13 dias de reindexação. O adapter deixa de mandar `sort=indexed`, que a API ignora (dá o mesmo conjunto da ordem por relevância). O relatório passa a mostrar, por fonte, a sobreposição com as execuções anteriores. A ameaça entra no guia do Artigo 1, e o corpus oficial é o do pacote | ✅ |
| O7 | **Enriquecimento reaproveitado entre execuções.** `msrkit enrich --reuse-days N` copia os sinais de um repositório já enriquecido em outra execução, desde que tenha a mesma janela e os mesmos arquivos de configuração do gazetteer e tenha sido coletado há até N dias. A origem fica marcada (`reused_from`), e o script local usa 14 dias. Na d855, 3.977 dos 5.260 repositórios (76%) já estavam na 7837: são ~5 h a menos por coleta | ✅ |
| O8 | **Amostra estratificada por ano.** Com `sampling: {strata: year}` no protocolo, cada consulta das fontes que filtram por data (GitHub repositórios e issues, Hacker News, Stack Exchange) roda uma vez por ano da janela (2023, 2024, 2025 e 2026 até agosto), com o mesmo teto por consulta. Hoje 65% das issues e 93 dos 98 artigos do dev.to são de 2026, porque cada consulta fica com os 100 mais recentes. O total disponível de cada estrato (`total_count` do GitHub, `nbHits` do HN) vai para o manifesto, para que a análise pondere as frequências. Busca de código e Hugging Face não filtram por data e ficam sem estratos; o dev.to é censo (O5) | ✅ |
| O9 | **Coleta oficial** com O1–O8 e O11–O15, rodada pelo pesquisador com `scripts/coleta_local.py`, e congelada: id da execução registrado aqui e no Artigo 1, pacote no Zenodo. A 015e (2026-11-06) foi a candidata; a revisão dela encontrou os defeitos de O11–O15 e ela passa a ser calibração | ⏳ pesquisador |
| O10 | **Precisão e triagem da execução oficial:** preencher `correct` (1/0) nas 200 detecções de `data/validation/<run>/precision_sample.csv` e rodar `msrkit precision score`; depois, a triagem (A2-3). Só depois de O9: as regras de O11 mudam as detecções, então a amostra da 015e não serve | ⏳ equipe |
| O11 | **Evidência estrutural lida do artefato** (ADR-056). Na 015e, 533 detecções N2/N3 estavam penduradas em issues, só 28% dos 1.986 "CI" vindos da busca tinham a ferramenta num passo executável, e 8% dos pares de N3 externo eram cópias do repositório da própria ferramenta. Agora: sinais do repositório só em item de repositório ou de código; CI lido do YAML do workflow (`run:`/`uses:`, sem o que só imprime ou exclui), com a forma registrada (`uses`, `cli`, `module`, `install`, `name`) e o nome solto valendo no máximo N2; dependências lidas das tabelas que as declaram; repositório com o nome da ferramenta não conta como uso; o enriquecimento lê primeiro os workflows apontados pela busca e só reaproveita sinal que os contém. Na 015e reextraída: 13.923 → 11.234 detecções, CI 2.186 → 473, N3 1.099 → 135 | ✅ |
| O12 | **Coleta: filtros de issues, léxico, gazetteer e linguagens** (ADR-057). `filter_bots: true` (812 das 7.339 issues da 015e eram de automação) e `exclude_labels` (`dependencies`, `requirement`; `question` fica, de propósito); "oracle" vira "test oracle"; gazetteer 0.2 com sete nomes ambíguos, o pacote do BFCL e o repositório novo do PyRIT; página de tamanho fixo; o teto de cada consulta repartido entre Python e TypeScript na proporção dos totais; total da fonte registrado também para a busca de código. Adapter do GitHub 0.3.0 | ✅ |
| O13 | **Pesos de amostragem e PRISMA** (ADR-058). `msrkit.weights` tira do manifesto a fração de cada consulta (examinados ÷ disponíveis) e o peso de cada item; o relatório ganha a seção Amostragem e o PRISMA passa a contar o que as consultas limitadas pelo teto não examinaram (na 015e: 122 consultas, cerca de 120 mil registros, limite superior) | ✅ |
| O14 | **Artigo 2: P2 pela média de E, duas leituras e boletins automáticos** (ADR-059). A mediana de E fica no piso para todas as ferramentas (a maioria das issues não tem reação) e o ρ da P2 saía indefinido; a P2 passa a usar a média de E. P1 e P2 são calculadas na amostra e ponderadas, e só se sustentam se as duas leituras concordarem. Boletins periódicos publicados por contas comuns saem de todas as análises, pelo título. Sensibilidade: N3 contando o nome solto, proporção no topo do estrato | ✅ |
| O15 | **Pacote: um hash de protocolo e o commit da coleta** (ADR-060). O pacote da 015e mostrava um hash de protocolo diferente do relatório (fim de linha) e o commit de quando foi montado, não o da coleta. O pacote agora usa o hash do manifesto, guarda o protocolo com LF, registra o commit e se havia alterações pendentes, na coleta e no empacotamento; o script local avisa antes de começar | ✅ |
| O16 | **Análise lê o pacote uma linha por registro.** Na ba7b (2026-11-16), três itens do Stack Exchange traziam U+2028 no texto, e `analysis/artigo2.py`, que separava os registros com `splitlines()`, os lia como vinte linhas quebradas: a análise ficava com 16.639 dos 16.642 itens. Os registros passam a ser separados só por `\n`; as conclusões de P1 e P2 não mudam | ✅ |

## Registro

Cada etapa é entregue num commit próprio, cujo título começa com o identificador da etapa
(ex.: `C1: ...`, `A1: ...`) e que atualiza estas tabelas.
