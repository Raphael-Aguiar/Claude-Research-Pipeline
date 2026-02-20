"""Etapa 0 — Seed refs: extração de referências de textos existentes.

Parser agnóstico de estilo de citação. Cascade:
1. DOI no texto → validar via CrossRef
2. Sem DOI → extrair título + primeiro sobrenome → buscar CrossRef
3. Fallback → incluir como-está com needs_verification=True
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ..apis.crossref import search_crossref, verify_doi
from ..config import get_api_config, get_pipeline_dir
from ..models import Reference


def extract_seed_refs(
    project_name: str,
    source_file: str | None = None,
) -> list[Reference]:
    """Extrai referências seed de textos existentes do projeto.

    Busca em:
    1. Arquivo fonte específico (se fornecido)
    2. Arquivo canônico do projeto (ex: capitulo16.md)
    3. notas_pesquisa.md

    Returns:
        Lista de Reference com source="seed".
    """
    from ..config import get_project_dir

    project_dir = get_project_dir(project_name)
    api_config = get_api_config()
    email = api_config.get("CROSSREF_EMAIL", "")

    # Determinar arquivos a processar
    files_to_scan: list[Path] = []
    if source_file:
        path = Path(source_file)
        if not path.is_absolute():
            path = project_dir / source_file
        if path.exists():
            files_to_scan.append(path)
    else:
        # Buscar arquivos .md do projeto (exceto SKILL.md e PROMPT_RETOMADA.md)
        for md in project_dir.glob("*.md"):
            if md.name not in ("SKILL.md", "PROMPT_RETOMADA.md"):
                files_to_scan.append(md)

    if not files_to_scan:
        print("  Nenhum arquivo fonte encontrado para seed refs.")
        return []

    # Extrair DOIs e referências textuais
    all_dois: list[str] = []
    all_text_refs: list[str] = []

    for filepath in files_to_scan:
        print(f"  Escaneando: {filepath.name}")
        text = filepath.read_text(encoding="utf-8")
        dois = _extract_dois(text)
        text_refs = _extract_text_references(text)
        all_dois.extend(dois)
        all_text_refs.extend(text_refs)

    print(f"  → {len(all_dois)} DOIs encontrados, {len(all_text_refs)} referências textuais")

    # Deduplicar DOIs
    unique_dois = list(dict.fromkeys(all_dois))

    references: list[Reference] = []

    # Processar DOIs
    for doi in unique_dois:
        print(f"    DOI: {doi}")
        ref = _resolve_doi(doi, email)
        if ref:
            references.append(ref)
        time.sleep(0.15)

    # Processar referências textuais (sem DOI)
    for text_ref in all_text_refs[:20]:  # Limitar a 20 para não sobrecarregar
        # Extrair título e autor
        title, author = _extract_title_author(text_ref)
        if not title:
            continue

        # Buscar no CrossRef
        ref = _search_crossref_for_ref(title, author, email)
        if ref:
            references.append(ref)
        else:
            # Fallback: incluir como-está
            ref = Reference(
                id=f"seed_{hash(text_ref) % 100000}",
                title=title,
                source="seed",
                source_api="manual",
                relevance_method="needs_verification",
            )
            references.append(ref)
        time.sleep(0.15)

    print(f"  → {len(references)} seed refs extraídas")

    # Salvar
    if references:
        pipeline_dir = get_pipeline_dir(project_name)
        seed_path = pipeline_dir / "refs-seed.json"
        data = {
            "metadata": {
                "project": project_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_refs": len(references),
                "source_files": [f.name for f in files_to_scan],
            },
            "references": [ref.to_dict() for ref in references],
        }
        with open(seed_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  refs-seed.json salvo: {seed_path}")

    return references


def _extract_dois(text: str) -> list[str]:
    """Extrai DOIs de um texto."""
    # Padrão DOI: 10.XXXX/...
    pattern = r'10\.\d{4,}/[^\s\]\)\},;]+'
    matches = re.findall(pattern, text)
    # Limpar trailing pontuação
    cleaned = []
    for doi in matches:
        doi = doi.rstrip('.')
        doi = doi.rstrip(',')
        doi = doi.rstrip(')')
        if len(doi) > 8:  # DOI mínimo válido
            cleaned.append(doi)
    return cleaned


def _extract_text_references(text: str) -> list[str]:
    """Extrai referências textuais (linhas de lista de referências)."""
    refs = []
    in_refs_section = False

    for line in text.split('\n'):
        line = line.strip()

        # Detectar seção de referências
        if re.match(r'^#+\s*(Referências|References|Bibliografia)', line, re.IGNORECASE):
            in_refs_section = True
            continue

        # Parar em outra seção
        if in_refs_section and re.match(r'^#+\s', line):
            break

        # Coletar linhas de referência
        if in_refs_section and line:
            # Referência numerada: "1. ..." ou "[1] ..."
            if re.match(r'^(\d+[\.\)]\s|\[\d+\]\s)', line):
                refs.append(line)
            # Referência com autor (Sobrenome, ano)
            elif re.match(r'^[A-Z][a-záàâã]', line) and len(line) > 30:
                refs.append(line)

    return refs


def _extract_title_author(text_ref: str) -> tuple[str, str]:
    """Extrai título e primeiro autor de uma referência textual.

    Agnóstico de estilo — tenta extrair pela estrutura.
    """
    # Remover numeração inicial
    text_ref = re.sub(r'^(\d+[\.\)]\s|\[\d+\]\s)', '', text_ref).strip()

    # Tentar extrair autor (primeiro sobrenome antes de vírgula ou espaço)
    author = ""
    author_match = re.match(r'^([A-Z][a-záàâãéèêíóòôõúç]+)', text_ref)
    if author_match:
        author = author_match.group(1)

    # Tentar extrair título (maior substring entre pontos)
    parts = re.split(r'\.\s+', text_ref)
    if len(parts) >= 2:
        # O título geralmente é o segundo segmento (após autores)
        title_candidates = [p for p in parts[1:] if len(p) > 15]
        if title_candidates:
            return title_candidates[0].strip('.'), author

    # Fallback: usar texto inteiro como busca
    return text_ref[:100], author


def _resolve_doi(doi: str, email: str) -> Reference | None:
    """Resolve DOI via CrossRef e cria Reference."""
    result = verify_doi(doi, email=email)
    if not result["resolves"]:
        return None

    metadata = result.get("metadata", {})
    titles = metadata.get("title", [])
    title = titles[0] if titles else ""

    # Autores
    authors = []
    for author in metadata.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        if family:
            authors.append(f"{family} {given}".strip())

    # Ano
    year = None
    issued = metadata.get("issued", {})
    date_parts = issued.get("date-parts", [[]])
    if date_parts and date_parts[0]:
        year = date_parts[0][0]

    # Journal
    journal = None
    container = metadata.get("container-title", [])
    if container:
        journal = container[0]

    ref = Reference(
        id=f"seed_{doi.replace('/', '_')}",
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        journal=journal,
        pub_type=result.get("pub_type", ""),
        source="seed",
        source_api="crossref",
        doi_resolves=True,
        crossref_match=True,
        retracted=result.get("is_retracted", False),
        has_correction=result.get("has_update", False),
    )
    return ref


def _search_crossref_for_ref(
    title: str,
    author: str,
    email: str,
) -> Reference | None:
    """Busca referência no CrossRef por título + autor."""
    query = f"{author} {title}" if author else title
    results = search_crossref(query, email=email, max_results=3)

    if not results:
        return None

    # Fuzzy match do título
    from rapidfuzz import fuzz
    for item in results:
        cr_titles = item.get("title", [])
        cr_title = cr_titles[0] if cr_titles else ""
        if cr_title:
            similarity = fuzz.ratio(title.lower(), cr_title.lower())
            if similarity >= 70:
                doi = item.get("DOI", "")
                authors = []
                for a in item.get("author", []):
                    family = a.get("family", "")
                    given = a.get("given", "")
                    if family:
                        authors.append(f"{family} {given}".strip())

                year = None
                issued = item.get("issued", {})
                date_parts = issued.get("date-parts", [[]])
                if date_parts and date_parts[0]:
                    year = date_parts[0][0]

                journal = None
                container = item.get("container-title", [])
                if container:
                    journal = container[0]

                return Reference(
                    id=f"seed_{doi.replace('/', '_')}" if doi else f"seed_{hash(title) % 100000}",
                    title=cr_title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    journal=journal,
                    pub_type=item.get("type", ""),
                    source="seed",
                    source_api="crossref",
                    doi_resolves=bool(doi),
                    crossref_match=True,
                    crossref_title_similarity=similarity,
                )

    return None
