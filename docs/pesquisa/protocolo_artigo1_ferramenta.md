# Guia de protocolo — Artigo 1: MSR-Kit, um minerador reprodutível de literatura cinza

> **Como usar este guia.** Ele é a especificação de pesquisa do Artigo 1, escrita para ser seguida por uma IA ou por uma pessoa. Leia tudo antes de agir.
>
> - `[DECIDIR]` marca decisões que cabem aos autores. Não decida sozinho: apresente as opções com prós e contras.
> - `[VERIFICAR]` marca afirmações a conferir no código, nos dados ou na fonte original antes de usar no texto.
> - Cada etapa termina com **Pronto quando**, o critério objetivo de conclusão.

---

## 0. Contexto mínimo

- **O que é o MSR-Kit.** Ferramenta em Python que executa um protocolo de mineração escrito em YAML: plan → run (coleta) → dedupe → enrich → extract → screen → coding → agreement → prisma → analyze → package. Os módulos ficam em `src/msrkit/`, e o manual, em `docs/manual.md`.
- **Repositórios.**
  - O original (`MatheusLimaZz/Miner-Tests-Agents-Rag`) contém todo o código: os commits do fork mais 44 commits.
  - O fork (`Icar0S/Miner-Tests-Agents-Rag`) não tem commits próprios em 30/10/2026.
  - Detalhes em `docs/pesquisa/comparativo_mineracoes_a2a8_d28a.md`.
- **Execuções existentes** (são histórico, não experimento controlado):
  - `412d`, `6dec` e `a2a8`: 29/09, commit `0de5639`.
  - `98f6`: 19/10, commit `88cec7c`.
  - `d28a`: 20/10, commit `0f1d4d3`.
- **Artigo irmão.** O Artigo 2 (`docs/pesquisa/protocolo_artigo2_conteudo.md`) usa o MSR-Kit para estudar como a comunidade testa sistemas RAG e agentes.

### Divisão entre os dois artigos (vale para ambos)

| | Artigo 1 (este) | Artigo 2 |
|---|---|---|
| Objeto | o instrumento | o fenômeno (teste de RAG e agentes) |
| Resultado principal | o MSR-Kit funciona, é reprodutível e serve para outros temas | o que a comunidade usa, como testa e o que engaja |
| RAG/agentes aparecem como | prova de conceito (POC), em poucos números e sem discussão de mérito | resultado principal |
| Cita o outro | "achados substantivos em [Artigo 2]" | "instrumento descrito e validado em [Artigo 1]" |

Para não haver sobreposição indevida, as métricas de validação do instrumento (recall, precisão, estabilidade) são publicadas aqui, e o Artigo 2 apenas as cita.

---

## 1. Enquadramento

- **Tipo:** artigo de ferramenta com avaliação empírica (*engineering research*), estruturado como Design Science: problema → artefato → validação (Wieringa, 2014; Engström et al., 2020).
- **Padrões a seguir** `[VERIFICAR nomes e checklists em acm-sigsoft.github.io/EmpiricalStandards]`: ACM SIGSOFT Empirical Standards, em especial os padrões *Engineering Research*, *Repository Mining* e o de artefatos e ciência aberta.
- **Veículos candidatos** `[DECIDIR]`:
  - Curto (4–6 p.): CBSoft/SBES (Sessão de Ferramentas), MSR (*Data and Tool Showcase*) ou ICSE/FSE (*Demonstrations*).
  - Longo: JSS, IST, EMSE.
  - Software: SoftwareX ou JOSS.
  - O formato define o tamanho da avaliação. Num *tool track*, EQ1–EQ4 bastam; num periódico, inclua também EQ5 e EQ6.
- **Autoria e repositório canônico** `[DECIDIR antes de escrever]`:
  - A ferramenta foi criada no repositório original e estendida no fork.
  - Definam um único repositório canônico, uma *tag* de versão, o `CITATION.cff` e a ordem de autoria entre os dois pesquisadores.

---

## 2. Problema e lacuna

**Problema.** Estudos de literatura cinza (LC) e revisões multivocais em Engenharia de Software (ES) costumam coletar dados de forma manual, por buscadores e *snowballing* (Garousi et al., 2019). Essa coleta tem quatro limites:

