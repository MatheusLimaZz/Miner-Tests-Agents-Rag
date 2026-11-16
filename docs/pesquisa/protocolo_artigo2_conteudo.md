# Guia de protocolo — Artigo 2: como a comunidade testa sistemas com RAG e agentes

> **Como usar este guia.** Ele é a especificação de pesquisa do Artigo 2, escrita para ser seguida por uma IA ou por uma pessoa. Leia tudo antes de agir.
>
> - `[DECIDIR]` marca decisões dos autores. Apresente opções; não decida sozinho.
> - `[VERIFICAR]` marca algo a conferir no código, nos dados ou na fonte antes de usar.
> - Cada etapa termina com **Pronto quando**.

---

## 0. Contexto mínimo

- **Base metodológica: Protocolo E2 v2.** Ele define RQ1–RQ5, a hipótese H2, as análises A1–A6, os critérios de elegibilidade (§8), a qualidade da LC (§9), a triagem em lotes com saturação (§11), a dupla codificação com κ (§15) e o formulário de extração (Anexo A).
  - O documento completo do protocolo **não está no repositório**; o código e os docs só citam as seções. `[VERIFICAR]` Obtenha o PDF/DOCX do E2 v2 com os autores e confira cada seção citada aqui.
- **Instrumento.** O MSR-Kit é descrito e validado no Artigo 1 (`docs/pesquisa/protocolo_artigo1_ferramenta.md`). Aqui ele é só citado, sem repetir a avaliação.
- **Corpus.** A mineração `d28a` (repositório original) é a mais rica; veja `docs/pesquisa/comparativo_mineracoes_a2a8_d28a.md`. O ideal é usar a **execução oficial congelada** da etapa 4 do Artigo 1. Se ela não existir, use a `d28a` e declare os desvios listados no comparativo.
- **Divisão entre os artigos:**
  - Este artigo publica os achados substantivos.
  - Recall, precisão e estabilidade do instrumento são publicados no Artigo 1 e apenas citados aqui.

---

## 1. Tipo de estudo

- **Desenho:** estudo de métodos mistos que combina revisão de literatura cinza (multivocal) e mineração de repositórios. A parte quantitativa vem das detecções N1/N2/N3 e do engajamento; a qualitativa, da codificação manual pelo Anexo A.
- **Guias:**
  - Garousi et al. (2019), para LC e revisão multivocal;
  - PRISMA 2020 (Page et al., 2021), para o fluxo;
  - ACM SIGSOFT Empirical Standards, padrões *Repository Mining* e *Systematic Reviews* `[VERIFICAR]`;
  - Kalliamvakou et al. (2014), sobre os riscos de minerar o GitHub.
- **Precedente direto:** Raulamo-Jurvanen, Mäntylä & Garousi (2017) usaram LC para descobrir quais ferramentas de automação de teste são mais populares. É o modelo mais próximo da QP1 e da QP3.

---

## 2. Questões de pesquisa

As QPs abaixo reorganizam as RQ1–RQ5 do E2 v2 e acrescentam engajamento, que não estava no protocolo.

| QP | Pergunta | E2 v2 | Análise | Comando | Base mínima |
|---|---|---|---|---|---|
| **QP1** | Quais ferramentas de teste e avaliação a comunidade mais usa em sistemas RAG e em agentes, e com que força de evidência? | RQ1, RQ3 | A1, A2 | `msrkit analyze frequency` | detecções (N2/N3) + rótulo de sistema codificado |
| **QP2** | Quais técnicas de teste e quais tipos de oráculo a comunidade emprega? | RQ2, RQ4 (métodos) | A4, H2 | `msrkit analyze oracles` | codificação (`tipo_oraculo`, `agregacao`) |
| **QP3** | Quais ferramentas e técnicas geram mais engajamento, e engajamento acompanha adoção? | **nova** | A7 (nova) | script novo em `analysis/` | itens + detecções + engajamento |
| **QP4** | Quais modos de falha a comunidade relata, e quais técnicas e ferramentas os cobrem? | RQ4 | A3 | `msrkit analyze coverage` | codificação (`modos_falha`) |
| **QP5** | O que transfere de RAG para agentes, e o que é genuinamente novo em agentes? | RQ5 | A6 | `msrkit analyze compare` | codificação |
| (todas) | Quais ferramentas e métodos existem fora do gazetteer? | §7.6 | A5 | `msrkit analyze topics` | itens |

