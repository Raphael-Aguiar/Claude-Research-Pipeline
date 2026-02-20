"""Etapa 8c — Extração de PDFs manuais.

Processa PDFs colocados pelo autor em pipeline/pdfs-manual/
e atualiza os MDs correspondentes em pipeline/refs/.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import get_pipeline_dir


def extract_manual_pdfs(project_name: str) -> int:
    """Processa PDFs manuais e atualiza MDs.

    O autor obtém PDFs de artigos não-OA via acesso institucional
    e os coloca em pipeline/pdfs-manual/. Esta função:
    1. Lista PDFs em pdfs-manual/
    2. Extrai conteúdo com Docling
    3. Tenta match com MDs existentes em refs/
    4. Atualiza o MD com o conteúdo extraído

    Returns:
        Número de PDFs processados com sucesso.
    """
    pipeline_dir = get_pipeline_dir(project_name)
    pdfs_dir = pipeline_dir / "pdfs-manual"
    refs_dir = pipeline_dir / "refs"

    if not pdfs_dir.exists():
        print(f"  Pasta pdfs-manual/ não encontrada: {pdfs_dir}")
        print(f"  Crie a pasta e coloque os PDFs antes de executar.")
        return 0

    pdf_files = list(pdfs_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"  Nenhum PDF encontrado em {pdfs_dir}")
        return 0

    if not refs_dir.exists():
        print(f"  Pasta refs/ não encontrada. Execute a extração primeiro.")
        return 0

    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        print("  Docling não instalado (pip install docling)")
        return 0

    converter = DocumentConverter()
    processed = 0

    for pdf_path in pdf_files:
        print(f"  Processando: {pdf_path.name}")

        try:
            result = converter.convert(str(pdf_path))
            md_content = result.document.export_to_markdown()

            if not md_content or len(md_content) < 100:
                print(f"    → Conteúdo insuficiente, pulando")
                continue

            # Tentar match com MD existente
            md_file = _find_matching_md(pdf_path.stem, refs_dir)

            if md_file:
                _update_md_with_content(md_file, md_content)
                print(f"    → MD atualizado: {md_file.name}")
                processed += 1
            else:
                # Criar novo MD com conteúdo bruto
                new_md = refs_dir / f"{pdf_path.stem}.md"
                new_md.write_text(
                    f"# {pdf_path.stem}\n\n"
                    f"*Extraído manualmente via Docling*\n\n"
                    f"---\n\n{md_content}",
                    encoding="utf-8",
                )
                print(f"    → Novo MD criado: {new_md.name}")
                processed += 1

        except Exception as e:
            print(f"    → Erro: {e}")

    print(f"\n  → {processed}/{len(pdf_files)} PDFs processados")
    return processed


def _find_matching_md(pdf_stem: str, refs_dir: Path) -> Path | None:
    """Tenta encontrar um MD que corresponda ao PDF.

    Estratégia: comparar o nome do PDF (normalizado) com nomes dos MDs.
    """
    pdf_norm = _normalize_name(pdf_stem)

    best_match = None
    best_score = 0

    for md_file in refs_dir.glob("*.md"):
        md_norm = _normalize_name(md_file.stem)

        # Verificar se compartilham palavras significativas
        pdf_words = set(pdf_norm.split())
        md_words = set(md_norm.split())
        common = pdf_words & md_words

        if len(common) >= 2:
            score = len(common) / max(len(pdf_words), len(md_words))
            if score > best_score:
                best_score = score
                best_match = md_file

    return best_match if best_score >= 0.3 else None


def _normalize_name(name: str) -> str:
    """Normaliza nome de arquivo para comparação."""
    name = name.lower()
    name = re.sub(r'[_\-\.]', ' ', name)
    name = re.sub(r'\d{4}', '', name)  # Remover anos
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _update_md_with_content(md_path: Path, content: str) -> None:
    """Atualiza um MD existente com conteúdo extraído do PDF."""
    existing = md_path.read_text(encoding="utf-8")

    # Substituir a seção de conteúdo
    marker = "## Conteúdo"
    if marker in existing:
        before = existing[:existing.index(marker)]
        new_content = (
            f"{marker}\n\n"
            f"*Extraído manualmente via Docling*\n\n"
            f"{content}"
        )
        md_path.write_text(before + new_content, encoding="utf-8")
    else:
        # Adicionar ao final
        md_path.write_text(
            existing + f"\n\n{marker}\n\n"
            f"*Extraído manualmente via Docling*\n\n"
            f"{content}",
            encoding="utf-8",
        )