1. **Não é reprodutível:** os resultados variam por usuário e ao longo do tempo, sem registro de requisições.
2. **Não declara truncamento:** não se sabe o que deixou de ser recuperado.
3. **Não liga discurso a prática:** um post que cita uma ferramenta não mostra se ela é usada em código ou em CI.
4. **Não gera um fluxo PRISMA auditável** a partir dos registros.

**Lacuna em relação às ferramentas existentes** `[VERIFICAR cada uma]`:

- PyDriller, Perceval/GrimoireLab, GHTorrent/GH Archive, SEART GitHub Search, Boa e World of Code mineram repositórios e rastreadores de projetos escolhidos previamente.
- Nenhuma delas combina, sob um protocolo versionado, cinco capacidades:
  - busca por palavra-chave em várias fontes de LC (Q&A, fóruns, blogs, hubs de modelos);
  - proveniência por resposta de API;
  - contabilidade de truncamento;
  - níveis de evidência que ligam menção a uso;
  - triagem e codificação com humano no laço.

**Tarefa para a IA:** montar uma matriz de funcionalidades × ferramentas (linhas: R1–R11 abaixo) com fonte verificável para cada célula.

### Requisitos de projeto e onde estão no código

| Req. | Requisito | Módulo / comando |
|---|---|---|
| R1 | Protocolo como código (YAML versionado com hash) | `config.py`, `msrkit validate` |
| R2 | Proveniência: hash SHA-256 de cada resposta, consulta e partição | `provenance.py`, `manifest.json` |
| R3 | Truncamento registrado (motivo e estimativa do não recuperado) | `partition.py`, manifesto, `prisma.py` |
| R4 | Governança de taxa e retomada por partição | `governor.py`, `msrkit run --resume` |
| R5 | Deduplicação em três passadas (URL, conteúdo, MinHash/LSH) | `dedupe.py` |
| R6 | Níveis de evidência N1 (menção), N2 (uso declarado) e N3 (CI com ≥2 contribuidores e ≥2 meses) | `enrich.py`, `extract.py`, `gazetteer.py` |
| R7 | Triagem e codificação com dois codificadores e κ por dimensão | `screening.py`, `coding.py`, `agreement.py` |
| R8 | PRISMA 2020 gerado dos registros | `prisma.py` |
| R9 | Validação do instrumento (recall em gold set, precisão estratificada) | `validation.py`, `msrkit recall`, `msrkit precision` |
| R10 | Pacote de replicação (Zenodo) | `package.py` |
| R11 | Ética: autores anonimizados, redistribuição só de metadados, robots.txt | adaptadores, `fulltext.py` |

---

## 3. Contribuições (rascunho)

- **C1.** O MSR-Kit, ferramenta aberta que executa protocolos de mineração de LC multifonte com proveniência, truncamento declarado e PRISMA gerado.
- **C2.** Um modelo operacional de níveis de evidência (N1/N2/N3) que separa falar de uma ferramenta, declarar uso e adotá-la de forma sustentada em CI. Ele é reutilizável em qualquer estudo sobre adoção de ferramentas.
- **C3.** Um procedimento de validação do próprio instrumento: gold set, precisão estratificada com IC de Wilson, testes de contrato com *cassettes* e determinismo das etapas offline.
- **C4.** Uma avaliação em um estudo real (RAG/agentes: ≈11 mil itens, 6 fontes) e, se o veículo comportar, em um segundo estudo de outro tema, para mostrar generalidade.
- **C5.** Um pacote de replicação com DOI.

---

## 4. Questões de avaliação (EQ)

Para cada EQ: pergunta, métrica, procedimento, critério de sucesso e estado atual.

### EQ1 — Recall: quanto do que é relevante e conhecido o MSR-Kit recupera?

