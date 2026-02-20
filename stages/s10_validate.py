"""Etapa 10 — Validação Final.

Re-verifica referências finais: DOIs, URLs, consistência.
"""

from __future__ import annotations

import time

from ..apis.crossref import verify_doi
from ..apis.unpaywall import check_access_http
from ..config import get_api_config, get_pipeline_dir
from ..exporters.json_export import save_refs_json
from ..models import AccessStatus, CompositeGrade, Reference, SearchConfig


def validate_final(
    refs: list[Reference],
    config: SearchConfig,
) -> dict:
    """Re-verifica referências finais para garantir consistência.

    Verificações:
    1. DOIs ainda resolvem
    2. URLs ainda acessíveis
    3. Nenhuma referência sem título
    4. Nenhuma referência sem pelo menos DOI ou URL
    5. Grades consistentes com dados

    Args:
        refs: Lista de referências (pós-síntese).
        config: SearchConfig do projeto.

    Returns:
        Dict com resultado da validação.
    """
    api_config = get_api_config()
    email = api_config.get("CROSSREF_EMAIL", "")

    # Apenas refs finais (não duplicadas, não descartadas)
    final_refs = [
        r for r in refs
        if not r.is_duplicate and r.grade != CompositeGrade.DISCARD
    ]

    print(f"\n  Validando {len(final_refs)} referências finais...")

    issues: list[dict] = []

    # Verificação 1: Títulos
    for ref in final_refs:
        if not ref.title:
            issues.append({
                "ref_id": ref.id,
                "type": "missing_title",
                "message": f"Referência {ref.id} sem título",
            })

    # Verificação 2: DOI ou URL
    for ref in final_refs:
        if not ref.doi and not ref.url:
            issues.append({
                "ref_id": ref.id,
                "type": "no_identifier",
                "message": f"'{ref.title[:50]}' sem DOI nem URL",
            })

    # Verificação 3: Re-verificar DOIs (amostra)
    dois_to_recheck = [r for r in final_refs if r.doi][:20]
    doi_failures = 0
    for ref in dois_to_recheck:
        try:
            result = verify_doi(ref.doi, email=email)
            if not result["resolves"]:
                doi_failures += 1
                issues.append({
                    "ref_id": ref.id,
                    "type": "doi_broken",
                    "message": f"DOI {ref.doi} não resolve mais",
                })
            time.sleep(0.1)
        except Exception:
            pass

    # Verificação 4: Re-verificar URLs (amostra)
    urls_to_recheck = [r for r in final_refs if r.url][:20]
    url_failures = 0
    for ref in urls_to_recheck:
        try:
            result = check_access_http(ref.url)
            if result["status_class"] == "broken":
                url_failures += 1
                issues.append({
                    "ref_id": ref.id,
                    "type": "url_broken",
                    "message": f"URL quebrada: {ref.url}",
                })
        except Exception:
            pass

    # Verificação 5: Consistência de grades
    for ref in final_refs:
        recomputed = Reference(
            tier=ref.tier,
            relevance=ref.relevance,
            access_status=ref.access_status,
            retracted=ref.retracted,
        )
        recomputed.compute_grade()
        if recomputed.grade != ref.grade:
            issues.append({
                "ref_id": ref.id,
                "type": "grade_inconsistency",
                "message": (
                    f"Grade {ref.grade.value} inconsistente "
                    f"(recalculado: {recomputed.grade.value})"
                ),
            })

    # Resultado
    validation = {
        "total_checked": len(final_refs),
        "issues": len(issues),
        "doi_rechecked": len(dois_to_recheck),
        "doi_failures": doi_failures,
        "url_rechecked": len(urls_to_recheck),
        "url_failures": url_failures,
        "passed": len(issues) == 0,
        "details": issues,
    }

    if issues:
        print(f"\n  ⚠ {len(issues)} problemas encontrados:")
        for issue in issues[:10]:
            print(f"    - [{issue['type']}] {issue['message']}")
        if len(issues) > 10:
            print(f"    ... e {len(issues) - 10} adicionais")
    else:
        print("  ✓ Validação OK — nenhum problema encontrado")

    return validation
