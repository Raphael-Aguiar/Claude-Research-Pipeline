"""Etapa 9b — Research Brief por eixo.

Organiza referências selecionadas por eixo de pesquisa com
metadados + abstract. Campo "Resumo Claude" fica vazio para
preenchimento posterior via /enrich-refs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_pipeline_dir
from ..models import CompositeGrade, Reference, Relevance, SearchConfig


def generate_brief(
    refs: list[Reference],
    config: SearchConfig,
) -> Path:
    """Gera research-brief.md organizado por eixo de pesquisa.

    Returns:
        Caminho do arquivo gerado.
    """
    pipeline_dir = get_pipeline_dir(config.project_name)

    # Filtrar refs finais (Gold + Silver, não-duplicadas)
    final_refs = [
        r for r in refs
        if not r.is_duplicate
        and r.grade in (CompositeGrade.GOLD, CompositeGrade.SILVER)
        and r.relevance != Relevance.OFF_TOPIC
    ]

    # Ordenar por score
    final_refs.sort(key=lambda r: r.relevance_score, reverse=True)

    # Organizar por eixo
    by_axis: dict[str, list[Reference]] = defaultdict(list)
    unmapped: list[Reference] = []

    for ref in final_refs:
        if ref.mapped_axes:
            for axis in ref.mapped_axes:
                by_axis[axis].append(ref)
        else:
            unmapped.append(ref)

    # Critérios de seleção para documentação
    criteria = _format_criteria(config)

    lines = [
        f"# Research Brief — {config.project_name}",
        "",
        f"**Pergunta:** {config.research_question}",
        f"**Referências selecionadas:** {len(final_refs)} (de {sum(1 for r in refs if not r.is_duplicate)} coletadas)",
        f"**Critérios:** {criteria}",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        "---",
        "",
    ]

    # Seção por eixo
    ref_counter = 1
    for axis_name in (a.name for a in config.research_axes):
        axis_refs = by_axis.get(axis_name, [])
        lines.append(f"## {axis_name}")
        lines.append(f"*{len(axis_refs)} referências mapeadas*")
        lines.append("")

        for ref in axis_refs:
            lines.extend(_format_brief_entry(ref, ref_counter))
            ref_counter += 1

        if not axis_refs:
            lines.append("*Nenhuma referência mapeada para este eixo — considerar busca complementar.*")
            lines.append("")

    # Refs sem eixo mapeado
    if unmapped:
        lines.append("## Sem eixo mapeado")
        lines.append(f"*{len(unmapped)} referências*")
        lines.append("")
        for ref in unmapped:
            lines.extend(_format_brief_entry(ref, ref_counter))
            ref_counter += 1

    lines.extend([
        "---",
        "",
        "*Research brief gerado pelo Pipeline de Pesquisa Acadêmica v2.0*",
        "*Resumos Claude: executar `/enrich-refs` para preencher*",
    ])

    brief_path = pipeline_dir / "research-brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → Research brief: {brief_path}")

    return brief_path


def _format_brief_entry(ref: Reference, num: int) -> list[str]:
    """Formata uma entrada do brief."""
    # Autores formatados
    if ref.authors:
        first_author = ref.authors[0].split()[0] if ref.authors[0] else "?"
        if len(ref.authors) > 1:
            authors_short = f"{first_author} et al."
        else:
            authors_short = first_author
    else:
        authors_short = "?"

    # DOI
    doi_str = f"10.{ref.doi.split('10.')[-1]}" if ref.doi and "10." in ref.doi else ref.doi or "-"

    # Citações
    citations = str(ref.citation_count) if ref.citation_count else "-"

    # Acesso
    access = "Open Access" if ref.is_open_access else ref.access_status.value

    # Filename do MD
    import re
    year = str(ref.year) if ref.year else "XXXX"
    author = re.sub(r'[^\w]', '', ref.authors[0].split()[0]) if ref.authors else "Unknown"
    title_words = re.sub(r'[^\w\s]', '', ref.title or "").split()
    stop_words = {"the", "a", "an", "of", "in", "for", "and", "or", "to", "on", "with", "by"}
    significant = [w for w in title_words if w.lower() not in stop_words][:5]
    title_short = "_".join(w.capitalize() for w in significant) if significant else "Untitled"
    md_filename = f"{year}_{author}_{title_short}.md"

    lines = [
        f"### [{num}] {authors_short} ({ref.year or '?'}) — {ref.title}",
        f"- **Journal:** {ref.journal or '?'} | **Grade:** {ref.grade.value.upper()} | **DOI:** {doi_str}",
        f"- **Acesso:** {access} | **Citações:** {citations}",
        "- **Resumo Claude:** *[a ser gerado via /enrich-refs]*",
        f"- **MD completo:** refs/{md_filename}",
        "",
    ]

    return lines


def _format_criteria(config: SearchConfig) -> str:
    """Formata resumo dos critérios de seleção."""
    parts = []
    if config.inclusion_criteria:
        parts.append("; ".join(config.inclusion_criteria[:3]))
    if config.exclusion_criteria:
        parts.append(f"Excluídos: {'; '.join(config.exclusion_criteria[:3])}")
    return " | ".join(parts) if parts else "Ver scope.yaml"
