"""Etapa 8 — Verificação de Acesso.

Verifica acessibilidade HTTP e status de Open Access via Unpaywall.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..apis.unpaywall import check_access_http, check_open_access
from ..config import get_api_config
from ..models import AccessStatus, Modality, Reference, SearchConfig


def check_access(
    refs: list[Reference],
    config: SearchConfig,
) -> list[Reference]:
    """Verifica acessibilidade e status OA das referências.

    1. HTTP HEAD/GET para verificar se URL funciona
    2. Unpaywall para refs com DOI (status OA + URL alternativa)

    Args:
        refs: Lista de referências.
        config: SearchConfig (para definir comportamento por modalidade).

    Returns:
        Lista atualizada com access_status, is_open_access, oa_url.
    """
    api_config = get_api_config()
    unpaywall_email = api_config.get("UNPAYWALL_EMAIL", "")

    active_refs = [r for r in refs if not r.is_duplicate]
    print(f"\n  Verificando acesso de {len(active_refs)} referências...")

    # Passo 1: HTTP check em paralelo
    urls_to_check = {r.id: r.url for r in active_refs if r.url}
    http_results = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(check_access_http, url): ref_id
            for ref_id, url in urls_to_check.items()
        }
        for future in as_completed(futures):
            ref_id = futures[future]
            try:
                http_results[ref_id] = future.result()
            except Exception:
                http_results[ref_id] = {
                    "status_code": None,
                    "accessible": False,
                    "status_class": "error",
                }

    # Passo 2: Unpaywall para refs com DOI
    oa_results = {}
    if unpaywall_email:
        dois_to_check = {r.id: r.doi for r in active_refs if r.doi}
        for ref_id, doi in dois_to_check.items():
            try:
                oa_results[ref_id] = check_open_access(doi, email=unpaywall_email)
            except Exception:
                pass

    # Passo 3: Atualizar referências
    accessible_count = 0
    restricted_count = 0
    broken_count = 0
    oa_count = 0

    for ref in active_refs:
        # HTTP result
        http = http_results.get(ref.id, {})
        http_class = http.get("status_class", "no_url")

        # OA result
        oa = oa_results.get(ref.id, {})
        is_oa = oa.get("is_oa", False)
        oa_url = oa.get("oa_url", "")

        # Se já tem info de OA do OpenAlex
        if ref.is_open_access and not is_oa:
            is_oa = ref.is_open_access

        ref.is_open_access = is_oa
        if oa_url:
            ref.oa_url = oa_url

        # Determinar access_status
        if not ref.url:
            ref.access_status = AccessStatus.NO_URL
        elif is_oa:
            ref.access_status = AccessStatus.OPEN_ACCESS
            oa_count += 1
            accessible_count += 1
        elif http_class == "accessible":
            ref.access_status = AccessStatus.ACCESSIBLE
            accessible_count += 1
        elif http_class in ("restricted", "timeout"):
            ref.access_status = AccessStatus.RESTRICTED
            restricted_count += 1
        elif http_class == "broken":
            ref.access_status = AccessStatus.BROKEN
            broken_count += 1
        else:
            ref.access_status = AccessStatus.RESTRICTED
            restricted_count += 1

    no_url = sum(1 for r in active_refs if r.access_status == AccessStatus.NO_URL)
    print(f"  → Acessíveis: {accessible_count} (OA: {oa_count})")
    print(f"  → Restritas: {restricted_count}")
    print(f"  → Quebradas: {broken_count}")
    print(f"  → Sem URL: {no_url}")

    return refs
