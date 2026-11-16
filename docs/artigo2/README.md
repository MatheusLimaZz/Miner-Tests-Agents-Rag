# Artigo 2: como a comunidade testa sistemas RAG e agentes de LLM

| Arquivo | O que é |
|---|---|
| `main.tex` | artigo completo no template ACM-like do CBSoft, em português |
| `referencias.bib` | referências; as marcadas `[VERIFICAR]` ainda precisam ser conferidas na fonte |
| `previa.pdf` | prévia compilada, com 7 páginas |

**No Overleaf:** substitua o conteúdo do `main.tex` do projeto por este arquivo e troque o
`referencias.bib`. Ele não depende de nenhum outro arquivo do projeto além de `acmart.cls` e
`ACM-Reference-Format.bst`, que já vêm no template.

**Origem dos números:** os resultados vêm da execução oficial `2026-11-16T07-29-05Z-ba7b` (`16/11/2026`, commit `22b444e`), na base de detecções automáticas analisada por `analysis/artigo2.py` sobre `data/packages/msrkit_2026-11-16T07-29-05Z-ba7b.zip` (com `statsmodels` e isolamento de organizações mantenedoras), com saída completa em `resultados/`. A validação de precisão (200 detecções), a triagem manual e a dupla codificação (QP4) aguardam execução.
