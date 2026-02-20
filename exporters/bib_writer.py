"""Exportação BibTeX — Reference → .bib."""

from __future__ import annotations

import re
from pathlib import Path

from ..models import CompositeGrade, Reference


def write_bib(
    refs: list[Reference],
    output_path: Path,
    include_grades: tuple[CompositeGrade, ...] = (
        CompositeGrade.GOLD,
        CompositeGrade.SILVER,
    ),
) -> int:
    """Gera arquivo .bib a partir das referências.

    Args:
        refs: Lista de referências.
        output_path: Caminho do arquivo .bib.
        include_grades: Grades a incluir no .bib.

    Returns:
        Número de entradas escritas.
    """
    selected = [
        r for r in refs
        if not r.is_duplicate and r.grade in include_grades
    ]

    entries = []
    for ref in selected:
        entry = _reference_to_bibtex(ref)
        if entry:
            entries.append(entry)

    content = "\n\n".join(entries)
    output_path.write_text(content, encoding="utf-8")
    return len(entries)


def _reference_to_bibtex(ref: Reference) -> str:
    """Converte uma Reference em entrada BibTeX."""
    # Determinar tipo de entrada
    entry_type = _get_entry_type(ref)

    # Gerar cite key
    cite_key = _generate_cite_key(ref)

    # Montar campos
    fields: list[str] = []

    if ref.title:
        fields.append(f"  title = {{{ref.title}}}")
    if ref.authors:
        authors_str = " and ".join(ref.authors)
        fields.append(f"  author = {{{authors_str}}}")
    if ref.year:
        fields.append(f"  year = {{{ref.year}}}")
    if ref.journal:
        fields.append(f"  journal = {{{ref.journal}}}")
    if ref.volume:
        fields.append(f"  volume = {{{ref.volume}}}")
    if ref.issue:
        fields.append(f"  number = {{{ref.issue}}}")
    if ref.pages:
        fields.append(f"  pages = {{{ref.pages}}}")
    if ref.doi:
        fields.append(f"  doi = {{{ref.doi}}}")
    if ref.pmid:
        fields.append(f"  pmid = {{{ref.pmid}}}")
    if ref.url:
        fields.append(f"  url = {{{ref.url}}}")
    if ref.abstract:
        # Truncar abstract longo
        abstract = ref.abstract[:500]
        abstract = abstract.replace("{", "\\{").replace("}", "\\}")
        fields.append(f"  abstract = {{{abstract}}}")

    fields_str = ",\n".join(fields)
    return f"@{entry_type}{{{cite_key},\n{fields_str}\n}}"


def _get_entry_type(ref: Reference) -> str:
    """Determina o tipo de entrada BibTeX."""
    pt = (ref.pub_type or "").lower()

    if pt in ("journal-article", "article"):
        return "article"
    if pt in ("book-chapter", "chapter"):
        return "incollection"
    if pt in ("book", "monograph"):
        return "book"
    if pt in ("proceedings-article", "conference-paper"):
        return "inproceedings"
    if pt in ("dissertation", "thesis"):
        return "phdthesis"
    if pt in ("report",):
        return "techreport"
    if pt in ("dataset",):
        return "misc"

    # Fallback: se tem journal → article
    if ref.journal:
        return "article"

    return "misc"


def _generate_cite_key(ref: Reference) -> str:
    """Gera cite key no formato SobrenomeAno."""
    # Primeiro autor
    if ref.authors:
        first = ref.authors[0]
        # Extrair sobrenome (última palavra ou antes da vírgula)
        parts = first.replace(",", " ").split()
        surname = parts[0] if parts else "Unknown"
    else:
        surname = "Unknown"

    # Limpar caracteres especiais
    surname = re.sub(r"[^a-zA-Z]", "", surname)

    year = ref.year or "nd"

    # Garantir unicidade adicionando parte do ID
    suffix = ref.id[-4:] if ref.id else ""

    return f"{surname}{year}{suffix}"
