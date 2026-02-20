"""OpenAlex API client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyalex
from pyalex import Works

from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig


def _build_search_query(config: SearchConfig) -> str:
    """Constrói query de busca textual a partir dos keyword_blocks.

    OpenAlex .search() aceita texto livre mas queries muito longas
    retornam 0 resultados. Estratégia: pegar os 2 primeiros termos
    de cada bloco (máximo 3 blocos = 6 termos), enriquecendo com
    sinônimos dos research_axes quando disponíveis.
    """
    parts = []
    for block in config.keyword_blocks[:3]:
        terms = block.get("terms", [])
        parts.extend(terms[:2])

    # Enriquecer com sinônimos dos eixos (até 2 extras)
    if config.research_axes:
        extra = []
        for axis in config.research_axes[:2]:
            if axis.synonyms:
                extra.append(axis.synonyms[0])
        parts.extend(extra)

    return " ".join(parts)


def search_openalex(
    config: SearchConfig,
    email: str = "",
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências no OpenAlex.

    Args:
        config: Configuração de busca do scope.yaml.
        email: Email para polite pool (recomendado).
        max_results: Override de max_results_per_api.

    Returns:
        Lista de Reference com campos de identidade preenchidos.
    """
    if email:
        pyalex.config.email = email

    query = _build_search_query(config)
    limit = max_results or config.max_results_per_api
    start_year, end_year = config.year_range

    # Construir filtro
    filters = {
        "from_publication_date": f"{start_year}-01-01",
        "to_publication_date": f"{end_year}-12-31",
    }

    # Busca paginada
    references = []
    per_page = min(limit, 200)
    try:
        pager = (
            Works()
            .search(query)
            .filter(**filters)
            .sort(relevance_score="desc")
            .paginate(per_page=per_page, n_max=limit)
        )

        for page in pager:
            for work in page:
                ref = _parse_openalex_work(work, query)
                if ref:
                    references.append(ref)
                    if len(references) >= limit:
                        break
            if len(references) >= limit:
                break

    except Exception as e:
        # Se a busca paginada falhar, tentar busca simples
        print(f"    OpenAlex busca com filtros falhou ({e}), tentando busca simples...")
        try:
            results = Works().search(query).get(per_page=min(limit, 50))
            for work in results:
                ref = _parse_openalex_work(work, query)
                if ref:
                    references.append(ref)
                    if len(references) >= limit:
                        break
        except Exception as e2:
            print(f"    OpenAlex busca simples também falhou: {e2}")

    return references


def _parse_openalex_work(work: dict, query: str) -> Reference | None:
    """Converte um registro OpenAlex em Reference."""
    try:
        oa_id = work.get("id", "")
        title = work.get("title") or ""
        doi = work.get("doi")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")

        # Autores
        authors = []
        for authorship in work.get("authorships", []):
            author = authorship.get("author", {})
            name = author.get("display_name", "")
            if name:
                authors.append(name)

        # Ano
        year = work.get("publication_year")

        # Journal
        primary_location = work.get("primary_location") or {}
        source = primary_location.get("source") or {}
        journal = source.get("display_name")

        # URL
        url = work.get("primary_location", {}).get("landing_page_url")
        if not url:
            url = f"https://openalex.org/works/{oa_id.split('/')[-1]}" if oa_id else None

        # PMID
        ids = work.get("ids", {})
        pmid = ids.get("pmid")
        if pmid and pmid.startswith("https://pubmed.ncbi.nlm.nih.gov/"):
            pmid = pmid.replace("https://pubmed.ncbi.nlm.nih.gov/", "").rstrip("/")

        # Abstract (OpenAlex retorna abstract invertido por posição)
        abstract = None
        abstract_index = work.get("abstract_inverted_index")
        if abstract_index:
            abstract = _reconstruct_abstract(abstract_index)

        # Tipo de publicação
        pub_type = work.get("type")

        # Volume, issue, pages
        biblio = work.get("biblio") or {}
        volume = biblio.get("volume")
        issue = biblio.get("issue")
        first_page = biblio.get("first_page")
        last_page = biblio.get("last_page")
        pages = None
        if first_page:
            pages = f"{first_page}-{last_page}" if last_page else first_page

        # Open access
        oa_info = work.get("open_access", {})
        is_oa = oa_info.get("is_oa", False)
        oa_url_val = oa_info.get("oa_url")

        # Citation count
        cited_by_count = work.get("cited_by_count")

        ref = Reference(
            id=f"openalex_{oa_id.split('/')[-1]}" if oa_id else "",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            pmid=pmid,
            openalex_id=oa_id,
            url=url,
            journal=journal,
            abstract=abstract,
            volume=volume,
            issue=issue,
            pages=pages,
            source_api="openalex",
            search_query=query,
            pub_type=pub_type,
            is_open_access=is_oa,
            oa_url=oa_url_val,
            citation_count=cited_by_count,
            source="api",
        )
        return ref

    except Exception:
        return None


def _reconstruct_abstract(inverted_index: dict) -> str:
    """Reconstrói abstract do formato invertido do OpenAlex."""
    if not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in word_positions)
