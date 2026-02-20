"""Europe PMC REST API client.

API aberta, sem autenticação. Suporta busca avançada com campos,
booleanos, wildcards, e filtros de acesso aberto / texto completo.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import requests

from ..config import DEFAULT_TIMEOUT
from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig

_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _build_query(config: SearchConfig) -> str:
    """Constrói query Europe PMC a partir dos keyword_blocks.

    Sintaxe: campo:valor, AND/OR/NOT, parênteses.
    """
    parts = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            quoted = [f'"{t}"' for t in terms]
            group = " OR ".join(quoted)
            parts.append(f"({group})")

    query = " AND ".join(parts)

    # Filtro de período
    start, end = config.year_range
    query += f" AND FIRST_PDATE:[{start}-01-01 TO {end}-12-31]"

    return query


def search_europe_pmc(
    config: SearchConfig,
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências no Europe PMC.

    Args:
        config: SearchConfig do scope.yaml.
        max_results: Override de max_results_per_api.

    Returns:
        Lista de Reference com campos de identidade preenchidos.
    """
    query = _build_query(config)
    limit = max_results or config.max_results_per_api

    references = []
    cursor_mark = "*"
    page_size = min(limit, 1000)

    while len(references) < limit:
        params = {
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": page_size,
            "cursorMark": cursor_mark,
        }

        try:
            time.sleep(0.15)  # ~7 req/s (conservador)
            resp = requests.get(
                f"{_BASE_URL}/search",
                params=params,
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code != 200:
                break

            data = resp.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                break

            for item in results:
                ref = _parse_europe_pmc_result(item, query)
                if ref:
                    references.append(ref)

            # Paginação por cursor
            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor_mark:
                break
            cursor_mark = next_cursor

        except Exception:
            break

    return references[:limit]


def _parse_europe_pmc_result(item: dict, query: str) -> Reference | None:
    """Converte um resultado Europe PMC em Reference."""
    try:
        pmid = item.get("pmid")
        pmcid = item.get("pmcid")
        doi = item.get("doi")
        title = item.get("title") or ""

        # Autores
        authors = []
        author_list = item.get("authorList", {}).get("author", [])
        for author in author_list:
            first = author.get("firstName", "")
            last = author.get("lastName", "")
            if last:
                authors.append(f"{last} {first}".strip())

        # Fallback: authorString
        if not authors:
            author_str = item.get("authorString", "")
            if author_str:
                authors = [a.strip() for a in author_str.split(",")][:10]

        # Ano
        year_str = item.get("pubYear", "")
        year = int(year_str) if year_str and year_str.isdigit() else None

        # Journal
        journal = item.get("journalTitle")
        volume = item.get("journalVolume")
        issue = item.get("issue")
        pages = item.get("pageInfo")

        # Abstract
        abstract = item.get("abstractText")

        # URL
        url = None
        if pmcid:
            url = f"https://europepmc.org/article/PMC/{pmcid}"
        elif pmid:
            url = f"https://europepmc.org/article/MED/{pmid}"
        elif doi:
            url = f"https://doi.org/{doi}"

        # Open access
        is_oa = item.get("isOpenAccess") == "Y"
        oa_url = None
        ft_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
        for ft in ft_urls:
            if ft.get("availabilityCode") == "OA" and ft.get("documentStyle") == "pdf":
                oa_url = ft.get("url")
                break

        # Tipo de publicação
        pub_type = item.get("pubType")

        # Citation count
        cited_by_count = item.get("citedByCount")

        ref = Reference(
            id=f"epmc_{pmid or pmcid or doi or ''}",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            pmid=str(pmid) if pmid else None,
            url=url,
            journal=journal,
            abstract=abstract,
            volume=volume,
            issue=issue,
            pages=pages,
            source_api="europe_pmc",
            search_query=query,
            pub_type=pub_type,
            is_open_access=is_oa,
            oa_url=oa_url,
            citation_count=cited_by_count,
            source="api",
        )
        return ref

    except Exception:
        return None