- **Métricas:** recall de coleta (artefato recuperado) e recall de detecção (ferramenta certa detectada), no total e por fonte, com IC de Wilson a 95%.
- **Procedimento:**
  1. O gold set precisa ser **independente da ferramenta**. O atual (`validation/gold_set.yaml`, 53 itens) foi montado pelos autores depois de verem os corpora de setembro. Escolha uma saída `[DECIDIR]`:
     - **(a)** Um terceiro pesquisador monta um novo gold set por busca manual no Google, seguindo Garousi et al. (2019), sem acesso ao corpus. Isso também gera uma **linha de base**: busca manual × MSR-Kit em recall e esforço (horas).
     - **(b)** Um gold set extraído das referências de literatura cinza citadas em surveys acadêmicos de avaliação de RAG e agentes.
     - **(c)** Manter o atual e declarar o viés.
  2. Meta de tamanho: ≥100 itens, com ≥10 por fonte. Hoje o Stack Exchange tem 3.
  3. **Classifique cada item não recuperado por motivo:** fora da API, fora da janela, termo não casou, truncamento, fonte não coberta ou bug. Essa tabela de causas é tão importante quanto o número de recall.
- **Critério de sucesso** `[DECIDIR]`: por exemplo, recall de coleta ≥70% no total e nenhuma fonte com recall 0.
- **Estado:** a `d28a` mediu 38/53 (71,7%) no total e 0/3 no Stack Exchange, com o gold set não independente.

### EQ2 — Precisão: com que precisão o extrator atribui ferramenta/método e nível de evidência?

- **Métricas:** precisão ponderada por estrato (fonte × nível), com IC de Wilson; precisão por nível; κ entre juízes.
- **Procedimento:**
  1. Use a amostra estratificada de 200 detecções que o `msrkit precision sample` já gerou para a execução oficial.
  2. Dois juízes humanos avaliam de forma independente, **sem ver** a pré-avaliação por LLM nem a decisão do outro.
  3. Calcule κ; um terceiro juiz resolve as divergências.
  4. Calcule a precisão com `msrkit precision score`.
  5. **Resultado secundário:** a concordância LLM × humano (a pré-avaliação da `d28a`) pode ser reportada como comparação, nunca como a precisão do instrumento.
  6. **Precisões auxiliares** (de 50 a 100 casos cada):
     - rótulo de sistema RAG/agente;
     - pares removidos como quase-duplicata (o limiar 0,85 acerta?);
     - descartes `no_term_match` do dev.to (há falsos negativos?).
- **Critério** `[DECIDIR]`: por exemplo, precisão ponderada ≥90% e κ ≥0,61 (Landis & Koch, 1977).
- **Estado:** na `a2a8`, a amostra foi sorteada mas não julgada. Na `d28a`, os 98% vêm só da pré-avaliação automática; as notas são frases-modelo.

### EQ3 — Reprodutibilidade e estabilidade

- **(a) Determinismo offline.** Reprocesse as etapas `normalize` → `dedupe` → `extract` → `analyze` duas vezes a partir do `raw/` da execução oficial e compare os hashes das saídas. Já existe `tests/test_determinism.py`; `[VERIFICAR]` quais etapas ele cobre. **Pronto quando** as saídas forem idênticas byte a byte, ou as diferenças estiverem explicadas.
- **(b) Estabilidade online.** Rode o mesmo protocolo, com o mesmo código, duas ou três vezes, com intervalos `[DECIDIR]` de 0, 7 e 30 dias.
  - Métricas por fonte: Jaccard de URLs; Kendall τ do ranking das 20 ferramentas mais detectadas; variação de N2/N3.
  - **Motivação observada:** só 45% das URLs da `a2a8` reaparecem na `d28a`. Esse número, porém, mistura versão de código e parâmetros, e a EQ3b isola o efeito do tempo.
- **(c) Replicação pelo pacote.** Uma pessoa de fora da equipe reproduz as tabelas do artigo usando só o pacote Zenodo. Registre o tempo gasto, o que faltou e se os números bateram.

### EQ4 — Custo, escala e sensibilidade a parâmetros

- **Experimento controlado:** mesma versão do código, mesmo protocolo, no mesmo dia, variando um fator por vez:
  - `per_query_limit` ∈ {10, 50, 100};
  - enriquecimento ∈ {300 repositórios, todos}.
- **Métricas:**
  - requisições, tempo, itens, consultas truncadas;
  - respostas 403/429 e tempo em espera do governador de taxa;
  - contagens N2/N3;
  - estabilidade do ranking das 10 ferramentas mais frequentes em relação à maior configuração (Kendall τ);
  - recall no gold set.
