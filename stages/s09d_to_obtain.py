"""Etapa 9d — Lista de artigos não-OA para obter.

Gera to-obtain.md com artigos que precisam de acesso institucional.
Papers canônicos (alta citação) são priorizados.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import get_pipeline_dir
from ..models import AccessStatus, CompositeGrade, Reference, Relevance, SearchConfig


def generate_to_obtain(
    refs: list[Reference],
    config: SearchConfig,
) -> None:
    """Gera lista de artigos não-acessíveis para obtenção institucional."""
    pipeline_dir = get_pipeline_dir(config.project_name)

    # Refs Gold/Silver que NÃO são OA e NÃO estão acessíveis
    to_obtain = [
        r for r in refs
        if not r.is_duplicate
        and r.grade in (CompositeGrade.GOLD, CompositeGrade.SILVER)
        and r.relevance != Relevance.OFF_TOPIC
        and not r.is_open_access
        and r.access_status in (AccessStatus.RESTRICTED, AccessStatus.NO_URL)
    ]

    if not to_obtain:
        print("  → Todos os artigos selecionados são OA ou acessíveis")
        return

    # Ordenar: canônicos primeiro, depois por citações, depois por score
    to_obtain.sort(
        key=lambda r: (
            r.canonical,
            r.citation_count or 0,
            r.relevance_score,
        ),
        reverse=True,
    )

    lines = [
        f"# Artigos para Obter — {config.project_name}",
        "",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"**Total:** {len(to_obtain)} artigos não-acessíveis",
        "",
        "Estes artigos foram selecionados pelo pipeline mas requerem acesso institucional",
        "(Capes, ProBE, assinatura direta) para obter o texto completo.",
        "",
        "**Fluxo:**",
        "1. Obtenha os PDFs via acesso institucional",
        "2. Coloque em `pipeline/pdfs-manual/`",
        "3. Execute: `python -m tools extract-manual \"{}\"`".format(config.project_name),
        "",
        "---",
        "",
        "## Artigos não-acessíveis (requer acesso institucional)",
        "",
        "| # | Referência | DOI | Grade | Canônico? | Citações | Eixos |",
        "|---|---|---|---|---|---|---|",
    ]

    for i, ref in enumerate(to_obtain, 1):
        # Autores formatados
        if ref.authors:
            first = ref.authors[0].split()[0] if ref.authors[0] else "?"
            authors_short = f"{first} et al." if len(ref.authors) > 1 else first
        else:
            authors_short = "?"

        title_short = ref.title[:50] + "..." if len(ref.title) > 50 else ref.title
        doi = ref.doi or "-"
        grade = ref.grade.value.upper()
        canonical = f"Sim ({ref.citation_count} cit.)" if ref.canonical else "Não"
        citations = str(ref.citation_count) if ref.citation_count else "-"
        axes = ", ".join(ref.mapped_axes[:2]) if ref.mapped_axes else "-"

        lines.append(
            f"| {i} | {authors_short} ({ref.year or '?'}) — {title_short} | "
            f"{doi} | {grade} | {canonical} | {citations} | {axes} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "*Gerado pelo Pipeline de Pesquisa Acadêmica v2.0*",
    ])

    obtain_path = pipeline_dir / "to-obtain.md"
    obtain_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → Lista de artigos para obter: {obtain_path} ({len(to_obtain)} artigos)")
