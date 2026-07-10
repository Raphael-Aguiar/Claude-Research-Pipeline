"""Fluxograma PRISMA canônico (PRISMA 2020 / PRISMA-ScR / PRIOR).

Gera prisma-flow.md com as contagens canônicas
(identificação → triagem → elegibilidade → incluídos) por base,
mais um diagrama Mermaid renderizável no Obsidian.

Referências normativas:
- PRISMA 2020 (Page et al. 2021) — revisão sistemática/integrativa
- PRISMA-ScR (Tricco et al. 2018) — revisão de escopo
- PRIOR (Gates et al. 2022) — overview of reviews / meta-revisão
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
)

_STANDARD_BY_MODALITY = {
    Modality.REVISAO_SISTEMATICA: "PRISMA 2020 (Page et al. 2021)",
    Modality.REVISAO_INTEGRATIVA: "PRISMA 2020 adaptado (revisão integrativa)",
    Modality.REVISAO_ESCOPO: "PRISMA-ScR (Tricco et al. 2018)",
    Modality.META_REVISAO: "PRIOR (Gates et al. 2022) + PRISMA 2020",
}

_EXCLUSION_LABELS = {
    "pub_type_excluded": "Tipo de publicação (editorial/carta/comentário)",
    "exclusion_keyword": "Termo de exclusão do escopo",
    "nao_e_revisao": "Não é revisão (critério da meta-revisão)",
    "no_match": "Sem correspondência com os blocos de conceito",
    "no_text": "Sem título/abstract avaliável",
}


def generate_prisma_flow(
    refs: list[Reference],
    config: SearchConfig,
    output_path: Path,
) -> dict:
    """Gera o prisma-flow.md e retorna as contagens calculadas."""
    standard = _STANDARD_BY_MODALITY.get(
        config.modality, "PRISMA 2020 (simplificado para pesquisa-base)"
    )

    # --- Identificação ---
    total_identified = len(refs)
    per_db = Counter(r.source_api or "seed/manual" for r in refs)
    duplicates = sum(1 for r in refs if r.is_duplicate)
    unique = [r for r in refs if not r.is_duplicate]

    # --- Triagem (título/abstract) ---
    screened = len(unique)
    excluded_screening = [r for r in unique if r.relevance == Relevance.OFF_TOPIC]
    exclusion_reasons = Counter()
    for r in excluded_screening:
        base_method = r.relevance_method.split("|")[0]
        label = _EXCLUSION_LABELS.get(base_method, base_method or "não classificada")
        exclusion_reasons[label] += 1

    # --- Recuperação ---
    candidates = [r for r in unique if r.relevance != Relevance.OFF_TOPIC]
    not_retrieved = [r for r in candidates if r.access_status == AccessStatus.BROKEN]
    to_obtain = [r for r in candidates if r.access_status == AccessStatus.RESTRICTED]

    # --- Elegibilidade ---
    assessed = [r for r in candidates if r not in not_retrieved]
    retracted = [r for r in assessed if r.retracted is True]
    divergent = [r for r in assessed if "llm_divergent" in (r.relevance_method or "")]

    # --- Incluídos ---
    included_grades = {CompositeGrade.GOLD, CompositeGrade.SILVER}
    if config.modality != Modality.PESQUISA_BASE:
        included_grades.add(CompositeGrade.BRONZE)
    included = [r for r in assessed if r.grade in included_grades]
    grade_counts = Counter(r.grade.value for r in included)

    counts = {
        "identified": total_identified,
        "duplicates": duplicates,
        "screened": screened,
        "excluded_screening": len(excluded_screening),
        "not_retrieved": len(not_retrieved),
        "assessed": len(assessed),
        "retracted": len(retracted),
        "included": len(included),
    }

    db_lines = "<br/>".join(
        f"{db}: {n}" for db, n in per_db.most_common()
    )

    mermaid = f"""```mermaid
flowchart TD
    A["IDENTIFICAÇÃO<br/>Registros identificados: {total_identified}<br/>{db_lines}"]
    A --> B["Duplicatas removidas: {duplicates}"]
    B --> C["TRIAGEM<br/>Registros triados (título/abstract): {screened}"]
    C --> D["Registros excluídos na triagem: {len(excluded_screening)}"]
    C --> E["Relatórios buscados para recuperação: {len(candidates)}"]
    E --> F["Não recuperados (link quebrado): {len(not_retrieved)}"]
    E --> G["ELEGIBILIDADE<br/>Avaliados para elegibilidade: {len(assessed)}"]
    G --> H["Excluídos: retratados: {len(retracted)}<br/>divergências em revisão humana: {len(divergent)}"]
    G --> I["INCLUÍDOS<br/>Estudos incluídos na revisão: {len(included)}"]
```"""

    lines = [
        f"# Fluxo PRISMA — {config.project_name}",
        "",
        f"**Norma de relato:** {standard}",
        f"**Modalidade:** {config.modality.value}",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        mermaid,
        "",
        "## Identificação",
        "",
        "| Base | Registros |",
        "|---|---|",
    ]
    for db, n in per_db.most_common():
        lines.append(f"| {db} | {n} |")
    lines += [
        f"| **Total identificado** | **{total_identified}** |",
        f"| Duplicatas removidas | {duplicates} |",
        "",
        "## Triagem (título/abstract)",
        "",
        f"- Registros triados: **{screened}**",
        f"- Excluídos na triagem: **{len(excluded_screening)}**, por razão:",
        "",
        "| Razão de exclusão | n |",
        "|---|---|",
    ]
    for label, n in exclusion_reasons.most_common():
        lines.append(f"| {label} | {n} |")
    lines += [
        "",
        "## Recuperação e elegibilidade",
        "",
        f"- Relatórios buscados para recuperação: **{len(candidates)}**",
        f"- Não recuperados (URL/DOI quebrado): **{len(not_retrieved)}**",
        f"- Acesso restrito (obter via CAPES/ProBE — ver to-obtain.md): {len(to_obtain)}",
        f"- Avaliados para elegibilidade: **{len(assessed)}**",
        f"- Excluídos por retratação: **{len(retracted)}**",
        f"- Divergências de triagem pendentes de decisão humana: **{len(divergent)}** "
        "(ver screening-report.md — não descartadas)",
        "",
        "## Incluídos",
        "",
        f"- Estudos incluídos: **{len(included)}**"
        + (f" ({', '.join(f'{g}: {n}' for g, n in sorted(grade_counts.items()))})"
           if grade_counts else ""),
    ]
    if config.quality_framework:
        lines += [
            "",
            f"**Avaliação de qualidade prevista:** {config.quality_framework} "
            "(etapa manual — registrar por estudo incluído)",
        ]
    lines += [
        "",
        "---",
        "",
        "> Contagens geradas automaticamente pelo pipeline "
        "(`python -m tools prisma`). Para publicação, transpor para o "
        "diagrama oficial PRISMA 2020 (https://www.prisma-statement.org).",
    ]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  prisma-flow.md salvo: {output_path}")
    return counts
