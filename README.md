# Pipeline de Pesquisa Acadêmica

Ferramenta automatizada para buscar, verificar e auditar referências bibliográficas com princípio **zero trust**: toda referência é verificada antes de uso.

**Autor:** Raphael Augusto Teixeira de Aguiar
**Versão:** 0.1.0 (MVP)


## Problema que resolve

Ferramentas de IA generativa são úteis para mapeamento conceitual e descoberta de fontes, mas **nunca devem ser a fonte final de referências**. Auditoria empírica de 288 referências geradas por 5 ferramentas (Perplexity, scite.ai, Gemini, ChatGPT, Claude) revelou:

- ~50% off-topic
- Apenas 9.6% classificadas como "Gold"
- 1 referência completamente fabricada (autores, DOI e periódico inventados)
- Dados quantitativos inverificáveis

Este pipeline automatiza a busca em bases indexadas e a verificação individual de cada referência.


## Arquitetura

### Pipeline de 10 estágios (busca → refs verificadas)

```
scope.yaml → [1]Escopo → [2]Busca → [3]Dedup → [4]Verificação → [5]Tier
                                                                     ↓
refs.bib ← [9]Síntese ← [8]Acesso ← [7]Integridade ← [6]Relevância
                ↓
         [10]Validação → audit-report.md
```

### Camadas de verificação (pré e pós-escrita)

```
Refs verificadas
      ↓
[extract-facts] → .facts-registry.json    ← Camada PREVENTIVA (antes de escrever)
      ↓
[escrita do texto]
      ↓
[verify-claims]  → .claims-data.json      ← Camada CORRETIVA (depois de escrever)
      ↓
[facts-crosscheck] → cruzamento           ← Ponte entre as camadas
```

| Etapa | Módulo | O que faz |
|---|---|---|
| 1 | `s01_scope` | Carrega `scope.yaml` → `SearchConfig` |
| 2 | `s02_search` | Busca nas APIs do `scope.yaml` — implementadas: PubMed, OpenAlex, Semantic Scholar, Europe PMC, BVS/LILACS (experimental) → `refs-raw.json` |
| 3 | `s03_normalize` | Dedup: DOI exato + fuzzy(título+ano+autor, 85%) → `refs-dedup.json` |
| 4 | `s04_verify` | CrossRef: DOI resolve? Título confere? → `refs-verified.json` |
| 5 | `s05_classify` | TIER_MAP (~200 domínios) + CrossRef pub_type → Tier |
| 6 | `s06_screen` | Keywords do scope.yaml → relevância (DIRECT/TANGENTIAL/OFF_TOPIC) |
| 7 | `s07_integrity` | Crossmark via CrossRef → retratações e correções |
| 8 | `s08_access` | Unpaywall + HTTP HEAD/GET → status de acesso |
| 9 | `s09_synthesize` | Calcula grade composta → `refs-final.json` + `refs.bib` + `audit-report.md` |
| 10 | `s10_validate` | Re-verifica DOIs e URLs finais + consistência |


## Modalidades

O pipeline opera em 5 modos, selecionados no `scope.yaml`:

| Modalidade | Uso | Produto | Norma/qualidade |
|---|---|---|---|
| `pesquisa-base` | Subsidiar escrita (capítulo, artigo, aula) | Base de refs verificadas (intermediário) | — |
| `revisao-integrativa` | Publicar revisão integrativa | A revisão É o artigo | PRISMA adaptado; MMAT/CASP |
| `revisao-sistematica` | Publicar revisão sistemática | A revisão É o artigo | PRISMA 2020; RoB 2/GRADE |
| `revisao-escopo` | Mapear extensão da evidência | A revisão É o artigo | PRISMA-ScR (JBI) |
| `meta-revisao` | Umbrella review (revisão de revisões) | A revisão É o artigo | PRIOR; AMSTAR-2 |

Na `meta-revisao`, o PubMed recebe filtro `systematic[sb]/review[pt]` automático
e a triagem descarta o que não é revisão (`relevance_method=nao_e_revisao`).

## LILACS/SciELO — importação RIS (portal BVS)

A API bibliográfica da BVS é de **uso interno da BIREME** — não existe chave
pública (docs.api.bvsalud.org, verificado 2026-07-15). O caminho legítimo,
indicado pela própria BIREME, é buscar pelo portal e exportar:

```bash
# 1. Executar a estratégia de busca em https://pesquisa.bvsalud.org (navegador)
# 2. Exportar os resultados em RIS
python -m tools import-ris "Projeto" ~/Downloads/export.ris
python -m tools run "Projeto" --from-stage 3   # dedup + verificação zero-trust
# 3. Documentar a string de busca no pipeline/search-log.md (exigência PRISMA)
```

