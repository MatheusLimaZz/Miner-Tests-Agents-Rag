# Análises do Artigo 2

`artigo2.py` calcula, sobre um pacote de reprodução do MSR-Kit (`msrkit package`), as análises
das QP1–QP3 do guia do Artigo 2. Ele gera os números e as tabelas que o artigo inclui. Todas as
análises usam a base de **detecções automáticas**. As que dependem da codificação manual, como
H2, QP4 e QP5, ficam para quando a codificação existir.

## Como rodar

```bash
pip install -e ".[analysis]" statsmodels      # statsmodels é opcional (binomial negativa e P3)
python analysis/artigo2.py data/packages/msrkit_<run>.zip --out resultados/
```

Antes de existir um pacote, `--pending` grava os mesmos arquivos com marcadores visíveis, para o
artigo compilar:

```bash
python analysis/artigo2.py --pending --out resultados/
```

No Overleaf, suba a pasta `resultados/` inteira ao lado do `.tex`. Para atualizar o artigo,
basta rodar o script de novo e substituir a pasta.

## O que sai em `resultados/`

| Arquivo | Conteúdo |
|---|---|
| `macros.tex` | todos os números citados no texto, cada um com a origem em comentário (arquivo, base, parâmetros) |
| `tab_qp1.tex`, `tab_qp1_pares.tex` | pirâmide de evidência por ferramenta, em unidades da amostra; pares que mais coocorrem |
| `tab_qp1_pond.tex` | a mesma pirâmide em estimativas ponderadas (N3, N2, N1) e a posição da ferramenta na amostra |
| `tab_qp2.tex`, `tab_qp2_oraculos.tex` | métodos por sistema; escada de oráculos |
| `tab_qp3.tex`, `tab_qp3_metodos.tex` | **média** de E com IC 95% por ferramenta e por método |
| `fig_qp1_dispersao.tex` | dispersão menções (N1) × adoção externa (N3), escala log |
| `qp*.csv`, `summary.json` | tabelas completas e o resumo, com os parâmetros usados |

`qp3_engajamento_*.csv` traz, por ferramenta ou método: `n`, `mean` com o intervalo, `median`,
`share_floor` (proporção de itens empatados no menor valor do estrato, como zero reações),
`share_top` (proporção no topo do estrato) e `mean_w` (média ponderada).

## Decisões de análise (ADR-046, revisto pelo ADR-059)

- **P2 usa a média de E, não a mediana.** A maioria das issues não tem reação e empata no piso
  do estrato: a mediana é o mesmo número para todas as ferramentas e o ρ fica indefinido. E é um
  percentil de posto médio, então a média de E de uma ferramenta é a chance de um item dela
  superar outro item do mesmo estrato (0,5 = item típico). A mediana continua na tabela, e o ρ
  calculado com ela sai em `\ResRhoMediana`. **O texto e o cabeçalho da `tab_qp3` devem dizer
  "média".**
- **Duas leituras de P1 e P2.** Consultas que param no limite de itens são amostras. Quando o
  manifesto do pacote tem o total de cada consulta, P1 e P2 são calculadas na amostra e com
  pesos de amostragem (`msrkit.weights`, ADR-058). A proposição só é lida como sustentada se as
  duas leituras concordarem (`\ResPUmConclusao`, `\ResPDoisConclusao`); se divergirem, o
  resultado é "inconclusivo (depende da ponderação)". Sem os totais, vale só a leitura da
  amostra e `\ResPesos` diz "sem pesos".
- **Regra do N3.** A análise principal usa o N3 do extrator (ADR-056): um passo do workflow que
  chama, importa ou instala a ferramenta. `\ResTauUmTresNome` repete a P1 contando também o nome
  solto num comando, como sensibilidade.
- **Issues automáticas saem de todas as análises:** manutenção de dependências e boletins
  periódicos (lista diária de artigos, resumo semanal), reconhecidos pelo título.
  `\ResItensAutomaticos` dá o total; `--keep-bots` as mantém.

## Parâmetros provisórios

Os valores padrão são **decisões dos autores ainda abertas** `[DECIDIR]`. Precisam ser
confirmados no ADR-046 antes da rodada confirmatória.

| Opção | Padrão | Decide |
|---|---|---|
| `--min-age-months` | 3 | idade mínima do item para entrar no engajamento |
| `--min-n` | 10 | itens mínimos para reportar o engajamento de uma ferramenta ou método |
| `--listicle-k` | 4 | quantas ferramentas citadas fazem de um item uma *listicle*, excluída na sensibilidade |
| `--tau-threshold` | 0,5 | τ abaixo do qual a P1 (menção ≠ adoção) é lida como sustentada |
| `--rho-threshold` | 0,4 | \|ρ\| abaixo do qual a P2 (engajamento ≠ adoção) é lida como sustentada |
| `--top-share` | 0,1 | fração do estrato que conta como topo, na sensibilidade da P2 |
| `--min-together` | 5 | unidades mínimas em comum para um par aparecer na coocorrência |
| `--bootstrap` | 10.000 | reamostragens dos intervalos de confiança |
| `--with-repos` | não | inclui repositórios do GitHub (estrelas) no engajamento |
| `--keep-bots` | não | mantém issues automáticas (atualização de dependência, alertas, boletins) |

## Convenções (regras do guia)

- **Nível de evidência:** N1 é menção, nunca uso. N2 é uso declarado e N3 é adoção sustentada.
- **Repositório da própria ferramenta:** quando a unidade é o repositório da ferramenta (*self*), ela fica fora do ranking de adoção e aparece numa coluna à parte. Cópias e homônimos já saem na extração (ADR-056).
- **Frameworks:** as entradas `framework-sut` (LangChain, LangGraph…) não entram no ranking de ferramentas de teste.
- **Engajamento:** só é comparado depois de normalizado dentro de cada par fonte × tipo.
- **Pesos:** corrigem a repartição desigual entre estratos, não a ordem em que a fonte devolve os resultados dentro de cada um. Totais ponderados são limites superiores.
