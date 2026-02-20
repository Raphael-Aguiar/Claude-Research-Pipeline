"""Etapa 8b — Extração de conteúdo para MDs individuais.

Estratégia por prioridade:
1. Europe PMC fullTextXML (PMCID) → parse XML para MD
2. OA PDF (oa_url) → Docling → Markdown estruturado
3. OA HTML (landing page) → requests + beautifulsoup + markdownify
4. Paywall / sem OA → apenas abstract
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests

from ..config import DEFAULT_TIMEOUT, get_pipeline_dir
from ..models import AccessStatus, CompositeGrade, Reference, Relevance, SearchConfig

_PMC_XML_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"


def extract_references(
    refs: list[Reference],
    config: SearchConfig,
) -> list[Reference]:
    """Extrai conteúdo das referências selecionadas e gera MDs individuais.

    Processa apenas refs Gold + Silver, não-duplicadas.
    """
    pipeline_dir = get_pipeline_dir(config.project_name)
    refs_dir = pipeline_dir / "refs"
    refs_dir.mkdir(exist_ok=True)

    candidates = [
        r for r in refs
        if not r.is_duplicate
        and r.grade in (CompositeGrade.GOLD, CompositeGrade.SILVER)
        and r.relevance != Relevance.OFF_TOPIC
    ]

    print(f"\n  Extraindo conteúdo de {len(candidates)} referências...")

    extracted = 0
    abstract_only = 0

    for i, ref in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] {ref.title[:60]}...")
        content, method = _extract_content(ref)

        md_text = _format_md(ref, content, method)
        filename = _generate_filename(ref)
        md_path = refs_dir / filename

        md_path.write_text(md_text, encoding="utf-8")

        if method == "abstract_only":
            abstract_only += 1
        else:
            extracted += 1

        time.sleep(0.2)  # Rate limiting

    print(f"  → {extracted} com conteúdo completo, {abstract_only} apenas abstract")
    print(f"  → MDs salvos em: {refs_dir}")

    return refs


def _extract_content(ref: Reference) -> tuple[str, str]:
    """Extrai conteúdo da referência pela melhor fonte disponível.

    Returns:
        (conteúdo, método): o conteúdo extraído e o método usado.
    """
    # Prioridade 1: Europe PMC XML (se tem PMCID via URL)
    pmcid = _get_pmcid(ref)
    if pmcid:
        content = _extract_pmc_xml(pmcid)
        if content:
            return content, "pmc_xml"

    # Prioridade 2: PDF via Docling (se OA com PDF)
    if ref.oa_url and ref.oa_url.endswith(".pdf"):
        content = _extract_pdf_docling(ref.oa_url)
        if content:
            return content, "docling_pdf"

    # Prioridade 3: HTML (se OA com landing page)
    if ref.is_open_access and ref.url:
        content = _extract_html(ref.url)
        if content:
            return content, "html"

    # Prioridade 4: Apenas abstract
    if ref.abstract:
        return "", "abstract_only"

    return "", "no_content"


def _get_pmcid(ref: Reference) -> str | None:
    """Extrai PMCID da referência (da URL ou campo openalex_id)."""
    if ref.url and "/PMC/" in ref.url:
        match = re.search(r"PMC(\d+)", ref.url)
        if match:
            return f"PMC{match.group(1)}"
    return None


def _extract_pmc_xml(pmcid: str) -> str | None:
    """Extrai full text de Europe PMC via XML API."""
    try:
        url = _PMC_XML_URL.format(pmcid=pmcid)
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return None

        # Parse XML básico para extrair seções
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.content)

        sections = []
        for sec in root.iter("sec"):
            title_elem = sec.find("title")
            title = title_elem.text if title_elem is not None else ""
            paragraphs = []
            for p in sec.findall("p"):
                text = "".join(p.itertext())
                if text.strip():
                    paragraphs.append(text.strip())
            if paragraphs:
                if title:
                    sections.append(f"### {title}\n\n" + "\n\n".join(paragraphs))
                else:
                    sections.append("\n\n".join(paragraphs))

        return "\n\n".join(sections) if sections else None

    except Exception:
        return None


def _extract_pdf_docling(pdf_url: str) -> str | None:
    """Extrai texto de PDF via Docling (IBM)."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        print("    Docling não instalado (pip install docling), pulando PDF")
        return None

    try:
        converter = DocumentConverter()
        result = converter.convert(pdf_url)
        md_content = result.document.export_to_markdown()
        return md_content if md_content and len(md_content) > 100 else None
    except Exception as e:
        print(f"    Docling falhou: {e}")
        return None


