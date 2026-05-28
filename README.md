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
| 2 | `s02_search` | Busca em PubMed + OpenAlex → `refs-raw.json` |
| 3 | `s03_normalize` | Dedup: DOI exato + fuzzy(título+ano+autor, 85%) → `refs-dedup.json` |
| 4 | `s04_verify` | CrossRef: DOI resolve? Título confere? → `refs-verified.json` |
| 5 | `s05_classify` | TIER_MAP (~200 domínios) + CrossRef pub_type → Tier |
| 6 | `s06_screen` | Keywords do scope.yaml → relevância (DIRECT/TANGENTIAL/OFF_TOPIC) |
| 7 | `s07_integrity` | Crossmark via CrossRef → retratações e correções |
| 8 | `s08_access` | Unpaywall + HTTP HEAD/GET → status de acesso |
| 9 | `s09_synthesize` | Calcula grade composta → `refs-final.json` + `refs.bib` + `audit-report.md` |
| 10 | `s10_validate` | Re-verifica DOIs e URLs finais + consistência |


## Modalidades

O pipeline opera em 4 modos, selecionados no `scope.yaml`:

| Modalidade | Uso | Produto |
|---|---|---|
| `pesquisa-base` | Subsidiar escrita (capítulo, artigo, aula) | Base de refs verificadas (intermediário) |
| `revisao-integrativa` | Publicar revisão integrativa | A revisão É o artigo |
| `revisao-sistematica` | Publicar revisão sistemática | A revisão É o artigo (protocolo PRISMA) |
| `revisao-escopo` | Mapear extensão da evidência | A revisão É o artigo (PRISMA-ScR) |


## Pré-requisitos

- **Python 3.12+**
- API keys gratuitas:
  - **NCBI/PubMed:** registrar email em https://www.ncbi.nlm.nih.gov/account/
  - **OpenAlex:** basta informar email (polite pool)
  - **CrossRef:** basta informar email (polite pool)
  - **Unpaywall:** basta informar email

## Instalação

```bash
cd ~/PKM/Escrita
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements.txt

# Configurar API keys
cp tools/config.example.env tools/.env
# Editar tools/.env com seus dados
```


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

- Fuzzy matching de títulos: `FUZZY_THRESHOLD` em `stages/s03_normalize.py` (default: 85)
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

- Relatório de auditoria que motivou o pipeline: `~/PKM/Escrita/Livro Editora Atheneu/relatorio-audit-deep-research.md`
- Skills que usam este pipeline: `~/bin/escrita-tooling/skills/pesquisa-academica/SKILL.md`, `~/bin/escrita-tooling/skills/revisao-literatura/SKILL.md`
- Decisões arquiteturais: `~/bin/escrita-tooling/tools/DECISOES.md`