- **Pergunta prática respondida:** a partir de que tamanho de coleta os rankings se estabilizam? É a curva de custo × estabilidade.
- As execuções históricas (`412d` → `d28a`) entram só como **narrativa da evolução**, numa tabela descritiva, nunca como evidência causal.

### EQ5 — Generalidade: um estudo de outro tema se configura só com protocolo e gazetteer?

- **Procedimento:**
  1. Escolha um tema de ES com ferramentas conhecidas, que tenham imports, arquivos de configuração e uso em CI identificáveis, para exercitar N2/N3.
  2. Candidatos `[DECIDIR]`: adoção de *mutation testing* (PIT, Stryker, mutmut), *property-based testing* (Hypothesis, fast-check) ou testes *flaky* (pytest-rerunfailures).
  3. Escreva um protocolo YAML e um gazetteer novos **sem alterar o código**.
  4. Rode uma coleta reduzida e monte um gold set pequeno (≈30 itens).
- **Métricas:** linhas de configuração escritas, horas de configuração, alterações de código necessárias (meta: zero), recall e o que falhou.

### EQ6 (opcional, só em periódico) — Utilidade percebida

- De 3 a 5 pesquisadores fazem a triagem de dois lotes, um pela TUI e outro pela planilha. Medidas: tempo por item, κ e questionário SUS.
- Alternativa: entrevista curta com quem usou a ferramenta nos dois repositórios.

---

## 5. Pré-requisitos no código, antes da execução oficial

Implemente cada item num commit próprio, com teste. O congelamento de escopo do protocolo (§19, "minerador virar o produto") vale a partir da *tag*.

**P0 (bloqueiam a avaliação):**

1. **PRISMA e manifesto.** Consultas que não rodaram porque a fonte bateu o teto devem aparecer como truncamento `source_cap` e ser contadas no PRISMA. Mantenha o `estimated_total` mesmo com `per_query_limit ≤ 100` (pelo menos via `msrkit plan --estimate`), para reportar o que não foi recuperado. Na `d28a`, o PRISMA informa 0, o que está errado.
2. ~~**Orçamento por estrato de idioma.**~~ Resolvido com a saída do estrato PT: o léxico ficou só em inglês (ADR-050).
3. **Registro de desvios.** Quando o `protocol_sha256` mudar em relação à execução anterior, grave o diff no manifesto.
4. **Planilha de precisão.** Separe as colunas `pre_llm`, `juiz_a`, `juiz_b` e `final`. O `precision score` deve se recusar a calcular só a partir de `pre_llm`, ou rotular o resultado como pré-avaliação.
5. **Janela no Hugging Face.** 29 cards da `d28a` estão fora da janela. `[VERIFICAR]` se o adaptador usa `createdAt` ou `lastModified`.

**P1:**

6. **Consultas de repositório.** Volte ao texto do protocolo, com a sintaxe correta: `(RAG OR "retrieval augmented")` e `(agent OR agentic)`. A versão atual perdeu termos.
7. **Enriquecimento com estrelas e forks** de todo repositório enriquecido. O Artigo 2 precisa disso para medir engajamento das unidades N2/N3.
8. **Pré-processamento da descoberta aberta (A5).** Na `a2a8`, os termos candidatos são ruído de dependências e bots (`whl`, `botocore`, `py3-none-any`). Adicione *stopwords* de código e exclua issues de bots antes do LDA.
9. **Versão e citação.** *Tag* `v0.x`, `CITATION.cff` e DOI no Zenodo.

---

## 6. Procedimento passo a passo

