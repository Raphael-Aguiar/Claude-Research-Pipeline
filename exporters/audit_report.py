"""Geração do relatório de auditoria — audit-report.md.

v2: inclui contagens PRISMA + critérios de seleção explícitos.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    AccessStatus,
    CompositeGrade,
    Modality,
    Reference,
    Relevance,
    SearchConfig,
    Tier,
)


def generate_audit_report(
    refs: list[Reference],
    config: SearchConfig,
    output_path: Path,
) -> None:
    """Gera relatório de auditoria em Markdown com contagens PRISMA."""
    active = [r for r in refs if not r.is_duplicate]
    total = len(refs)
    unique = len(active)
    duplicates = total - unique

    # Contadores
    tier_counts = Counter(r.tier for r in active)
    relevance_counts = Counter(r.relevance for r in active)
    grade_counts = Counter(r.grade for r in active)
    access_counts = Counter(r.access_status for r in active)
    api_counts = Counter(r.source_api for r in active)

    retracted = sum(1 for r in active if r.retracted)
    corrections = sum(1 for r in active if r.has_correction)

    # Contagens PRISMA
    excluded_pub_type = sum(
        1 for r in active if r.relevance_method == "pub_type_excluded"
    )
    excluded_off_topic_kw = sum(
        1 for r in active if r.relevance_method == "exclusion_keyword"
    )
    screened_direct = relevance_counts.get(Relevance.DIRECT, 0)
    screened_tangential = relevance_counts.get(Relevance.TANGENTIAL, 0)
    screened_off_topic = relevance_counts.get(Relevance.OFF_TOPIC, 0)
    final_gold = grade_counts.get(CompositeGrade.GOLD, 0)
    final_silver = grade_counts.get(CompositeGrade.SILVER, 0)
    final_included = final_gold + final_silver

    lines = [
        f"# Relatório de Auditoria — {config.project_name}",
        "",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Modalidade:** {config.modality.value}",
        f"**Pergunta:** {config.research_question}",
        "",
        "---",
        "",
        "## Fluxo PRISMA (simplificado)",
        "",
        "| Etapa | Registros | Detalhes |",
        "|---|---|---|",
        f"| Identificação (busca) | {total} | {', '.join(f'{api}: {c}' for api, c in api_counts.most_common())} |",
        f"| Após deduplicação | {unique} | {duplicates} duplicatas removidas |",
        f"| Excluídos por pub_type | {excluded_pub_type} | editoriais, cartas, comentários |",
        f"| Excluídos por off-topic keywords | {excluded_off_topic_kw} | termos de exclusão do scope |",
        f"| Triados por co-ocorrência | {screened_direct + screened_tangential} | match em keyword blocks |",
        f"| Classificados DIRECT | {screened_direct} | match em 2+ blocos + mapeamento a eixo |",
        f"| Classificados TANGENTIAL | {screened_tangential} | match parcial |",
        f"| **Selecionados (Gold+Silver)** | **{final_included}** | Gold: {final_gold}, Silver: {final_silver} |",
        "",
        "## Critérios de Seleção",
        "",
    ]

    if config.inclusion_criteria:
        lines.append("**Inclusão:**")
        for c in config.inclusion_criteria:
            lines.append(f"- {c}")
        lines.append("")

    if config.exclusion_criteria:
        lines.append("**Exclusão:**")
        for c in config.exclusion_criteria:
            lines.append(f"- {c}")
        lines.append("")

    # Pendências de verificação humana (zero-trust: nunca silenciosas)
    pending = [
        (r, r.verification_pendencies())
        for r in active
        if r.grade != CompositeGrade.DISCARD
    ]
    pending = [(r, p) for r, p in pending if p]
    if pending:
        lines.extend([
            "## ⚠ Pendências de verificação humana",
            "",
            "Referências abaixo têm verificação incompleta e estão LIMITADAS a "
            "BRONZE até resolução manual. Não citar antes de resolver.",
            "",
            "| Ref | Título | DOI | Pendência |",
            "|---|---|---|---|",
        ])
        for r, pend in pending:
            # DOI nunca truncado (regra absoluta — coluna própria)
            lines.append(
                f"| {r.id} | {r.title[:60]} | {r.doi or '—'} | "
                f"{'; '.join(pend)} |"
            )
        lines.append("")

    lines.extend([
        f"**Bases consultadas:** {', '.join(config.apis)}",
        f"**Período:** {config.year_range[0]}–{config.year_range[1]}",
        f"**Idiomas:** {', '.join(config.languages)}",
        "",
        "---",
        "",
        "## Resumo Executivo",
        "",
        f"| Métrica | Valor |",
        f"|---|---|",
        f"| Total de referências coletadas | {total} |",
        f"| Duplicatas removidas | {duplicates} |",
        f"| Referências únicas | {unique} |",
        f"| Retratações encontradas | {retracted} |",
        f"| Correções/errata | {corrections} |",
        "",
        "## Classificação por Grade",
        "",
        "| Grade | Qtd | % |",
        "|---|---|---|",
    ])

    for grade in (CompositeGrade.GOLD, CompositeGrade.SILVER, CompositeGrade.BRONZE,
                  CompositeGrade.DISCARD, CompositeGrade.UNGRADED):
        count = grade_counts.get(grade, 0)
        pct = (count / unique * 100) if unique else 0
        lines.append(f"| {grade.value.upper()} | {count} | {pct:.1f}% |")

    lines.extend([
        "",
        "## Classificação por Tier",
        "",
        "| Tier | Descrição | Qtd | % |",
        "|---|---|---|---|",
    ])

    tier_desc = {
        Tier.T1: "Periódico revisado por pares",
        Tier.T2: "Fonte institucional",
        Tier.T3: "Relatório técnico",
        Tier.T4: "Blog/notícia/informal",
        Tier.UNKNOWN: "Não classificado",
    }
    for tier in (Tier.T1, Tier.T2, Tier.T3, Tier.T4, Tier.UNKNOWN):
        count = tier_counts.get(tier, 0)
        pct = (count / unique * 100) if unique else 0
        lines.append(f"| {tier.name} | {tier_desc[tier]} | {count} | {pct:.1f}% |")

    # Alerta de T1
    t1_pct = (tier_counts.get(Tier.T1, 0) / unique * 100) if unique else 0
    _alert_thresholds = {
        Modality.PESQUISA_BASE: 50,
        Modality.REVISAO_INTEGRATIVA: 60,
        Modality.REVISAO_SISTEMATICA: 70,
        Modality.REVISAO_ESCOPO: 0,
    }
    threshold = _alert_thresholds.get(config.modality, 50)
    if threshold and t1_pct < threshold:
        lines.extend([
            "",
            f"> **ALERTA:** T1 está em {t1_pct:.0f}%, abaixo do "
            f"recomendado ({threshold}%) para {config.modality.value}. "
            f"Considere buscar mais artigos em bases indexadas.",
        ])

    lines.extend([
        "",
        "## Relevância",
        "",
        "| Score | Qtd | % |",
        "|---|---|---|",
    ])
    for rel in (Relevance.DIRECT, Relevance.TANGENTIAL, Relevance.OFF_TOPIC):
        count = relevance_counts.get(rel, 0)
        pct = (count / unique * 100) if unique else 0
        label = {
            Relevance.DIRECT: "DIRECT (2)",
            Relevance.TANGENTIAL: "TANGENTIAL (1)",
            Relevance.OFF_TOPIC: "OFF_TOPIC (0)",
        }[rel]
        lines.append(f"| {label} | {count} | {pct:.1f}% |")

    lines.extend([
        "",
        "## Acesso",
        "",
        "| Status | Qtd | % |",
        "|---|---|---|",
    ])
    for status in AccessStatus:
        count = access_counts.get(status, 0)
        pct = (count / unique * 100) if unique else 0
        lines.append(f"| {status.value} | {count} | {pct:.1f}% |")

    lines.extend([
        "",
        "## Fontes (APIs)",
        "",
        "| API | Qtd |",
        "|---|---|",
    ])
    for api, count in api_counts.most_common():
        lines.append(f"| {api} | {count} |")

    # Referências descartadas
    discarded = [r for r in active if r.grade == CompositeGrade.DISCARD]
    if discarded:
        lines.extend([
            "",
            "## Referências Descartadas",
            "",
            "| # | Título | Motivo |",
            "|---|---|---|",
        ])
        for i, ref in enumerate(discarded[:50], 1):
            title = ref.title[:60] + "..." if len(ref.title) > 60 else ref.title
            reason = _discard_reason(ref)
            lines.append(f"| {i} | {title} | {reason} |")
        if len(discarded) > 50:
            lines.append(f"| ... | *{len(discarded) - 50} adicionais* | |")

    # Referências Gold
    gold = [r for r in active if r.grade == CompositeGrade.GOLD]
    if gold:
        # Ordenar por score
        gold.sort(key=lambda r: r.relevance_score, reverse=True)
        lines.extend([
            "",
            "## Referências Gold",
            "",
            "| # | Autores | Ano | Título | DOI | Tier | Score | Eixos |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for i, ref in enumerate(gold, 1):
            authors = ref.authors[0] if ref.authors else "?"
            if len(ref.authors) > 1:
                authors += " et al."
            title = ref.title[:50] + "..." if len(ref.title) > 50 else ref.title
            doi = ref.doi or "-"
            axes = ", ".join(ref.mapped_axes[:2]) if ref.mapped_axes else "-"
            lines.append(
                f"| {i} | {authors} | {ref.year or '?'} | {title} | "
                f"{doi} | {ref.tier.name} | {ref.relevance_score:.1f} | {axes} |"
            )

    lines.extend([
        "",
        "---",
        "",
        f"*Gerado pelo Pipeline de Pesquisa Acadêmica v2.0*",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _discard_reason(ref: Reference) -> str:
    """Determina motivo do descarte."""
    if ref.retracted:
        return "Retratado"
    if ref.relevance_method == "pub_type_excluded":
        return f"Pub type excluído ({ref.pub_type})"
    if ref.relevance_method == "exclusion_keyword":
        return "Exclusion keyword"
    if ref.relevance == Relevance.OFF_TOPIC:
        return "Off-topic"
    if ref.tier == Tier.T4:
        return "Fonte informal (T4)"
    if ref.access_status == AccessStatus.BROKEN:
        return "URL quebrada"
    return "Critério composto"
