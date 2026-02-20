"""Etapa 9 — Síntese e Outputs.

Filtra Gold+Silver, calcula grades, gera .bib, audit-report.md,
research-brief.md, coverage-report.md e to-obtain.md.
"""

from __future__ import annotations

from ..config import get_pipeline_dir
from ..exporters.audit_report import generate_audit_report
from ..exporters.bib_writer import write_bib
from ..exporters.json_export import save_refs_json
from ..models import CompositeGrade, Modality, Reference, SearchConfig
from ..stages.s09b_brief import generate_brief
from ..stages.s09c_coverage import generate_coverage_report
from ..stages.s09d_to_obtain import generate_to_obtain


def synthesize(
    refs: list[Reference],
    config: SearchConfig,
) -> list[Reference]:
    """Calcula grades compostas e gera outputs finais.

    1. Calcula grade composta para cada referência
    2. Filtra Gold+Silver (+ Bronze para revisões)
    3. Gera refs-final.json
    4. Gera refs.bib
    5. Gera audit-report.md (com contagens PRISMA)
    6. Gera research-brief.md (por eixo)
    7. Gera coverage-report.md (cobertura por eixo)
    8. Gera to-obtain.md (artigos não-OA)
    """
    print("\n  Sintetizando resultados...")

    # Passo 1: Computar grades
    for ref in refs:
        if not ref.is_duplicate:
            ref.compute_grade()

    # Estatísticas de grade
    grade_counts = {}
    for ref in refs:
        if not ref.is_duplicate:
            grade_counts[ref.grade] = grade_counts.get(ref.grade, 0) + 1

    for grade in (CompositeGrade.GOLD, CompositeGrade.SILVER,
                  CompositeGrade.BRONZE, CompositeGrade.DISCARD):
        count = grade_counts.get(grade, 0)
        print(f"  → {grade.value.upper()}: {count}")

    # Passo 2: Determinar grades a incluir por modalidade
    include_grades = _grades_for_modality(config.modality)

    # Passo 3: Filtrar refs finais (respeitando max_final_refs)
    eligible = [
        r for r in refs
        if not r.is_duplicate and r.grade in include_grades
    ]
    # Ordenar por score e limitar
    eligible.sort(key=lambda r: r.relevance_score, reverse=True)
    final_refs = eligible[:config.max_final_refs]

    print(f"\n  → {len(final_refs)} referências no output final "
          f"(de {len(eligible)} elegíveis, max={config.max_final_refs})")

    # Passo 4: Salvar outputs
    pipeline_dir = get_pipeline_dir(config.project_name)

    # refs-final.json
    final_path = pipeline_dir / "refs-final.json"
    save_refs_json(final_refs, final_path, metadata={
        "project": config.project_name,
        "modality": config.modality.value,
        "included_grades": [g.value for g in include_grades],
        "max_final_refs": config.max_final_refs,
    })
    print(f"  refs-final.json: {final_path}")

    # refs.bib
    bib_path = pipeline_dir / "refs.bib"
    bib_count = write_bib(refs, bib_path, include_grades=include_grades)
    print(f"  refs.bib: {bib_path} ({bib_count} entradas)")

    # audit-report.md (com contagens PRISMA)
    report_path = pipeline_dir / "audit-report.md"
    generate_audit_report(refs, config, report_path)
    print(f"  audit-report.md: {report_path}")

    # research-brief.md
    generate_brief(refs, config)

    # coverage-report.md
    if config.research_axes:
        generate_coverage_report(refs, config)

    # to-obtain.md
    generate_to_obtain(refs, config)

    return refs


def _grades_for_modality(modality: Modality) -> tuple[CompositeGrade, ...]:
    """Determina quais grades incluir no output por modalidade."""
    if modality == Modality.PESQUISA_BASE:
        return (CompositeGrade.GOLD, CompositeGrade.SILVER)
    elif modality in (Modality.REVISAO_SISTEMATICA, Modality.REVISAO_ESCOPO):
        return (CompositeGrade.GOLD, CompositeGrade.SILVER, CompositeGrade.BRONZE)
    elif modality == Modality.REVISAO_INTEGRATIVA:
        return (CompositeGrade.GOLD, CompositeGrade.SILVER, CompositeGrade.BRONZE)
    return (CompositeGrade.GOLD, CompositeGrade.SILVER)