| # | Etapa | Pronto quando |
|---|---|---|
| 0 | Alinhar repositório canônico, autoria e veículo `[DECIDIR]` | decisão registrada em `docs/decisions.md` (ADR) |
| 1 | Implementar P0 (e P1, se possível) | `pytest`, `mypy` e `ruff` sem erros; PRISMA de teste mostra truncamento por teto |
| 2 | Congelar a versão (*tag*) | *tag* publicada; nenhum requisito novo até o fim da avaliação |
| 3 | Montar o gold set independente (EQ1) | arquivo versionado **antes** da execução oficial, com autor e data |
| 4 | Execução oficial (POC) com a versão congelada | `manifest.json` sem falhas; serve de corpus para o Artigo 2 |
| 5 | Recall (EQ1) e tabela de causas das perdas | `msrkit recall` + planilha de motivos |
| 6 | Precisão humana (EQ2) | dois juízes, κ calculado, divergências resolvidas |
| 7 | Estabilidade (EQ3) e sensibilidade (EQ4) | execuções repetidas e variações de parâmetros registradas |
| 8 | Segundo estudo de caso (EQ5), se o veículo comportar | protocolo, gazetteer e resultados do tema 2 |
| 9 | Pacote de replicação | DOI emitido; o pacote reproduz todas as tabelas do artigo |
| 10 | Escrita | seção 9 deste guia |

---

## 7. Resultados a apresentar

| ID | Conteúdo | Origem |
|---|---|---|
| T1 | Matriz de funcionalidades: MSR-Kit × ferramentas relacionadas × busca manual | seção 2 |
| F1 | Arquitetura e fluxo (protocolo → pacote) | `docs/manual.md` (diagrama Mermaid) |
| T2 | Fontes, APIs, autenticação, limites de taxa, cobertura histórica | `docs/sources.md`, `docs/api-notes.md` |
| T3 / F2 | Funil PRISMA da POC | `msrkit prisma` |
| T4 | Recall por fonte e causas das perdas | EQ1 |
| T5 | Precisão por estrato e nível (humana) e concordância LLM × humano | EQ2 |
| T6 | Estabilidade: Jaccard por fonte e τ dos rankings | EQ3 |
| F3 | Sensibilidade: custo × itens × estabilidade do ranking | EQ4 |
| T7 | Evolução do instrumento: as 5 execuções históricas, de forma descritiva | `comparativo_evolucao_runs.md`, corrigido |
| F4 | Exemplo da pirâmide de evidência N1/N2/N3 para 5 ferramentas, como demonstração | POC |
| T8 | Segundo estudo: esforço de configuração e resultados | EQ5 |

---

## 8. Ameaças à validade

- **Construto:**
  - N3 (invocação em CI) é uma aproximação de adoção: CI não garante uso sério.
  - O gold set pode não representar o universo de artefatos relevantes.
- **Interna:**
  - O gold set foi feito pelos autores (mitigação: EQ1-a).
  - A pré-avaliação por LLM pode influenciar os juízes (mitigação: avaliação às cegas).
  - Versão do código e parâmetros mudaram entre as execuções históricas.
- **Externa:**
  - São 6 fontes, e o GitHub domina (≈81% dos itens).
  - O domínio é um só, a menos que a EQ5 seja feita.
  - As APIs podem mudar ou fechar, como aconteceu com Reddit e X.
- **Confiabilidade:**
  - As buscas das APIs não são determinísticas e os rankings mudam (medido em EQ3b).
  - A busca de código do GitHub deriva com a reindexação. Em 24 h, o top-100 de 3 consultas ficou 98–100% igual, mas só 61% dos hits de código da `d855` estavam na `d28a`, coletada 13 dias antes; Hacker News, Hugging Face e Stack Exchange ficaram em 99–100%. Mitigação: o corpus do estudo é o pacote congelado da execução oficial, não uma nova consulta. O relatório de cada coleta mostra, por fonte, quanto se repete das anteriores (ADR-053).
  - O comportamento sob limite de taxa varia.
- **Gazetteer:** um minerador ancorado em lista acha principalmente o que já conhece. Reporte a fração do catálogo que veio da descoberta aberta (A5).

---

## 9. Ética e licenças `[VERIFICAR termos de uso de cada fonte]`

- Só APIs oficiais. Fontes sem API ou que exigem autorização (LinkedIn, X pago, Discord) ficam excluídas, com o motivo registrado no manifesto.
- Os autores aparecem anonimizados (`anon-<hash>`).
- O pacote redistribui só metadados. A exceção é o Stack Exchange, que tem texto integral com atribuição (`full_text_with_attribution`), conforme a licença CC BY-SA.
- O `msrkit fetch` respeita o robots.txt e guarda o texto integral só localmente.
- Declare no artigo: nenhum dado pessoal identificável é publicado.

---

## 10. Estrutura sugerida do artigo

