"""BVS API client — busca federada BIREME (LILACS + SciELO + MEDLINE + ...).

Caminho principal: API oficial https://api.bvsalud.org/search/v1 (requer
BVS_API_KEY — chave gratuita solicitada em https://api.bvsalud.org).
O portal público pesquisa.bvsalud.org passou a ficar atrás de desafio
anti-robô (Bunny Shield, verificado 2026-07-10) e é mantido apenas como
fallback de melhor esforço — normalmente retorna 403 para clientes HTTP.

A cobertura SciELO vem daqui (índice federado) e também via OpenAlex/
Crossref (DOIs de periódicos SciELO).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import requests

from ..config import DEFAULT_TIMEOUT
from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig

_API_URL = "https://api.bvsalud.org/search/v1/"
_PORTAL_URL = "https://pesquisa.bvsalud.org/portal/"


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
    api_key: str = "",
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências na BVS (LILACS + SciELO + MEDLINE federados).

    Com BVS_API_KEY: usa a API oficial (api.bvsalud.org).
    Sem chave: tenta o portal público (normalmente bloqueado por
    anti-robô) e instrui como obter a chave gratuita.
    """
    query = _build_query(config)
    limit = max_results or config.max_results_per_api

    if api_key:
        return _search_official_api(query, config, api_key, limit)

    print(
        "    BVS: sem BVS_API_KEY configurada — o portal público está atrás "
        "de desafio anti-robô e provavelmente falhará.\n"
        "    Solicite a chave GRATUITA em https://api.bvsalud.org e adicione "
        "BVS_API_KEY=<chave> ao tools/.env."
    )
    return _search_portal_fallback(query, config, limit)


def _search_official_api(
    query: str,
    config: SearchConfig,
    api_key: str,
    limit: int,
) -> list[Reference]:
    """Busca via API oficial BIREME (api.bvsalud.org/search/v1)."""
    references: list[Reference] = []
    start, end = config.year_range
    count = min(limit, 100)
    offset = 0

    while len(references) < limit:
        params = {
            "q": query,
            "count": str(count),
            "start": str(offset),
            "lang": "pt",
            "fq": f"year_cluster:[{start} TO {end}]",
        }
        try:
            time.sleep(0.5)
            resp = requests.get(
                _API_URL,
                params=params,
                timeout=DEFAULT_TIMEOUT,
                headers={"apikey": api_key, "Accept": "application/json"},
            )
            if resp.status_code == 401:
                print("    BVS API: chave inválida/expirada (HTTP 401)")
                return references
            if resp.status_code != 200:
                print(f"    BVS API: HTTP {resp.status_code}")
                return references

            data = resp.json()
            docs = _extract_docs(data)
            if not docs:
                break

            for doc in docs:
                ref = _parse_bvs_doc(doc, query)
                if ref:
                    references.append(ref)
                if len(references) >= limit:
                    break

            if len(docs) < count:
                break
            offset += count

        except requests.exceptions.Timeout:
            print("    BVS API: timeout")
            break
        except Exception as e:
            print(f"    BVS API: erro ({e})")
            break

    return references


def _extract_docs(data: dict) -> list[dict]:
    """Extrai a lista de documentos, tolerando os formatos iAHx conhecidos."""
    if "diaServerResponse" in data:
        blocks = data.get("diaServerResponse") or []
        if blocks and isinstance(blocks, list):
            return blocks[0].get("response", {}).get("docs", [])
    return data.get("documents", data.get("response", {}).get("docs", []))


def _search_portal_fallback(
    query: str,
    config: SearchConfig,
    limit: int,
) -> list[Reference]:
    """Fallback de melhor esforço via portal público (frequentemente 403)."""
    start, end = config.year_range
    params = {
        "q": query,
        "output": "json",
        "count": str(min(limit, 50)),
        "from": "0",
        "lang": "pt",
        "filter[year_cluster][]": f"{start}-{end}",
    }
    references: list[Reference] = []
    try:
        time.sleep(0.5)
        resp = requests.get(
            _PORTAL_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            print(f"    BVS portal: HTTP {resp.status_code} (esperado sem chave)")
            return []
        try:
            data = resp.json()
        except Exception:
            print("    BVS portal: resposta não é JSON (desafio anti-robô)")
            return []
        for doc in _extract_docs(data)[:limit]:
            ref = _parse_bvs_doc(doc, query)
            if ref:
                references.append(ref)
    except requests.exceptions.Timeout:
        print("    BVS portal: timeout")
    except Exception as e:
        print(f"    BVS portal: erro ({e})")
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

    except Exception as e:
        print(f"    bvs_lilacs: registro descartado por erro de parsing ({e})")
        return None
