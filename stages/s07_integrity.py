"""Etapa 7 — Verificação de Integridade.

Verifica retratações e correções via Crossmark (CrossRef).
"""

from __future__ import annotations

import time

from ..apis.crossref import verify_doi
from ..config import get_api_config
from ..models import Reference


def check_integrity(
    refs: list[Reference],
    project_name: str,
) -> list[Reference]:
    """Verifica integridade das referências (retratações, correções).

    Referências já verificadas na etapa 4 podem ter retracted=True.
    Esta etapa re-verifica apenas as que não foram verificadas.

    Args:
        refs: Lista de referências.
        project_name: Nome do projeto.

    Returns:
        Lista atualizada com retracted e has_correction.
    """
    api_config = get_api_config()
    email = api_config.get("CROSSREF_EMAIL", "")

    # Apenas refs não-duplicadas com DOI e sem verificação prévia
    to_check = [
        r for r in refs
        if not r.is_duplicate and r.doi and r.retracted is None
    ]

    if not to_check:
        print("\n  Integridade: todas as referências já verificadas na etapa 4.")
        return refs

    print(f"\n  Verificando integridade de {len(to_check)} referências...")

    retracted_count = 0
    correction_count = 0

    for ref in to_check:
        try:
            result = verify_doi(ref.doi, email=email)
            if result["resolves"]:
                ref.retracted = result["is_retracted"]
                ref.has_correction = result["has_update"]
                if ref.retracted:
                    retracted_count += 1
                    print(f"  ⚠ RETRATADO: {ref.title[:60]}...")
                if ref.has_correction:
                    correction_count += 1
            else:
                ref.retracted = False
                ref.has_correction = False
            time.sleep(0.1)
        except Exception:
            ref.retracted = False
            ref.has_correction = False

    # Para refs sem DOI, marcar como não verificado
    for ref in refs:
        if not ref.is_duplicate and not ref.doi:
            if ref.retracted is None:
                ref.retracted = False
            if ref.has_correction is None:
                ref.has_correction = False

    print(f"  → {retracted_count} retratações encontradas")
    print(f"  → {correction_count} correções/errata encontradas")

    return refs