O `import-ris` aceita qualquer RIS padrão (BVS, SciELO, Zotero, Rayyan,
Scopus, Web of Science) — os registros passam pela MESMA verificação das
buscas automáticas.

## Triagem semântica LLM (dupla triagem)

Os critérios de inclusão/exclusão **em prosa** do scope.yaml são aplicados por
um revisor LLM (Claude Code), combinado com a triagem por keywords:

```bash
python -m tools screen-export "Projeto"   # gera screening-batch.jsonl + instruções
# Claude Code julga cada ref e escreve pipeline/.screening-verdicts.jsonl
python -m tools screen-import "Projeto"   # valida, calcula kappa de Cohen, relata divergências
python -m tools run "Projeto" --from-stage 5   # etapa 6 combina os dois sinais
```

Divergências keyword×LLM **nunca são descartadas silenciosamente** — vão para
`screening-report.md` para decisão do revisor humano (revisor 2).

## Descritores MeSH/DeCS

```bash
python -m tools descriptors "Projeto"
# → pipeline/descriptors-suggested.md (API MeSH da NLM; matches exatos
#   viram bloco YAML pronto). DeCS aceita o rótulo MeSH em inglês (mh:).
```

## Fluxo PRISMA canônico

Gerado automaticamente na etapa 9 para modalidades de revisão
(`pipeline/prisma-flow.md`, com diagrama Mermaid + contagens por base), ou
sob demanda: `python -m tools prisma "Projeto"`.

## Busca semântica vetorial (Fase 3)

Camada opcional via Ollama local (modelo `bge-m3`, multilíngue). Duas funções:

1. **Re-ranqueamento**: similaridade de cosseno consulta×referência vira bônus
   no ranking (`semantic_weight`, default 8.0).
2. **Resgate semântico**: referência descartada só por falta de match de
   keyword (`no_match`) mas semanticamente próxima da consulta (≥ 0.60,
   calibrado empiricamente) é promovida a TANGENTIAL com método
   `semantic_rescue` — captura sinônimos que as keywords não previram.
   Exclusões deliberadas (termo de exclusão, pub_type, não-revisão) NUNCA
   são resgatadas.

```bash
# Opt-in no scope.yaml: semantic_rerank: true  (roda dentro da etapa 6)
# Ou standalone sobre o último checkpoint:
python -m tools semantic "Projeto"
```

Degradação graciosa: sem Ollama no ar, o pipeline segue sem a camada
semântica, com aviso explícito. Config: `OLLAMA` local em `localhost:11434`
com o modelo `bge-m3` puxado (`ollama pull bge-m3`).


## Pré-requisitos

- **Python 3.12+**
- API keys gratuitas:
  - **NCBI/PubMed:** registrar email em https://www.ncbi.nlm.nih.gov/account/
  - **OpenAlex:** basta informar email (polite pool)
  - **CrossRef:** basta informar email (polite pool)
  - **Unpaywall:** basta informar email

## Instalação

```bash
cd ~/bin/escrita-tooling
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements.txt

# Configurar API keys
cp tools/config.example.env tools/.env
# Editar tools/.env com seus dados
```

**Layout de diretórios (modelo híbrido, 2026-05-28):** o código vive em
`~/bin/escrita-tooling/`; os projetos (textos, `scope.yaml`, outputs
`pipeline/`) vivem no vault, em `~/PKM/Escrita/<Projeto>/`.


## Como usar

### Pipeline completo

```bash
# 1. Criar scope.yaml para o projeto
python -m tools scope "Livro Editora Atheneu" --init

# 2. Editar scope.yaml com keywords, critérios, etc.

# 3. Executar pipeline completo
python -m tools run "Livro Editora Atheneu"

# Com modalidade específica
python -m tools run "Artigo Revisão" --modality revisao-integrativa
```

### Subcomandos individuais

```bash
# Apenas busca
python -m tools search "Livro Editora Atheneu"

# Verificar referências existentes
python -m tools verify "Livro Editora Atheneu"

# Estado do pipeline
python -m tools status "Livro Editora Atheneu"

# Exportar .bib
python -m tools export "Livro Editora Atheneu" --format bib

# Gerar relatório de auditoria
python -m tools audit "Livro Editora Atheneu"
```

### Facts Registry (camada preventiva)

