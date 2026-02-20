"""Etapa 5 — Classificação por Tier.

Classifica referências por domínio (TIER_MAP) e tipo de publicação (CrossRef).
"""

from __future__ import annotations

from ..models import Reference, Tier
from ..tier_map import classify_domain


def classify_references(refs: list[Reference]) -> list[Reference]:
    """Classifica referências por tier de qualidade.

    Estratégia:
    1. classify_domain() pelo URL → Tier via TIER_MAP
    2. Se DOI verificado, usar pub_type do CrossRef como refinamento
    3. Se PMID existe, garantir pelo menos T1

    Args:
        refs: Lista de referências (pós-verificação).

    Returns:
        Lista atualizada com tier e domain preenchidos.
    """
    print("\n  Classificando referências por tier...")

    tier_counts = {t: 0 for t in Tier}

    for ref in refs:
        if ref.is_duplicate:
            continue

        # Classificar por domínio da URL
        domain, tier = classify_domain(ref.url or "")
        ref.domain = domain
        ref.tier = tier

        # Refinamento: PMID garante pelo menos T1
        if ref.pmid and ref.tier == Tier.UNKNOWN:
            ref.tier = Tier.T1
            ref.domain = ref.domain or "pubmed"

        # Refinamento: tipo de publicação do CrossRef
        if ref.pub_type and ref.tier == Tier.UNKNOWN:
            ref.tier = _tier_from_pub_type(ref.pub_type)

        # Refinamento: DOI de periódico = pelo menos T1
        if ref.doi_resolves and ref.tier == Tier.UNKNOWN:
            ref.tier = Tier.T1

        tier_counts[ref.tier] += 1

    # Estatísticas
    unique = sum(1 for r in refs if not r.is_duplicate)
    for tier, count in sorted(tier_counts.items(), key=lambda x: x[0].value):
        pct = (count / unique * 100) if unique else 0
        print(f"  → {tier.name}: {count} ({pct:.0f}%)")

    return refs


def _tier_from_pub_type(pub_type: str) -> Tier:
    """Infere tier a partir do tipo de publicação CrossRef."""
    pt = pub_type.lower()

    # Tipos acadêmicos → T1
    if pt in (
        "journal-article", "proceedings-article", "book-chapter",
        "dissertation", "posted-content", "peer-review",
        "reference-entry", "monograph",
    ):
        return Tier.T1

    # Relatórios → T3
    if pt in ("report", "report-component", "dataset", "standard"):
        return Tier.T3

    return Tier.UNKNOWN
