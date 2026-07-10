"""Unpaywall API client — verificação de Open Access."""

from __future__ import annotations

import time

import requests

from ..config import DEFAULT_HEADERS, DEFAULT_TIMEOUT

_BASE_URL = "https://api.unpaywall.org/v2"


def check_open_access(
    doi: str,
    email: str = "",
) -> dict:
    """Verifica status de Open Access via Unpaywall.

    Args:
        doi: DOI do artigo.
        email: Email obrigatório para a API.

    Returns:
        Dict com:
        - is_oa (bool)
        - oa_status (str): gold, green, hybrid, bronze, closed
        - oa_url (str): URL do PDF/HTML em OA
        - best_oa_location (dict): Melhor localização OA
    """
    result = {
        "is_oa": False,
        "oa_status": "closed",
        "oa_url": "",
        "best_oa_location": {},
    }

    if not doi or not email:
        return result

    # Limpar DOI
    doi_clean = doi.strip()
    if doi_clean.startswith("https://doi.org/"):
        doi_clean = doi_clean.replace("https://doi.org/", "")
    if doi_clean.startswith("http://doi.org/"):
        doi_clean = doi_clean.replace("http://doi.org/", "")

    url = f"{_BASE_URL}/{doi_clean}?email={email}"

    try:
        time.sleep(0.1)  # Rate limiting
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return result

        data = resp.json()
        result["is_oa"] = data.get("is_oa", False)
        result["oa_status"] = data.get("oa_status", "closed")

        best_loc = data.get("best_oa_location") or {}
        result["best_oa_location"] = best_loc
        result["oa_url"] = best_loc.get("url_for_pdf") or best_loc.get("url") or ""

    except Exception as e:
        print(f"    Unpaywall: erro ao consultar {doi_clean} ({e})")

    return result


def check_access_http(
    url: str,
) -> dict:
    """Verifica acessibilidade HTTP de uma URL.

    Returns:
        Dict com:
        - status_code (int | None)
        - accessible (bool)
        - status_class (str): accessible, restricted, broken, timeout, error
    """
    result = {
        "status_code": None,
        "accessible": False,
        "status_class": "error",
    }

    if not url or not url.startswith("http"):
        result["status_class"] = "no_url"
        return result

    try:
        resp = requests.head(
            url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        result["status_code"] = resp.status_code

        if resp.status_code == 200:
            result["accessible"] = True
            result["status_class"] = "accessible"
        elif resp.status_code in (403, 405, 406):
            # Tenta GET (alguns sites bloqueiam HEAD)
            resp = requests.get(
                url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
                allow_redirects=True, stream=True,
            )
            resp.close()
            result["status_code"] = resp.status_code
            if resp.status_code == 200:
                result["accessible"] = True
                result["status_class"] = "accessible"
            else:
                result["status_class"] = "restricted"
        elif resp.status_code == 404:
            result["status_class"] = "broken"
        elif resp.status_code in (301, 302, 303, 307, 308):
            result["accessible"] = True
            result["status_class"] = "accessible"
        elif resp.status_code in (429, 503):
            result["status_class"] = "restricted"
        else:
            result["status_class"] = "restricted"

    except requests.exceptions.Timeout:
        result["status_class"] = "timeout"
    except requests.exceptions.ConnectionError:
        result["status_class"] = "broken"
    except Exception:
        result["status_class"] = "error"

    return result
