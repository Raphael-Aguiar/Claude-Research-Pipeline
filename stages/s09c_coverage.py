"""Etapa 9c — Relatório de cobertura por eixo.

Verifica se cada eixo de pesquisa tem referências suficientes.
Sugere busca complementar para eixos com lacunas.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from ..config import get_pipeline_dir
from ..models import CompositeGrade, Reference, Relevance, SearchConfig


def generate_coverage_report(
    refs: list[Reference],
    config: SearchConfig,
) -> None:
    """Gera relatório de cobertura por eixo de pesquisa."""
    pipeline_dir = get_pipeline_dir(config.project_name)

    # Refs finais
    final_refs = [
        r for r in refs
        if not r.is_duplicate
        and r.grade in (CompositeGrade.GOLD, CompositeGrade.SILVER)
        and r.relevance != Relevance.OFF_TOPIC
    ]

    # Contagem por eixo
    axis_counts: dict[str, int] = defaultdict(int)
    axis_gold: dict[str, int] = defaultdict(int)
    for ref in final_refs:
        for axis in ref.mapped_axes:
            axis_counts[axis] += 1
            if ref.grade == CompositeGrade.GOLD:
                axis_gold[axis] += 1

    # Meta: para pesquisa-base, pelo menos 3 refs por eixo
    min_per_axis = 3

    lines = [
        f"# Relatório de Cobertura — {config.project_name}",
        "",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"**Total de referências selecionadas:** {len(final_refs)}",
        f"**Eixos de pesquisa:** {len(config.research_axes)}",
        "",
        "## Cobertura por Eixo",
        "",
        "| Eixo | Total | Gold | Silver | Status |",
        "|---|---|---|---|---|",
    ]

    gaps = []
    for axis in config.research_axes:
        total = axis_counts.get(axis.name, 0)
        gold = axis_gold.get(axis.name, 0)
        silver = total - gold
        if total >= min_per_axis:
            status = "✓ Adequado"
        elif total > 0:
            status = "⚠ Insuficiente"
            gaps.append(axis)
        else:
            status = "✗ Sem cobertura"
            gaps.append(axis)

        lines.append(f"| {axis.name} | {total} | {gold} | {silver} | {status} |")

    # Refs sem eixo
    unmapped = sum(1 for r in final_refs if not r.mapped_axes)
    if unmapped:
        lines.append(f"| *Sem eixo mapeado* | {unmapped} | - | - | ⚠ Revisar |")

    # Sugestões para lacunas
    if gaps:
        lines.extend([
            "",
            "## Sugestões para Lacunas",
            "",
        ])
        for axis in gaps:
            kw = ", ".join(axis.keywords[:5])
            syn = ", ".join(axis.synonyms[:3]) if axis.synonyms else "-"
            lines.extend([
                f"### {axis.name}",
                f"- **Keywords:** {kw}",
                f"- **Sinônimos:** {syn}",
                f"- **Sugestão:** Buscar em PubMed/OpenAlex com termos: `{' OR '.join(axis.keywords[:3])}`",
                f"- **Alternativa:** Adicionar termos ao scope.yaml e re-rodar `python -m tools run \"{config.project_name}\" --from-stage 2`",
                "",
            ])
    else:
        lines.extend([
            "",
            "✓ **Todos os eixos têm cobertura adequada.**",
        ])

    lines.extend([
        "",
        "---",
        "",
        "*Gerado pelo Pipeline de Pesquisa Acadêmica v2.0*",
    ])

    report_path = pipeline_dir / "coverage-report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → Relatório de cobertura: {report_path}")
