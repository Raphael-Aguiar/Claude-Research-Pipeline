"""BVS/LILACS API client (MVP via iAHx URL params).

API instável e mal documentada. Implementação cautelosa:
se a resposta não for parseable, descartar silenciosamente.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import requests

from ..config import DEFAULT_TIMEOUT
from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig

_BASE_URL = "https://pesquisa.bvsalud.org/portal/"


def _build_query(config: SearchConfig) -> str:
    """Constrói query BVS a partir dos keyword_blocks e DeCS terms."""
    parts = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            quoted = [f'"{t}"' for t in terms]
            group = " OR ".join(quoted)
            parts.append(f"({group})")

    query = " AND ".join(parts)

    # Adicionar DeCS terms se existirem
    if config.decs_terms:
        decs_part = " OR ".join(f'(mh:"{t}")' for t in config.decs_terms)
        if query:
            query = f"({query}) OR ({decs_part})"
        else:
            query = decs_part

    return query


def search_bvs_lilacs(
    config: SearchConfig,
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências na BVS/LILACS via iAHx.

    Abordagem cautelosa: se a API falhar ou retornar dados
    não-parseáveis, retorna lista vazia sem erro.
    """
    query = _build_query(config)
    limit = max_results or config.max_results_per_api

    params = {
        "q": query,
        "filter[db][]": "LILACS",
        "output": "json",
        "count": str(min(limit, 50)),
        "from": "0",
        "lang": "pt",
    }

    # Filtro de período
    start, end = config.year_range
    params["filter[year_cluster][]"] = f"{start}-{end}"

    references = []

    try:
        time.sleep(0.5)  # Rate limiting conservador
        resp = requests.get(
            _BASE_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )

        if resp.status_code != 200:
            print(f"    BVS/LILACS: HTTP {resp.status_code}")
            return []

        # Tentar parsear como JSON
        try:
            data = resp.json()
        except Exception:
            print("    BVS/LILACS: resposta não é JSON válido, descartando")
            return []

        # Extrair documentos (formato pode variar)
        docs = data.get("documents", data.get("response", {}).get("docs", []))
        if not docs:
            print("    BVS/LILACS: nenhum documento encontrado")
            return []

        for doc in docs[:limit]:
            ref = _parse_bvs_doc(doc, query)
            if ref:
                references.append(ref)

    except requests.exceptions.Timeout:
        print("    BVS/LILACS: timeout")
    except Exception as e:
        print(f"    BVS/LILACS: erro ({e}), descartando")

    return references


def _parse_bvs_doc(doc: dict, query: str) -> Reference | None:
    """Converte um documento BVS/LILACS em Reference."""
    try:
        # Campos podem ser strings ou listas
        def _first(val):
            if isinstance(val, list):
                return val[0] if val else ""
            return str(val) if val else ""

        title = _first(doc.get("ti", doc.get("title", "")))
        if not title:
            return None

        # Autores
        authors_raw = doc.get("au", doc.get("author", []))
        if isinstance(authors_raw, str):
            authors_raw = [authors_raw]
        authors = [str(a).strip() for a in authors_raw if a]

        # Ano
        year_str = _first(doc.get("da", doc.get("year", "")))
        year = None
        if year_str:
            # Formato pode ser "2023" ou "20230101"
            import re
            m = re.search(r"(\d{4})", year_str)
            if m:
                year = int(m.group(1))

        # Journal
        journal = _first(doc.get("ta", doc.get("journal", "")))

        # DOI
        doi = None
        doi_raw = doc.get("doi", doc.get("identifier", []))
        if isinstance(doi_raw, str):
            doi = doi_raw
        elif isinstance(doi_raw, list):
            for d in doi_raw:
                if "doi" in str(d).lower() or "10." in str(d):
                    doi = str(d)
                    break

        # URL
        url = _first(doc.get("ur", doc.get("url", "")))

        # Abstract
        abstract = _first(doc.get("ab", doc.get("abstract", "")))

        # ID
        lilacs_id = _first(doc.get("id", ""))

        ref = Reference(
            id=f"lilacs_{lilacs_id}" if lilacs_id else f"lilacs_{hash(title) % 100000}",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            url=url or None,
            journal=journal or None,
            abstract=abstract or None,
            source_api="bvs_lilacs",
            search_query=query,
            source="api",
        )
        return ref

    except Exception:
        return None
