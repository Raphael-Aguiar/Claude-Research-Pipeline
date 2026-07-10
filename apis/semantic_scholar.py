"""Semantic Scholar Academic Graph API client.

Suporta busca por relevância, busca em massa com sintaxe booleana,
e extração de citation context/intents.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import requests

from ..config import DEFAULT_TIMEOUT
from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig

_BASE_URL = "https://api.semanticscholar.org/graph/v1"

# Campos solicitados por padrão
_SEARCH_FIELDS = (
    "paperId,corpusId,externalIds,url,title,abstract,venue,"
    "year,referenceCount,citationCount,influentialCitationCount,"
    "isOpenAccess,openAccessPdf,fieldsOfStudy,publicationTypes,"
    "publicationDate,journal,authors"
)


def _build_query(config: SearchConfig) -> str:
    """Constrói query textual a partir dos keyword_blocks.

    Semantic Scholar /paper/search aceita texto livre (sem booleanos).
    Para /paper/search/bulk, booleanos são suportados.
    """
    all_terms = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            # Usar aspas para frases compostas
            quoted = [f'"{t}"' if " " in t else t for t in terms[:5]]
            all_terms.append(f"({' | '.join(quoted)})")
    return " ".join(all_terms)


def search_semantic_scholar(
    config: SearchConfig,
    api_key: str = "",
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências no Semantic Scholar.

    Usa /paper/search/bulk quando api_key disponível (suporta booleanos),
    senão usa /paper/search (texto livre).

    Args:
        config: SearchConfig do scope.yaml.
        api_key: API key do Semantic Scholar (opcional).
        max_results: Override de max_results_per_api.

    Returns:
        Lista de Reference com campos de identidade preenchidos.
    """
    query = _build_query(config)
    limit = max_results or config.max_results_per_api
    start_year, end_year = config.year_range

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    # Usar bulk endpoint se tiver API key (suporta booleanos e paginação por token)
    if api_key:
        return _search_bulk(query, start_year, end_year, limit, headers)
    else:
        return _search_relevance(query, start_year, end_year, limit, headers)


def _search_relevance(
    query: str,
    start_year: int,
    end_year: int,
    limit: int,
    headers: dict,
) -> list[Reference]:
    """Busca via /paper/search (texto livre, ordenado por relevância)."""
    references = []
    offset = 0
    per_page = min(limit, 100)

    while len(references) < limit:
        params = {
            "query": query,
            "fields": _SEARCH_FIELDS,
            "offset": offset,
            "limit": per_page,
            "year": f"{start_year}-{end_year}",
            "fieldsOfStudy": "Medicine",
        }

        try:
            time.sleep(1.0)  # Rate limit: 1 req/s sem key
            resp = requests.get(
                f"{_BASE_URL}/paper/search",
                params=params,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                break

            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                break

            for paper in papers:
                ref = _parse_paper(paper, query)
                if ref:
                    references.append(ref)

            # Verificar se há próxima página
            if data.get("next") is None or len(papers) < per_page:
                break
            offset = data["next"]

        except Exception:
            break

    return references[:limit]


def _search_bulk(
    query: str,
    start_year: int,
    end_year: int,
    limit: int,
    headers: dict,
) -> list[Reference]:
    """Busca via /paper/search/bulk (booleanos, ordenação por citações)."""
    references = []
    token = None

    while len(references) < limit:
        params = {
            "query": query,
            "fields": _SEARCH_FIELDS,
            "year": f"{start_year}-{end_year}",
            "fieldsOfStudy": "Medicine",
            "sort": "citationCount:desc",
        }
        if token:
            params["token"] = token

        try:
            time.sleep(1.0)
            resp = requests.get(
                f"{_BASE_URL}/paper/search/bulk",
                params=params,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                break

            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                break

            for paper in papers:
                ref = _parse_paper(paper, query)
                if ref:
                    references.append(ref)

            token = data.get("token")
            if not token:
                break

        except Exception:
            break

    return references[:limit]


def _parse_paper(paper: dict, query: str) -> Reference | None:
    """Converte um registro Semantic Scholar em Reference."""
    try:
        paper_id = paper.get("paperId", "")
        title = paper.get("title") or ""

        # Autores
        authors = []
        for author in paper.get("authors", []):
            name = author.get("name", "")
            if name:
                authors.append(name)

        # Ano
        year = paper.get("year")

        # IDs externos
        ext_ids = paper.get("externalIds") or {}
        doi = ext_ids.get("DOI")
        pmid = ext_ids.get("PubMed")

        # Journal
        journal_info = paper.get("journal") or {}
        journal = journal_info.get("name")
        volume = journal_info.get("volume")
        pages = journal_info.get("pages")

        # URL
        url = paper.get("url")

        # Abstract
        abstract = paper.get("abstract")

        # Open access
        is_oa = paper.get("isOpenAccess", False)
        oa_pdf = paper.get("openAccessPdf") or {}
        oa_url = oa_pdf.get("url")

        # Tipo de publicação
        pub_types = paper.get("publicationTypes") or []
        pub_type = pub_types[0] if pub_types else None

        ref = Reference(
            id=f"s2_{paper_id[:12]}" if paper_id else "",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            pmid=str(pmid) if pmid else None,
            url=url,
            journal=journal,
            abstract=abstract,
            volume=volume,
            pages=pages,
            source_api="semantic_scholar",
            search_query=query,
            pub_type=pub_type,
            is_open_access=is_oa,
            oa_url=oa_url,
        )
        return ref

    except Exception as e:
        print(f"    semantic_scholar: registro descartado por erro de parsing ({e})")
        return None
