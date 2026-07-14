"""NLM Catalog (E-utilities) — verificação de indexação MEDLINE por ISSN."""

from __future__ import annotations

import requests

from ...config import DEFAULT_TIMEOUT

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"


def medline_indexado(
    issn: str, email: str = "", api_key: str = ""
) -> bool | None:
    """True/False conforme o periódico esteja atualmente indexado no MEDLINE.

    Usa `currentlyindexed` (flag oficial do NLM Catalog). Retorna None
    apenas quando a resposta não é interpretável.
    """
    params = {
        "db": "nlmcatalog",
        "term": f"{issn}[ISSN] AND currentlyindexed",
        "retmode": "json",
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(ESEARCH, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    try:
        count = int(resp.json()["esearchresult"]["count"])
    except (KeyError, ValueError, TypeError):
        return None
    return count > 0
