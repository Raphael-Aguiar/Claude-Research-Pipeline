"""Consulta de descritores MeSH via API oficial da NLM.

Endpoint keyless: https://id.nlm.nih.gov/mesh/lookup/descriptor
Retorna descritores oficiais (com URI) para um termo livre.

Sobre DeCS: o DeCS (BIREME) é trilíngue e incorpora os descritores MeSH
traduzidos. A busca da BVS aceita o rótulo MeSH em inglês no campo `mh:`,
então os rótulos sugeridos aqui servem também como `decs_terms`.
Confirmação manual (e tradução PT/ES): https://decs.bvsalud.org
"""

from __future__ import annotations

import time

import requests

from ..config import DEFAULT_TIMEOUT

_LOOKUP_URL = "https://id.nlm.nih.gov/mesh/lookup/descriptor"


def suggest_mesh_descriptors(term: str, limit: int = 5) -> list[dict]:
    """Sugere descritores MeSH oficiais para um termo livre.

    Returns:
        Lista de dicts {"label": ..., "uri": ..., "exact": bool},
        ordenada com matches exatos primeiro. Vazia se nada encontrado
        ou em erro de rede (logado, nunca silencioso).
    """
    if not term or not term.strip():
        return []

    try:
        time.sleep(0.2)
        resp = requests.get(
            _LOOKUP_URL,
            params={"label": term.strip(), "match": "contains", "limit": limit},
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            print(f"    MeSH lookup: HTTP {resp.status_code} para '{term}'")
            return []
        items = resp.json()
    except Exception as e:
        print(f"    MeSH lookup: erro para '{term}' ({e})")
        return []

    results = []
    for item in items:
        label = item.get("label", "")
        if not label:
            continue
        results.append({
            "label": label,
            "uri": item.get("resource", ""),
            "exact": label.lower() == term.strip().lower(),
        })

    results.sort(key=lambda r: (not r["exact"], r["label"]))
    return results


def suggest_for_config(config) -> dict[str, list[dict]]:
    """Sugere descritores MeSH para todos os termos dos keyword_blocks.

    Returns:
        Dict termo → lista de sugestões (pode ser vazia por termo).
    """
    terms: list[str] = []
    for block in config.keyword_blocks:
        for t in block.get("terms", []):
            if t and t not in terms:
                terms.append(t)

    suggestions: dict[str, list[dict]] = {}
    for term in terms:
        suggestions[term] = suggest_mesh_descriptors(term)
    return suggestions