**Escopo** `[DECIDIR]`:

- **Núcleo do artigo: QP1–QP3.** São as perguntas que você propôs ("quais ferramentas mais usadas", "o que gera mais engajamento") e podem ser respondidas com as detecções mais uma amostra codificada.
- **QP4 e QP5** dependem da codificação manual completa. Podem entrar se houver espaço, ou ficar para uma versão estendida em periódico.

**Sub-perguntas da QP1:**

- **QP1.1:** ranking por N3 externo, N2 e N1.
- **QP1.2:** recorte RAG × agentes.
- **QP1.3:** lacuna entre discurso e prática, ou seja, ferramentas muito citadas (N1) e pouco adotadas em CI (N3), e vice-versa.

---

## 3. Hipóteses e proposições

**Atenção:** os autores já viram resultados preliminares da `d28a`. Testes sobre o mesmo corpus, portanto, são **exploratórios**. Para um teste confirmatório há duas saídas:

- registrar as hipóteses **antes** de rodar sobre a amostra codificada; ou
- testar na execução oficial congelada, e não na `d28a`.

| ID | Enunciado | Teste | Origem |
|---|---|---|---|
| H2 | A grande maioria dos oráculos é atômica (limiar 0,75) | binomial exato unilateral (já em `analysis.py`) | E2 v2 |
| P1 | O ranking por menção (N1) difere do ranking por adoção em CI (N3) | Kendall τ entre rankings; IC por bootstrap | QP1.3; na `d28a`, ragas lidera N2 e promptfoo lidera N3 |
| P2 | Engajamento e adoção são fracamente correlacionados entre ferramentas | Spearman ρ (média de E × N3 externo; a mediana fica no piso, ADR-059) | QP3 |
| P3 | Conteúdo sobre agentes tem engajamento maior que conteúdo sobre RAG em 2025–2026, controlando fonte e idade | modelo da seção 6.3 | QP3, exploratória |

Os limiares de P1 e P2 ficam como `[DECIDIR]`; por exemplo, τ < 0,5 e ρ < 0,4.

---

## 4. Dados

### 4.1 Arquivos (pacote `d28a` ou execução oficial)

| Arquivo | Conteúdo | Tamanho na `d28a` |
|---|---|---|
| `corpus/items.jsonl` | itens deduplicados (título, URL, datas, engajamento, conceitos, termos casados, proveniência) | 11.433 |
| `evidence/detections.jsonl` | detecções (item, repositório, entrada do gazetteer, nível, sinal, trecho de evidência) | 9.871 |
| `evidence/github_repos.jsonl` | sinais de repositório (CI, testes, contribuidores, meses com commit, manifestos) | 5.500 |
| `run/manifest.json` | consultas, partições, truncamento, hashes | — |
| `protocol/gazetteer.yaml` | 41 ferramentas + 10 métodos | — |

### 4.2 Unidade de análise

- **Unidade:** um repositório do GitHub, com todos os seus itens fundidos, ou um item isolado nas demais fontes (ADR em `docs/decisions.md`).
- **Contagem:** cada unidade conta uma vez por ferramenta, no maior nível atingido (N3 > N2 > N1).
- **Self-repo:** o repositório da própria ferramenta (campo `repos` do gazetteer) é separado como "self" e excluído do ranking de adoção.

### 4.3 Separar ferramentas de teste e frameworks do sistema sob teste

O gazetteer tem 8 entradas da família `framework-sut`: langchain, langgraph, llamaindex, haystack, autogen, crewai, dspy e semantic-kernel. **Elas não são ferramentas de teste.** Na `d28a`, langchain (815 unidades) e langgraph (550) lideram as contagens brutas.

- **Fora do ranking da QP1.** Use-as só como contexto: sobre quais *stacks* os testes são feitos.
- **Famílias de teste** (33 ferramentas): `avaliacao-llm-rag`, `tracing-observabilidade`, `guardrails-seguranca`, `recuperacao` e `agentes`.
- **Métodos** (10): llm-as-judge, golden-dataset, eval-regression, red-teaming, assertion-based-testing, trajectory-evaluation, metamorphic-testing, synthetic-test-generation, property-based-testing e human-evaluation.

### 4.4 Engajamento: o que cada fonte oferece

