"""Etapa 2 — Busca Multi-API.

Orquestra buscas em PubMed, OpenAlex e outras APIs configuradas.
Salva refs-raw.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..apis.bvs_lilacs import search_bvs_lilacs
from ..apis.europe_pmc import search_europe_pmc
from ..apis.openalex import search_openalex
from ..apis.pubmed import search_pubmed
from ..apis.semantic_scholar import search_semantic_scholar
from ..config import get_api_config, get_pipeline_dir
from ..models import Reference, SearchConfig


def run_search(config: SearchConfig) -> list[Reference]:
    """Executa busca em todas as APIs configuradas.

    Args:
        config: SearchConfig do scope.yaml.

    Returns:
        Lista consolidada de referências brutas.
    """
    api_config = get_api_config()
    all_refs: list[Reference] = []
    search_log_entries: list[dict] = []

    for api_name in config.apis:
        print(f"\n  Buscando em {api_name}...")
        try:
            refs = _search_single_api(api_name, config, api_config)
            all_refs.extend(refs)
            print(f"  → {len(refs)} referências encontradas")
            search_log_entries.append({
                "api": api_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": len(refs),
                "query": refs[0].search_query if refs else "",
                "status": "ok",
            })
        except Exception as e:
            print(f"  → ERRO em {api_name}: {e}")
            search_log_entries.append({
                "api": api_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": 0,
                "error": str(e),
                "status": "error",
            })

    # Salvar refs-raw.json
    pipeline_dir = get_pipeline_dir(config.project_name)
    raw_path = pipeline_dir / "refs-raw.json"
    data = {
        "metadata": {
            "project": config.project_name,
            "modality": config.modality.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_refs": len(all_refs),
            "search_log": search_log_entries,
        },
        "references": [ref.to_dict() for ref in all_refs],
    }
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  refs-raw.json salvo: {raw_path} ({len(all_refs)} refs)")

    # Salvar search-log.md
    _write_search_log(config, search_log_entries, pipeline_dir)

    return all_refs


def _search_single_api(
    api_name: str,
    config: SearchConfig,
    api_config: dict[str, str],
) -> list[Reference]:
    """Executa busca em uma API específica."""
    if api_name == "pubmed":
        email = api_config.get("NCBI_EMAIL", "")
        api_key = api_config.get("NCBI_API_KEY", "")
        return search_pubmed(config, email=email, api_key=api_key)

    elif api_name == "openalex":
        email = api_config.get("OPENALEX_EMAIL", "")
        return search_openalex(config, email=email)

    elif api_name == "semantic_scholar":
        api_key = api_config.get("SEMANTIC_SCHOLAR_API_KEY", "")
        return search_semantic_scholar(config, api_key=api_key)

    elif api_name == "europe_pmc":
        return search_europe_pmc(config)

    elif api_name == "bvs_lilacs":
        return search_bvs_lilacs(config)

    else:
        print(f"  API '{api_name}' não implementada "
              f"(disponível: pubmed, openalex, semantic_scholar, europe_pmc, bvs_lilacs)")
        return []


def _write_search_log(
    config: SearchConfig,
    entries: list[dict],
    pipeline_dir,
) -> None:
    """Gera search-log.md com registro reprodutível das buscas."""
    lines = [
        f"# Search Log — {config.project_name}",
        "",
        f"**Modalidade:** {config.modality.value}",
        f"**Pergunta:** {config.research_question}",
        f"**Período:** {config.year_range[0]}–{config.year_range[1]}",
        f"**Idiomas:** {', '.join(config.languages)}",
        "",
        "## Buscas realizadas",
        "",
    ]

    for entry in entries:
        lines.append(f"### {entry['api']}")
        lines.append(f"- **Data/hora:** {entry['timestamp']}")
        lines.append(f"- **Resultados:** {entry['results']}")
        lines.append(f"- **Status:** {entry['status']}")
        if entry.get("query"):
            lines.append(f"- **Query:** `{entry['query']}`")
        if entry.get("error"):
            lines.append(f"- **Erro:** {entry['error']}")
        lines.append("")

    lines.extend([
        "## Keyword blocks",
        "",
    ])
    for block in config.keyword_blocks:
        concept = block.get("concept", "")
        terms = block.get("terms", [])
        lines.append(f"- **{concept}:** {', '.join(terms)}")

    if config.mesh_terms:
        lines.extend([
            "",
            "## MeSH Terms",
            "",
        ])
        for term in config.mesh_terms:
            lines.append(f"- {term}")

    log_path = pipeline_dir / "search-log.md"
    log_path.write_text("\n".join(lines), encoding="utf-8")
