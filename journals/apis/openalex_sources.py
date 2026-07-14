"""OpenAlex /sources — identidade canônica, métricas e tópicos do periódico.

Campos usados: issn_l, issn[], display_name, host_organization_name,
country_code, homepage_url, is_oa, is_in_doaj, apc_usd (origem DOAJ),
summary_stats (2yr_mean_citedness, h_index), works_count, topics.

Sem API key (polite pool via mailto). Verificado em 2026-07-11: a API
pública segue aceitando requisições sem key; se passar a exigir
(anúncio de fev/2026 na lista openalex-users), adicionar
OPENALEX_API_KEY ao .env e ao header Authorization aqui.
"""

from __future__ import annotations

import requests

from ...config import DEFAULT_TIMEOUT

BASE = "https://api.openalex.org/sources"


def buscar_source_por_issn(issn: str, email: str = "") -> dict | None:
    """Busca o source pelo ISSN. Retorna dict normalizado ou None."""
    params = {"filter": f"issn:{issn}", "per-page": 1}
    if email:
        params["mailto"] = email
    resp = requests.get(BASE, params=params, timeout=DEFAULT_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    resultados = resp.json().get("results", [])
    if not resultados:
        return None
    return _normalizar(resultados[0])


def _normalizar(src: dict) -> dict:
    stats = src.get("summary_stats") or {}
    topics = [
        t.get("display_name")
        for t in (src.get("topics") or [])[:15]
        if t.get("display_name")
    ]
    return {
        "openalex_id": src.get("id"),
        "issn_l": src.get("issn_l"),
        "issns": src.get("issn") or [],
        "titulo": src.get("display_name"),
        "editora": src.get("host_organization_name"),
        "pais": src.get("country_code"),
        "homepage_url": src.get("homepage_url"),
        "is_oa": src.get("is_oa"),
        "in_doaj": src.get("is_in_doaj"),
        "apc_usd": src.get("apc_usd"),
        "citedness_2yr": stats.get("2yr_mean_citedness"),
        "h_index": stats.get("h_index"),
        "works_count": src.get("works_count"),
        "topicos": topics,
    }