Mapeamento conferido nos adaptadores (`src/msrkit/adapters/*.py`):

| Fonte / tipo | Métrica principal | Secundária | Cuidado |
|---|---|---|---|
| GitHub issue | `reactions` (total) | `comments` | issues de bots (renovate, dependabot) devem ser removidas |
| GitHub repo | `stars` | `forks` | estrela ≠ uso (Borges & Valente, 2018) |
| GitHub code | — | — | sem engajamento; herdar estrelas do repositório exige enriquecer com `stars` (P1-7 do Artigo 1) |
| Hacker News | `votes` (= points) | `comments` | |
| Stack Exchange | `votes` (= score) | `views` | aqui o campo `comments` guarda o **número de respostas** |
| dev.to | `reactions` | `comments` | |
| Hugging Face | `stars` (= likes) | `views` (= downloads) | downloads incluem automação; não é engajamento social |
| RSS | — | — | sem engajamento |

O engajamento é uma foto tirada no momento de `provenance.fetched_at`: ele se acumula com o tempo, por isso a idade do item precisa ser controlada (seção 6.3).

---

## 5. Procedimento

### 5.1 Congelar o corpus

Registre `run_id`, `protocol_sha256`, commit, DOI do pacote e a lista de desvios.

**Pronto quando** houver um ADR em `docs/decisions.md` dizendo qual corpus é o oficial.

### 5.2 Triagem (§8, §11)

São duas trilhas complementares:

- **(a) Triagem ordenada** por relevância, em lotes de 25 (`msrkit screen export`), até saturar.
  - A saturação é medida sobre os **códigos da codificação manual** (ferramenta, método, modo de falha ou código aberto novo), não sobre entradas do gazetteer, que é fechado e satura por construção.
  - Regra de parada `[DECIDIR]`: por exemplo, 4 lotes consecutivos sem código novo.
- **(b) Amostra aleatória estratificada**, para estimativas sem o viés da ordenação.
  - n ≈ 372 itens (95% de confiança, ±5%, população de 11.433), estratificada por fonte × rótulo de sistema.
  - Alocação proporcional, com mínimo de 20 por estrato `[DECIDIR]`.
  - É a trilha que permite afirmar "X% do que a comunidade publica sobre teste de RAG usa LLM como juiz".

**Regras comuns:**

- Critérios de inclusão I1–I5 e de exclusão E1–E6, conforme `protocols/v0_rag_agents_testing.yaml`.
- Hits de código do GitHub são triados **por repositório** (I5, E6), não por hit.
- Pelo menos 20% dos itens triados passam por dois avaliadores independentes. Calcule κ sobre incluir/excluir; se κ < 0,61, recalibre os critérios e refaça o lote.

**Pronto quando** a saturação for atingida na trilha (a), a amostra (b) estiver completa, o κ estiver registrado e `msrkit prisma` mostrar os incluídos.

### 5.3 Codificação (Anexo A e §9)

- **Campos:** `sistema`, `nivel_evidencia`, `ferramentas`, `metodos`, `modos_falha` (FP1–FP13 e AF1–AF12), `tipo_oraculo`, `agregacao`, `evidencia_textual`, `criterio_aprovacao_declarado`, `modo_falha_relatado` (texto aberto), `declara_o_que_nao_cobre` e `confianca`, mais a qualidade da LC (`q_*`).
- **Dupla codificação** em ≥20% dos incluídos (`msrkit coding sample`), com κ por dimensão (`msrkit agreement`) e um terceiro codificador para as divergências.
  - Mantenha o `--prefill` desligado, para que a codificação seja independente.
  - Para campos multivalorados, como `modos_falha`, considere também o α de Krippendorff com distância MASI `[VERIFICAR o que o msrkit calcula hoje]`.
- **Codificação indutiva:** o campo `modo_falha_relatado` passa por síntese temática (Cruzes & Dybå, 2011) para revelar modos de falha fora do catálogo.
- **Qualidade (§9):** use os campos `q_*` numa análise de sensibilidade. Os resultados se mantêm só com fontes de qualidade alta?

**Pronto quando** os incluídos estiverem codificados, com κ por dimensão ≥ 0,61 ou justificativa registrada.

### 5.4 Descoberta aberta (A5, §7.6)

