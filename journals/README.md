# tools/journals — recomendação de periódicos-alvo

Subsistema para responder **"onde submeter"** um manuscrito/semente/tema.

**Critério VIGENTE (ciclo CAPES 2025-2028, Área 22)**: o Qualis Periódicos
foi descontinuado (a lista 2021-2024 é a última e retrospectiva). O que
vale para artigos publicados de 2025 em diante é a **consolidação** do
periódico — **Porta A** (categoria de saúde em Q1/Q2 no Scopus/WoS) ou
**Porta B** (coleção SciELO Saúde Pública, h5 > percentil 60). A shortlist é
ranqueada por consolidação × aderência × APC × OA × tempo × chance estimada.
**Todo dado tem fonte + data; ausência é "não disponível", nunca chute.**

Dois caveats de honestidade sempre explícitos: (1) o quartil SJR (Scimago)
é *proxy* do CiteScore/Scopus — confirmar no scopus.com/sources; (2) verdict
negativo pela Porta A não é definitivo (pode valer via CiteScore/JIF real ou
WoS). Ver `consolidacao.py`.

Tarefa distinta do pipeline de busca de literatura (`tools/`, nível-artigo)
— aqui a unidade é o periódico (ISSN). Skill que orquestra a parte com LLM:
`skills/periodicos-alvo/SKILL.md`.

## Uso

```bash
cd ~/bin/escrita-tooling && source .venv/bin/activate
python -m tools journals status                    # estado da base
python -m tools journals recommend --tema "..." -o finalistas.json
python -m tools journals report --finalistas finalistas.json -o rel.md
```

Bootstrap (base vazia):
```bash
python -m tools journals init-db
python -m tools journals download-qualis           # XLSX Sucupira (LEGADO)
python -m tools journals ingest-qualis --arquivo data/journals/raw/qualis_2021_2024.xlsx
python -m tools journals ingest-scimago-categorias --csv data/journals/raw/scimago_categorias_2025.csv  # Porta A
python -m tools journals ingest-scielo-sp          # Porta B (coleção SciELO SP)
python -m tools journals ingest-scimago --csv <CSV SJR>   # SJR geral (opcional)
python -m tools journals seed-from-vault           # Nota curada do vault
python -m tools journals enrich --all              # calcula a consolidação
```

As fontes estáticas da consolidação — `scimago_categorias_2025.csv`
(categorias Scopus por quartil) e `scielo_saude_publica.json` (coleção
`spa` do articlemeta) — vivem em `data/journals/raw/`. A de categorias é
gerada do parquet do pacote `ikashnitsky/sjrdata` (coluna `categories`);
atualizar anualmente (Scimago sai em junho).

## Módulos

| Arquivo | Papel |
|---|---|
| `db.py` | Schema SQLite + proveniência (`<campo>_fonte`, `_verificado_em`); migração de colunas |
| `consolidacao.py` | **Motor Área 22**: Porta A (categorias de saúde × quartil) + Porta B (SciELO-SP); conjunto curado de categorias de saúde |
| `qualis.py` | Baixa/ingere o Qualis 2021-2024 (LEGADO — retrospectivo) |
| `scimago.py` | Ingere SJR geral + `ingerir_categorias` (Porta A) |
| `scielo.py` | Ingere a coleção SciELO Saúde Pública (Porta B) |
| `seed_vault.py` | Parseia a Nota de Revistas e resolve ISSN por título |
| `apis/` | Clientes nível-periódico: OpenAlex /sources, DOAJ, Crossref /journals, NLM Catalog |
| `enrich.py` | Enriquecimento cache-first por TTL + cálculo da consolidação (sem LLM) |
| `candidates.py` | Descoberta (OpenAlex /works) ∪ curados; comando `recommend` |
| `rank.py` | Ranqueia por consolidação (eixo dominante) + fit + APC (`pesos.yaml`); nada é excluído por não-consolidação |
| `report.py` | Relatório Markdown liderando pela consolidação + caveats; Qualis como legado |
| `snapshot.py` | Captura páginas de editora (base auditável da extração) |
| `import_extraction.py` | Grava extrações LLM validando trecho por substring (anti-alucinação) |
| `llm.py` | Backend Ollama opcional (re-varredura em massa local) |
| `export_nota.py` | Regenera a Nota do vault agrupada por consolidação + APC verificada (preview) |

## Cache e manutenção

O banco é o cache: cada família de campos tem TTL (Qualis ∞; métricas
365d; DOAJ/APC 180d; snapshots 180d). `enrich` nunca reconsulta dado
fresco. Scimago: re-ingerir em junho. Dados vivem em
`data/journals/` (gitignored, exceto o CSV export).

## Fontes de dados

**Consolidação (critério vigente)**: Scimago categorias por quartil
(Porta A, proxy do CiteScore) + coleção SciELO Saúde Pública / articlemeta
(Porta B). **Qualis 2021-2024** (Sucupira legado, XLSX): retrospectivo, só
para artigos até 2024. OpenAlex /sources: ISSN, métricas, tópicos, OA,
apc_usd. DOAJ v4: APC (valor+moeda), licença, peer review, waiver. Crossref
/journals: fallback de identidade. NLM Catalog: indexação MEDLINE. Scimago:
SJR/quartil geral. Tempo/taxa/tipos: só por extração de página de editora
(best-effort, sempre com URL+trecho).
