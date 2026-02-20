"""CrossRef API client — verificação de DOI e metadados."""

from __future__ import annotations

import time

from habanero import Crossref
from rapidfuzz import fuzz


def verify_doi(
    doi: str,
    expected_title: str = "",
    email: str = "",
) -> dict:
    """Verifica se um DOI resolve no CrossRef e compara metadados.

    Args:
        doi: DOI a verificar.
        expected_title: Título esperado para comparação fuzzy.
        email: Email para polite pool.

    Returns:
        Dict com:
        - resolves (bool): DOI existe no CrossRef
        - title_match (bool): Título fuzzy match >= 80
        - title_similarity (float): Score de similaridade 0-100
        - crossref_title (str): Título retornado pelo CrossRef
        - pub_type (str): Tipo de publicação
        - is_retracted (bool): Artigo retratado (Crossmark)
        - has_update (bool): Tem correção/atualização
        - metadata (dict): Metadados completos
    """
    result = {
        "resolves": False,
        "title_match": False,
        "title_similarity": 0.0,
        "crossref_title": "",
        "pub_type": "",
        "is_retracted": False,
        "has_update": False,
        "metadata": {},
    }

    if not doi:
        return result

    cr = Crossref(mailto=email) if email else Crossref()

    try:
        work = cr.works(ids=doi)
        msg = work.get("message", {})

        result["resolves"] = True
        result["metadata"] = msg

        # Título
        titles = msg.get("title", [])
        crossref_title = titles[0] if titles else ""
        result["crossref_title"] = crossref_title

        # Comparação fuzzy de título
        if expected_title and crossref_title:
            similarity = fuzz.ratio(
                expected_title.lower().strip(),
                crossref_title.lower().strip(),
            )
            result["title_similarity"] = similarity
            result["title_match"] = similarity >= 80

        # Tipo de publicação
        result["pub_type"] = msg.get("type", "")

        # Retração (Crossmark)
        result["is_retracted"] = _check_retraction(msg)
        result["has_update"] = _check_update(msg)

    except Exception:
        result["resolves"] = False

    return result


def search_crossref(
    query: str,
    email: str = "",
    max_results: int = 20,
) -> list[dict]:
    """Busca no CrossRef por query textual (fallback para refs sem DOI).

    Returns:
        Lista de dicts com metadados básicos.
    """
    cr = Crossref(mailto=email) if email else Crossref()

    try:
        results = cr.works(
            query=query,
            limit=max_results,
            sort="relevance",
            order="desc",
        )
        items = results.get("message", {}).get("items", [])
        return items
    except Exception:
        return []


def _check_retraction(msg: dict) -> bool:
    """Verifica se o artigo foi retratado via Crossmark update-to."""
    updates = msg.get("update-to", [])
    for update in updates:
        if update.get("type") == "retraction":
            return True
    # Verificar assertion de retração
    assertions = msg.get("assertion", [])
    for assertion in assertions:
        if "retract" in assertion.get("name", "").lower():
            return True
    return False


def _check_update(msg: dict) -> bool:
    """Verifica se o artigo tem correção ou atualização.

    Nota: msg['update-policy'] indica apenas que o publisher tem
    política de updates registrada, NÃO que este artigo foi corrigido.
    Verificamos apenas 'update-to' (updates reais deste artigo).
    """
    updates = msg.get("update-to", [])
    for update in updates:
        if update.get("type") in ("correction", "erratum", "addendum"):
            return True
    return False


def get_crossref_metadata(doi: str, email: str = "") -> dict | None:
    """Obtém metadados completos de um DOI no CrossRef."""
    cr = Crossref(mailto=email) if email else Crossref()
    try:
        time.sleep(0.1)  # Rate limiting
        work = cr.works(ids=doi)
        return work.get("message", {})
    except Exception:
        return None


def extract_authors_from_crossref(metadata: dict) -> list[str]:
    """Extrai autores de metadados CrossRef no formato 'Sobrenome I'.

    Args:
        metadata: Dict de resposta do CrossRef (msg).

    Returns:
        Lista de autores no formato Vancouver ('Sobrenome INICIAIS').
    """
    authors = []
    for a in metadata.get("author", []):
        family = a.get("family", "")
        given = a.get("given", "")
        if not family:
            continue
        if given:
            initials = "".join(w[0] for w in given.split() if w)
            authors.append(f"{family} {initials}")
        else:
            authors.append(family)
    return authors


def extract_journal_from_crossref(metadata: dict) -> dict:
    """Extrai metadados de journal de resposta CrossRef.

    Returns:
        Dict com: journal_full, journal_short, volume, issue, pages, year.
    """
    container = metadata.get("container-title", [""])
    short = metadata.get("short-container-title", [""])
    issued = metadata.get("issued", {}).get("date-parts", [[None]])
    year = str(issued[0][0]) if issued and issued[0] and issued[0][0] else ""

    return {
        "journal_full": container[0] if container else "",
        "journal_short": short[0] if short else "",
        "volume": metadata.get("volume", ""),
        "issue": metadata.get("issue", ""),
        "pages": metadata.get("page", "") or metadata.get("article-number", ""),
        "year": year,
    }