1. Limpe o ruído antes de modelar: bots, atualizações de dependência, tokens de código (`whl`, `py3-none-any`).
2. Rode LDA e k-means (`msrkit analyze topics`) e revise manualmente os termos candidatos.
3. Ferramentas novas entram no gazetteer, e o `extract` é refeito. Repita até não surgir ferramenta nova.
4. Reporte a fração do catálogo final que veio da descoberta.

**Pronto quando** uma rodada de descoberta não acrescentar nenhuma ferramenta.

---

## 6. Plano de análise por QP

### 6.1 QP1: ferramentas mais usadas

- **Métrica:** unidades distintas por ferramenta e nível.
  - Ranking principal: **N3 externo** (sem *self-repo*).
  - Secundários: N2 e N1.
  - Apresente sempre os três níveis lado a lado, como uma pirâmide de evidência.
- **Recortes:** RAG × agentes. Na base de codificação, use o rótulo codificado; na base de detecções, o `applies_to` do gazetteer ou os conceitos. Diga sempre qual dos dois foi usado.
- **Lacuna discurso × prática (QP1.3):**
  - dispersão N1 × N3 por ferramenta, em escala log;
  - Kendall τ entre os rankings;
  - destaque de quem fica longe da diagonal.
- **Coocorrência (A2):** Jaccard e *lift* ferramenta × ferramenta e ferramenta × método, para achar as *stacks* usadas juntas.
- **Evolução temporal (opcional):** primeira aparição de cada ferramenta em N2/N3 por trimestre (`commit_months`). Atenção: o truncamento das buscas favorece itens recentes.
- **Robustez:** estabilidade do ranking sob variação de `per_query_limit` (EQ4 do Artigo 1).
- **Figuras:** barras por família e nível; *bump chart* dos rankings N1/N2/N3; dispersão N1 × N3.

### 6.2 QP2: técnicas e oráculos

- Distribuição dos métodos (detecções N1 e codificação) por sistema.
- Escada de oráculos (especificado, derivado, referência, pseudo-automático e humano; Barr et al., 2015), por sistema, com o teste de H2 sobre `agregacao`.
- Combinações de oráculos no mesmo item; prevalência de LLM como juiz e se ela vem acompanhada de validação humana.
- **Na amostra aleatória (5.2-b):** proporções com IC de Wilson.

### 6.3 QP3: engajamento (análise nova, A7)

1. **Unidades:** itens discursivos (issues, HN, Stack Exchange, dev.to, Hugging Face). Hits de código ficam de fora, a menos que o repositório tenha estrelas enriquecidas.
2. **Normalização dentro da fonte:**
   - calcule o percentil da métrica principal dentro de cada par (fonte, tipo), com empates pela média;
   - o resultado é E ∈ [0, 1], comparável entre fontes;
   - **nunca compare contagens brutas entre fontes.**
3. **Exposição:**
   - idade = `fetched_at` − `created_at`, em meses;
   - análise principal só com itens de idade ≥ 3 meses `[DECIDIR]`;
   - sensibilidade com engajamento por mês ou com a idade como covariável.
4. **Por ferramenta e por método:**
   - média de E com IC de 95% por bootstrap (10.000 reamostragens). E é um percentil de posto médio, então a média é a chance de um item da ferramenta superar outro item do mesmo estrato (0,5 = item típico);
   - a mediana e a proporção de itens no piso do estrato vão junto, só para descrever: a maioria das issues não tem reação, e a mediana é o mesmo valor para todas as ferramentas (ADR-059);
   - mínimo de n ≥ 10 itens para reportar `[DECIDIR]`.
5. **Comparação:**
   - Kruskal–Wallis entre ferramentas ou famílias, com pós-teste de Dunn e correção de Holm;
   - tamanho de efeito: δ de Cliff nos pares e ε² no global;
   - **robustez:** regressão binomial negativa das contagens brutas, com efeitos fixos de fonte × tipo e *offset* log(idade).
6. **Viés de múltiplas menções:**
   - um item que cita k ferramentas conta para cada uma;
   - sensibilidade 1: exclua *listicles* (≥4 ferramentas citadas);
   - sensibilidade 2: exclua conteúdo de fornecedor (`q_objetividade` ≠ `sem-vinculo`).