def _extract_html(url: str) -> str | None:
    """Extrai conteúdo de página HTML via beautifulsoup + markdownify."""
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify
    except ImportError:
        print("    beautifulsoup4/markdownify não instalados, pulando HTML")
        return None

    try:
        resp = requests.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remover scripts, styles, nav
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Tentar encontrar o conteúdo do artigo
        article = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"article|content|paper"))
            or soup.find("main")
            or soup.body
        )

        if not article:
            return None

        md = markdownify(str(article), heading_style="ATX")
        # Limpar markdown excessivo
        md = re.sub(r'\n{3,}', '\n\n', md)

        return md if md and len(md) > 200 else None

    except Exception:
        return None


def _format_md(ref: Reference, content: str, method: str) -> str:
    """Formata o MD individual da referência."""
    # Autores formatados
    if ref.authors:
        if len(ref.authors) <= 6:
            authors_str = ", ".join(ref.authors)
        else:
            authors_str = ", ".join(ref.authors[:6]) + " et al."
    else:
        authors_str = "?"

    # DOI link
    doi_link = f"https://doi.org/{ref.doi}" if ref.doi else ref.url or ""

    # Eixos
    axes_str = ", ".join(ref.mapped_axes) if ref.mapped_axes else "-"

    # Acesso
    access_str = "Open Access" if ref.is_open_access else ref.access_status.value

    # Citation count
    citations_str = str(ref.citation_count) if ref.citation_count else "-"

    lines = [
        f"# {ref.title}",
        "",
        f"**Link:** {doi_link}",
        f"**Autores:** {authors_str} | **Ano:** {ref.year or '?'} | **Journal:** {ref.journal or '?'}",
        f"**Grade:** {ref.grade.value.upper()} | **Tier:** {ref.tier.name} | **Citações:** {citations_str}",
        f"**Eixos:** {axes_str}",
        f"**Acesso:** {access_str} | **PMID:** {ref.pmid or '-'}",
        "",
        "## Resumo Claude",
        "",
        "*[A ser gerado via /enrich-refs]*",
        "",
        "---",
        "",
        "## Abstract",
        "",
        ref.abstract or "*Abstract não disponível*",
        "",
    ]

    if content:
        lines.extend([
            "## Conteúdo",
            "",
            f"*Extraído via {method}*",
            "",
            content,
        ])
    else:
        lines.extend([
            "## Conteúdo",
            "",
            "*Conteúdo completo não disponível — ver to-obtain.md*",
        ])

    return "\n".join(lines)


def _generate_filename(ref: Reference) -> str:
    """Gera nome de arquivo para o MD da referência."""
    # Formato: YYYY_PrimeiroAutor_TítuloAbreviado.md
    year = str(ref.year) if ref.year else "XXXX"

    author = "Unknown"
    if ref.authors:
        # Pegar sobrenome do primeiro autor
        first = ref.authors[0]
        parts = first.split()
        author = parts[0] if parts else "Unknown"
        # Remover caracteres especiais
        author = re.sub(r'[^\w]', '', author)

    # Título abreviado (primeiras 4-5 palavras significativas)
    title_words = re.sub(r'[^\w\s]', '', ref.title or "Untitled").split()
    stop_words = {"the", "a", "an", "of", "in", "for", "and", "or", "to", "on", "with", "by"}
    significant = [w for w in title_words if w.lower() not in stop_words][:5]
    title_short = "_".join(w.capitalize() for w in significant) if significant else "Untitled"

    filename = f"{year}_{author}_{title_short}.md"
    # Sanitizar
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

    return filename