1. **Introdução:** problema da LC em ES, lacuna, contribuições C1–C5.
2. **Fundamentação e trabalhos relacionados:** LC e revisões multivocais; ferramentas de MSR (T1).
3. **Projeto do MSR-Kit:** requisitos R1–R11, arquitetura (F1), protocolo como código, modelo N1/N2/N3, humano no laço.
4. **Cenário de uso:** passo a passo de um estudo, do YAML ao pacote, com o caso RAG/agentes.
5. **Avaliação:** EQ1–EQ5 (T3–T8, F2–F4).
6. **Discussão:** lições aprendidas (ex.: bugs de sintaxe de busca que zeram uma consulta sem erro aparente; instabilidade das buscas) e limitações.
7. **Ameaças à validade.**
8. **Conclusão e trabalhos futuros.**
9. **Disponibilidade de dados e artefatos:** DOI, licença, versão.

---

## 11. Regras para a IA que for executar este guia

1. **Não invente referências.** Toda citação precisa ser conferida (título, autores, ano, veículo, DOI) antes de entrar no texto; até lá, marque `[VERIFICAR]`.
2. **Todo número do artigo vem de um arquivo ou comando citado** (caminho + comando ou script). Não copie números de relatórios antigos sem recalcular.
3. **Nunca chame a pré-avaliação por LLM de "precisão".** Precisão é o julgamento humano.
4. **Toda contagem informa o nível de evidência (N1/N2/N3) e a base** (`detections` ou `coding`).
5. **Não discuta o mérito dos achados sobre RAG/agentes:** isso é do Artigo 2. Aqui eles são demonstração.
6. **Mudança no protocolo depois do congelamento é desvio:** registre-o num ADR e no artigo.
7. **Não rode coletas que violem termos de uso** nem fontes sem credencial legítima.
8. **Ao encontrar divergência** entre documentação e dados, relate e use os dados.

---

## 12. Referências-semente (conferir todas antes de citar)

- Garousi, V.; Felderer, M.; Mäntylä, M. V. (2019). Guidelines for including grey literature and conducting multivocal literature reviews in software engineering. *Information and Software Technology*, 106.
- Zhang, H. et al. (2020). An evidence-based inquiry into the use of grey literature in software engineering. *ICSE 2020*.
- Kamei, F. et al. (2021). Grey literature in software engineering: a critical review. *Information and Software Technology*, 138.
- Kalliamvakou, E. et al. (2014). The promises and perils of mining GitHub. *MSR 2014* (versão estendida: *EMSE*, 2016).
- González-Barahona, J. M.; Robles, G. (2012). On the reproducibility of empirical software engineering studies based on data retrieved from development repositories. *Empirical Software Engineering*, 17.
- Ralph, P. et al. (2020). ACM SIGSOFT Empirical Standards. arXiv:2010.03525.
- Wieringa, R. (2014). *Design Science Methodology for Information Systems and Software Engineering*. Springer.
- Hevner, A. R. et al. (2004). Design science in information systems research. *MIS Quarterly*, 28(1).
- Engström, E. et al. (2020). How software engineering research aligns with design science: a review. *Empirical Software Engineering*, 25.
- Page, M. J. et al. (2021). The PRISMA 2020 statement. *BMJ*, 372:n71.
- Spadini, D.; Aniche, M.; Bacchelli, A. (2018). PyDriller: Python framework for mining software repositories. *ESEC/FSE 2018* (demo).
- Dueñas, S. et al. (2018). Perceval: software project data at your will. *ICSE 2018 Companion*; e GrimoireLab (*PeerJ Computer Science*, 2021).
- Gousios, G. (2013). The GHTorrent dataset and tool suite. *MSR 2013*.
- Dabic, O.; Aghajani, E.; Bavota, G. (2021). Sampling projects in GitHub for MSR studies. *MSR 2021*.
- Dyer, R. et al. (2013). Boa: a language and infrastructure for analyzing ultra-large-scale software repositories. *ICSE 2013*.
- Ma, Y. et al. (2019). World of Code. *MSR 2019*.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *JASA*, 22.
- Landis, J. R.; Koch, G. G. (1977). The measurement of observer agreement for categorical data. *Biometrics*, 33.