7. **Engajamento × adoção (P2):**
   - Spearman ρ entre a média de E e o N3 externo, com IC por bootstrap;
   - sensibilidade: ρ com a proporção de itens no topo do estrato (10%), e ρ com a mediana, que era a estatística registrada antes;
   - gráfico de quadrantes: muito falado e pouco adotado, pouco falado e muito adotado.
8. **Engajamento "de dor":**
   - as consultas `issues-ancora` (`repo:{repo} is:issue label:bug`) trazem os bugs das próprias ferramentas;
   - reações e comentários nessas issues medem atrito, e o conteúdo alimenta a QP4;
   - **ressalva:** essas issues refletem a comunidade de cada ferramenta, não o uso em geral.
9. **Viés declarado:** a busca do GitHub ordena por relevância e trunca, o que favorece itens populares. Compare os resultados só com consultas não truncadas.
10. **Duas leituras (ADR-058, ADR-059):** cada consulta que para no limite de itens é uma amostra, com fração conhecida (examinados ÷ total da fonte). P1 e P2 são calculadas na amostra e com pesos de amostragem, e só se sustentam se as duas leituras concordarem. Os pesos corrigem a repartição entre estratos (45% das issues de 2023 contra 2% das de 2026, na 015e), não a ordem em que a fonte devolve os resultados dentro de cada um.
11. **Issues automáticas:** as de robôs saem na coleta (`filter_bots`); boletins periódicos publicados por contas comuns saem na análise, pelo título. As duas contagens entram no artigo.

### 6.4 QP4: modos de falha e cobertura (A3)

- Matriz modo de falha (FP1–FP13, AF1–AF12) × ferramenta/método, em unidades.
- Destaque as **células vazias**: falhas discutidas sem técnica ou ferramenta que as cubra.
- Compare com taxonomias acadêmicas `[VERIFICAR o mapeamento]`:
  - Barnett et al. (2024), para RAG: as FP1–FP7 parecem corresponder aos sete pontos de falha;
  - Cemri et al. (2025, MAST), para multiagentes.
- Modos de falha novos vêm da codificação indutiva (5.3).

### 6.5 QP5: transferência RAG → agentes (A6)

- Jaccard dos conjuntos de ferramentas e métodos entre unidades RAG e de agentes, mais a taxa de transferência, **na base de codificação**.
  - Na base de detecções, o rótulo vem do casamento de conceitos, e muitos itens recebem os dois rótulos. Isso infla a sobreposição: a `d28a` dá Jaccard de 0,97 por esse motivo.
- O que é exclusivo de agentes: avaliação de trajetória, asserção sobre chamadas de ferramenta e os métodos ligados a AF1–AF12.

---

## 7. Ameaças à validade

- **Construto:**
  - "Mais usada" foi medida por invocação em CI em repositórios públicos, o que exclui uso privado ou corporativo.
  - Menção não é uso.
  - Engajamento não é qualidade nem utilidade.
  - Estrelas são infladas (Borges & Valente, 2018), e downloads do Hugging Face incluem automação.
- **Interna:**
  - Viés do gazetteer, que é um vocabulário fechado (mitigado pela A5).
  - Viés de ordenação e truncamento das buscas.
  - Os autores viram resultados preliminares, por isso as proposições são tratadas como exploratórias.
  - A pré-avaliação por LLM não substitui a codificação.
- **Externa:**
  - São 6 fontes, sem Reddit, X, Discord e LinkedIn, que concentram muita discussão de praticantes. Declare a exclusão e o motivo.
  - Só inglês: o léxico em português trouxe 90 itens na `d28a` e 63 na `d855` e saiu do protocolo (ADR-050). Os achados valem para a comunidade que publica em inglês.
  - Janela de 01/2023 a 08/2026, com o GitHub somando ≈81% dos itens.
- **Conclusão:**
  - Comparações múltiplas (corrigidas por Holm).
  - Distribuições de cauda pesada (tratadas com testes não paramétricos).
  - n pequeno para várias ferramentas (exigido n mínimo).
- **Confiabilidade:** κ reportado, pacote de replicação e scripts de análise versionados.

---

## 8. Estrutura sugerida do artigo

