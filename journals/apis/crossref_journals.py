"""Crossref /journals/{issn} — metadados e ISSNs registrados (fallback)."""

from __future__ import annotations

import requests

from ...config import DEFAULT_TIMEOUT

BASE = "https://api.crossref.org/journals"


def buscar_journal_por_issn(issn: str, email: str = "") -> dict | None:
    """Metadados do periódico no Crossref. Retorna dict normalizado ou None."""
    params = {"mailto": email} if email else {}
    resp = requests.get(f"{BASE}/{issn}", params=params, timeout=DEFAULT_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    msg = resp.json().get("message", {})
    return {
        "titulo": msg.get("title"),
        "editora": msg.get("publisher"),
        "issns": msg.get("ISSN") or [],
        "total_dois": (msg.get("counts") or {}).get("total-dois"),
    }