```bash
# Extrair candidatos quantitativos das refs
python -m tools extract-facts "Livro Editora Atheneu" --refs 11-17

# Validar e importar fatos (após revisão pelo Claude)
python -m tools facts-import "Livro Editora Atheneu"

# Brief para escrita (conciso, ~2-5 KB)
python -m tools facts-status "Livro Editora Atheneu" --section 16.3

# Relatório completo
python -m tools facts-report "Livro Editora Atheneu"

# Cruzar com verify-claims (camadas desacopladas)
python -m tools facts-crosscheck "Livro Editora Atheneu"
```

### Executar etapas parciais

```bash
# A partir da etapa 5 (usa refs-verified.json existente)
python -m tools run "Projeto" --from-stage 5

# Apenas etapas 2-4
python -m tools run "Projeto" --from-stage 2 --to-stage 4
```


## scope.yaml — Guia de preenchimento

```yaml
project_name: "Livro Editora Atheneu"
modality: pesquisa-base

research_question: "Quais são as aplicações de Big Data e IA na gestão hospitalar?"

keyword_blocks:
  - concept: "tecnologia"
    terms: ["big data", "artificial intelligence", "machine learning"]
  - concept: "domínio"
    terms: ["hospital management", "healthcare administration"]
  - concept: "aplicação"
    terms: ["bed management", "fraud detection", "supply chain"]

mesh_terms:
  - "Big Data"
  - "Artificial Intelligence"
  - "Hospital Administration"

year_range: [2020, 2026]
languages: ["en", "pt"]
# Implementadas: pubmed, openalex, semantic_scholar, europe_pmc, bvs_lilacs (experimental).
# Os defaults de modalidade ativam só pubmed+openalex — amplie aqui conforme o rigor exigido.
apis: ["pubmed", "openalex"]
max_results_per_api: 100

relevance_keywords:
  direct: ["hospital management", "bed management", "gestão hospitalar"]
  tangential: ["healthcare", "saúde", "hospital"]
  off_topic: ["tuberculosis", "geriatrics", "waste management"]
```


## Outputs

| Arquivo | Descrição |
|---|---|
| `pipeline/refs-raw.json` | Referências brutas da busca (etapa 2) |
| `pipeline/refs-dedup.json` | Após deduplicação (etapa 3) |
| `pipeline/refs-verified.json` | Após verificação CrossRef (etapa 4) |
| `pipeline/refs-final.json` | Referências Gold + Silver finais |
| `pipeline/refs.bib` | BibTeX para Pandoc + CSL |
| `pipeline/audit-report.md` | Relatório de auditoria com estatísticas |
| `pipeline/search-log.md` | Log reprodutível das buscas |


## Grade composta

| Grade | Critério |
|---|---|
| **GOLD** | T1/T2 + relevância DIRECT + acessível |
| **SILVER** | T1/T2 + TANGENTIAL, ou T3 + DIRECT, ou T1/T2 + DIRECT + restrito |
| **BRONZE** | T3 + TANGENTIAL, ou paywall |
| **DISCARD** | Retratado, off-topic, T4, ou URL quebrada |


## Configuração avançada

### Estender TIER_MAP

Editar `tools/tier_map.py` para adicionar domínios:

```python
TIER_MAP["novo-periodico.com"] = 1  # Tier 1
```

### Ajustar thresholds

- Fuzzy matching de títulos: `FUZZY_THRESHOLD` em `stages/s03_normalize.py` (default: 90; pares 80-89 são logados como suspeitos para revisão manual)
- Título vs CrossRef: threshold de 80 em `apis/crossref.py`


## Troubleshooting

| Problema | Solução |
|---|---|
| `NCBI_EMAIL é obrigatório` | Configure email em `tools/.env` |
| API timeout | Verifique conexão; aumente `DEFAULT_TIMEOUT` em `config.py` |
| DOI não resolve | Normal para ~5-10% dos DOIs; marcado como `doi_resolves: false` |
| Poucas referências | Revise keywords no `scope.yaml`; amplie período ou adicione termos |
| Rate limiting (429) | O pipeline já inclui delays; aumente delays em `apis/*.py` |


## Testes

```bash
source .venv/bin/activate
python -m pytest tools/tests/ -v
```


## Referências

- Relatório de auditoria que motivou o pipeline: `~/PKM/Escrita/Livro Editora Atheneu/Deep Researches (Antigas)/relatorio-audit-deep-research.md`
- Skills que usam este pipeline: `~/bin/escrita-tooling/skills/pesquisa-academica/SKILL.md`, `~/bin/escrita-tooling/skills/revisao-literatura/SKILL.md`
- Decisões arquiteturais: `~/bin/escrita-tooling/tools/DECISOES.md`