1. **Introdução:** a prática de teste de RAG e agentes é pouco documentada, e os surveys acadêmicos focam em benchmarks; QPs e contribuições.
2. **Fundamentação:** RAG, agentes, teste de sistemas com ML/LLM (Zhang et al., 2020), problema do oráculo (Barr et al., 2015), LLM como juiz (Zheng et al., 2023), literatura cinza.
3. **Método:** protocolo, fontes, instrumento (citando o Artigo 1), triagem, codificação, análises e o fluxo PRISMA.
4. **Resultados:** uma subseção por QP.
5. **Discussão:** implicações para praticantes (que ferramenta usar para quê), para pesquisadores (lacunas da QP4) e para criadores de ferramentas (lacuna discurso × prática).
6. **Ameaças à validade.**
7. **Trabalhos relacionados:** surveys de avaliação de LLM, de RAG e de agentes; revisões de LC sobre ferramentas de teste.
8. **Conclusão.**
9. **Disponibilidade dos dados.**

**Veículos candidatos** `[DECIDIR]`: CAIN (engenharia de IA), ESEM, MSR (trilha técnica), SBES; em periódico, EMSE, JSS, IST ou TOSEM.

---

## 9. Regras para a IA que for executar este guia

1. **Todo número informa** o arquivo de origem, o comando ou script, a base (`detections` ou `coding`) e o nível de evidência.
2. **N1 nunca é chamado de "uso".** Use "menção"; "uso" é N2, e "adoção sustentada" é N3.
3. **Frameworks `framework-sut` não entram no ranking de ferramentas de teste.**
4. **Engajamento só é comparado na escala normalizada** dentro da fonte.
5. **Decisões de análise são registradas como ADR antes de rodar** sobre os dados codificados: limiares, n mínimo, regra de parada.
6. **Não misture execuções sem declarar.** Corpus oficial é um só.
7. **Não use a pré-avaliação por LLM como codificação nem como precisão.**
8. **Não invente referências.** Marque `[VERIFICAR]` até conferir título, autores, ano, veículo e DOI.
9. **Não publique dados pessoais:** os autores ficam anonimizados como no pacote.
10. **Respeite a divisão com o Artigo 1:** métricas do instrumento só são citadas aqui.

---

## 10. Referências-semente (conferir todas antes de citar)

**Método:**

- Garousi, V.; Felderer, M.; Mäntylä, M. V. (2019). Guidelines for including grey literature and conducting multivocal literature reviews in software engineering. *Information and Software Technology*, 106.
- Page, M. J. et al. (2021). The PRISMA 2020 statement. *BMJ*, 372:n71.
- Kitchenham, B.; Charters, S. (2007). Guidelines for performing systematic literature reviews in software engineering. EBSE Technical Report.
- Kalliamvakou, E. et al. (2014). The promises and perils of mining GitHub. *MSR 2014*.
- Cruzes, D. S.; Dybå, T. (2011). Recommended steps for thematic synthesis in software engineering. *ESEM 2011*.
- Landis, J. R.; Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*, 33.
- Cliff, N. (1993). Dominance statistics: ordinal analyses to answer ordinal questions. *Psychological Bulletin*, 114(3).

**Popularidade e engajamento:**

- Raulamo-Jurvanen, P.; Mäntylä, M.; Garousi, V. (2017). Choosing the right test automation tool: a grey literature review of practitioner sources. *EASE 2017*.
- Borges, H.; Valente, M. T. (2018). What's in a GitHub star? Understanding repository starring practices in a social coding platform. *Journal of Systems and Software*, 146.

**Teste de ML/LLM, RAG e agentes:**

- Barr, E. T. et al. (2015). The oracle problem in software testing: a survey. *IEEE TSE*, 41(5).
- Zhang, J. M.; Harman, M.; Ma, L.; Liu, Y. (2020). Machine learning testing: survey, landscapes and horizons. *IEEE TSE*.
- Zheng, L. et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *NeurIPS 2023*.
- Es, S. et al. (2024). RAGAS: automated evaluation of retrieval augmented generation. *EACL 2024* (demo).
- Barnett, S. et al. (2024). Seven failure points when engineering a retrieval augmented generation system. *CAIN 2024*.
- Cemri, M. et al. (2025). Why do multi-agent LLM systems fail? arXiv:2503.13657.
- Chang, Y. et al. (2024). A survey on evaluation of large language models. *ACM TIST*.
- Yu, H. et al. (2024). Evaluation of retrieval-augmented generation: a survey. arXiv:2405.07437.
- Yehudai, A. et al. (2025). Survey on evaluation of LLM-based agents. arXiv:2503.16416.
