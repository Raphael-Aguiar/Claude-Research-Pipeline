"""DOAJ API v4 — APC (valor + moeda), waiver, licença, peer review.

Endpoint público de busca, sem API key. O DOAJ NÃO fornece tempo de
revisão confiável (campo removido do formulário/CSV em 2020) — esse
dado vem da extração de páginas de editora (F3), nunca daqui.
"""

from __future__ import annotations

import requests

from ...config import DEFAULT_TIMEOUT

BASE = "https://doaj.org/api/v4/search/journals"


def buscar_journal_por_issn(issn: str) -> dict | None:
    """Busca o periódico no DOAJ. Retorna dict normalizado ou None.

    None significa "não está no DOAJ" — informação válida em si
    (in_doaj=False), distinta de erro de rede (exceção propaga).
    """
    resp = requests.get(
        f"{BASE}/issn:%22{issn}%22", timeout=DEFAULT_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    resultados = resp.json().get("results", [])
    if not resultados:
        return None
    bib = resultados[0].get("bibjson", {})

    apc = bib.get("apc") or {}
    apc_valor = apc_moeda = None
    if apc.get("has_apc") and apc.get("max"):
        # Quando há vários preços, preferir USD; senão o primeiro.
        precos = apc["max"]
        escolhido = next(
            (p for p in precos if p.get("currency") == "USD"), precos[0]
        )
        apc_valor = escolhido.get("price")
        apc_moeda = escolhido.get("currency")
    apc_url = apc.get("url")  # página de fees da editora, quando declarada

    licencas = [
        l.get("type") for l in (bib.get("license") or []) if l.get("type")
    ]
    review = (bib.get("editorial") or {}).get("review_process") or []
    if isinstance(review, str):
        review = [review]

    return {
        "titulo": bib.get("title"),
        "editora": (bib.get("publisher") or {}).get("name"),
        "tem_apc": bool(apc.get("has_apc")),
        "apc_valor": apc_valor,
        "apc_moeda": apc_moeda,
        "apc_url": apc_url,
        "apc_waiver": bool((bib.get("waiver") or {}).get("has_waiver")),
        "licenca": ", ".join(licencas) or None,
        "peer_review": ", ".join(review) or None,
    }
