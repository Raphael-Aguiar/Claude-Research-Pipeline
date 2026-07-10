"""PubMed/NCBI E-utilities API client."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from Bio import Entrez

from ..models import Reference

if TYPE_CHECKING:
    from ..models import SearchConfig


def _build_query(config: SearchConfig) -> str:
    """Constrói query PubMed a partir dos keyword_blocks do scope.yaml.

    Melhorias v2:
    - Field specifiers [Title/Abstract] para precisão
    - MeSH terms com AND (fallback OR se <10 resultados)
    - Filtro de idioma
    - Filtro de publication type (exclui editoriais/cartas)
    """
    # Keyword blocks com [Title/Abstract]
    parts = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            group = " OR ".join(f'"{t}"[Title/Abstract]' for t in terms)
            parts.append(f"({group})")

    keyword_query = " AND ".join(parts)

    # MeSH terms combinados com AND (mais preciso)
    mesh_query = ""
    if config.mesh_terms:
        mesh_part = " OR ".join(f'"{m}"[MeSH]' for m in config.mesh_terms)
        mesh_query = f"({mesh_part})"

    # Combinar: keywords AND MeSH (se ambos existirem)
    if keyword_query and mesh_query:
        query = f"({keyword_query}) AND {mesh_query}"
    elif keyword_query:
        query = keyword_query
    elif mesh_query:
        query = mesh_query
    else:
        return ""

    # Filtro de período
    start, end = config.year_range
    query += f' AND ("{start}"[PDAT] : "{end}"[PDAT])'

    # Filtro de idioma
    if config.languages:
        lang_map = {"en": "english", "pt": "portuguese", "es": "spanish"}
        lang_parts = []
        for lang in config.languages:
            code = lang_map.get(lang, lang)
            lang_parts.append(f'"{code}"[Language]')
        if lang_parts:
            query += f' AND ({" OR ".join(lang_parts)})'

    # Filtro de publication type
    from ..models import Modality

    if config.modality == Modality.META_REVISAO:
        # Meta-revisão (umbrella review): só revisões entram.
        # systematic[sb] é o subset oficial do PubMed para revisões sistemáticas.
        query += (
            ' AND (systematic[sb] OR "Review"[PT]'
            ' OR "Systematic Review"[PT] OR "Meta-Analysis"[PT])'
        )
    else:
        # Excluir editoriais, cartas, comentários
        query += (
            ' AND ("Journal Article"[PT] OR "Review"[PT]'
            ' OR "Systematic Review"[PT] OR "Meta-Analysis"[PT])'
        )

    return query


def _build_fallback_query(config: SearchConfig) -> str:
    """Query fallback com MeSH em OR (menos restritiva)."""
    parts = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            group = " OR ".join(f'"{t}"[Title/Abstract]' for t in terms)
            parts.append(f"({group})")

    keyword_query = " AND ".join(parts)

    if config.mesh_terms:
        mesh_part = " OR ".join(f'"{m}"[MeSH]' for m in config.mesh_terms)
        if keyword_query:
            query = f"({keyword_query}) OR ({mesh_part})"
        else:
            query = mesh_part
    else:
        query = keyword_query

    if not query:
        return ""

    start, end = config.year_range
    query += f' AND ("{start}"[PDAT] : "{end}"[PDAT])'

    if config.languages:
        lang_map = {"en": "english", "pt": "portuguese", "es": "spanish"}
        lang_parts = [f'"{lang_map.get(l, l)}"[Language]' for l in config.languages]
        query += f' AND ({" OR ".join(lang_parts)})'

    query += (
        ' AND ("Journal Article"[PT] OR "Review"[PT]'
        ' OR "Systematic Review"[PT] OR "Meta-Analysis"[PT])'
    )

    return query


def search_pubmed(
    config: SearchConfig,
    email: str,
    api_key: str = "",
    max_results: int | None = None,
) -> list[Reference]:
    """Busca referências no PubMed via E-utilities.

    Args:
        config: Configuração de busca do scope.yaml.
        email: Email para NCBI (obrigatório).
        api_key: API key NCBI (opcional, aumenta rate limit).
        max_results: Override de max_results_per_api.

    Returns:
        Lista de Reference com campos de identidade preenchidos.
    """
    if not email:
        raise ValueError("NCBI_EMAIL é obrigatório para PubMed. Configure no .env.")

    Entrez.email = email
    if api_key:
        Entrez.api_key = api_key

    query = _build_query(config)
    limit = max_results or config.max_results_per_api

    # Etapa 1: esearch para obter PMIDs
    handle = Entrez.esearch(
        db="pubmed",
        term=query,
        retmax=limit,
        sort="relevance",
    )
    search_results = Entrez.read(handle)
    handle.close()

    pmids = search_results.get("IdList", [])

    # Fallback: se MeSH AND retorna <10 resultados, usar OR
    if len(pmids) < 10 and config.mesh_terms:
        fallback_query = _build_fallback_query(config)
        if fallback_query != query:
            print("    PubMed: query com MeSH AND retornou poucos resultados, tentando OR...")
            time.sleep(0.34)
            handle = Entrez.esearch(
                db="pubmed",
                term=fallback_query,
                retmax=limit,
                sort="relevance",
            )
            fallback_results = Entrez.read(handle)
            handle.close()
            fallback_pmids = fallback_results.get("IdList", [])
            if len(fallback_pmids) > len(pmids):
                pmids = fallback_pmids
                query = fallback_query

    if not pmids:
        return []

    # Rate limiting respeitoso
    time.sleep(0.34)  # ~3 req/s sem API key

    # Etapa 2: efetch para obter metadados
    handle = Entrez.efetch(
        db="pubmed",
        id=",".join(pmids),
        rettype="xml",
        retmode="xml",
    )
    records = Entrez.read(handle)
    handle.close()

    references = []
    articles = records.get("PubmedArticle", [])

    for article in articles:
        ref = _parse_pubmed_article(article, query)
        if ref:
            references.append(ref)

    return references


def find_pmid_by_title(
    title: str,
    email: str,
    year: int | None = None,
) -> str | None:
    """Busca PMID pelo título exato (campo [Title]) no PubMed.

    Usado para verificar referências sem DOI. Retorna o PMID apenas se
    o título do resultado tiver similaridade fuzzy >= 90 com o buscado
    (e ano compatível ±1, quando informado); caso contrário, None.
    """
    if not email or not title:
        return None
    Entrez.email = email

    from rapidfuzz import fuzz

    try:
        handle = Entrez.esearch(
            db="pubmed",
            term=f"{title}[Title]",
            retmax=3,
        )
        result = Entrez.read(handle)
        handle.close()
        time.sleep(0.34)
    except Exception as e:
        print(f"    PubMed find_pmid_by_title: erro na busca ({e})")
        return None

    for candidate_pmid in result.get("IdList", []):
        meta = fetch_pubmed_metadata(pmid=candidate_pmid, email=email)
        if not meta:
            continue
        similarity = fuzz.ratio(
            title.lower().strip(), meta["title"].lower().strip()
        )
        if similarity < 90:
            continue
        if year and meta.get("year", "").isdigit():
            if abs(int(meta["year"]) - year) > 1:
                continue
        return candidate_pmid
    return None


def fetch_pubmed_metadata(
    doi: str | None = None,
    pmid: str | None = None,
    email: str = "",
) -> dict | None:
    """Busca metadados completos no PubMed por DOI ou PMID.

    Retorna dict com: authors, journal, journal_abbrev, volume, issue,
    pages, year, title, pmid. Retorna None se não encontrado.
    """
    if not email:
        return None
    Entrez.email = email

    # Resolver DOI → PMID se necessário
    target_pmid = pmid
    if not target_pmid and doi:
        try:
            handle = Entrez.esearch(
                db="pubmed",
                term=f"{doi}[doi]",
                retmax=1,
            )
            result = Entrez.read(handle)
            handle.close()
            ids = result.get("IdList", [])
            if ids:
                target_pmid = ids[0]
            time.sleep(0.34)
        except Exception:
            return None

    if not target_pmid:
        return None

    # Fetch metadados completos
    try:
        handle = Entrez.efetch(
            db="pubmed",
            id=target_pmid,
            rettype="xml",
            retmode="xml",
        )
        records = Entrez.read(handle)
        handle.close()
    except Exception:
        return None

    articles = records.get("PubmedArticle", [])
    if not articles:
        return None

    article = articles[0]
    medline = article.get("MedlineCitation", {})
    art_data = medline.get("Article", {})

    # Autores no formato Vancouver (Sobrenome INICIAIS)
    authors = []
    for a in art_data.get("AuthorList", []):
        last = a.get("LastName", "")
        initials = a.get("Initials", "")
        if last and initials:
            authors.append(f"{last} {initials}")
        elif last:
            authors.append(last)

    # Journal
    journal_info = art_data.get("Journal", {})
    ji = journal_info.get("JournalIssue", {})
    pub_date = ji.get("PubDate", {})
    year_str = str(pub_date.get("Year", ""))

    # Páginas
    pages = str(art_data.get("Pagination", {}).get("MedlinePgn", "")) or ""
    if not pages:
        for eloc in art_data.get("ELocationID", []):
            if str(eloc.attributes.get("EIdType", "")) == "pii":
                pages = str(eloc)
                break

    return {
        "pmid": target_pmid,
        "title": str(art_data.get("ArticleTitle", "")),
        "authors": authors,
        "journal": str(journal_info.get("Title", "")),
        "journal_abbrev": str(journal_info.get("ISOAbbreviation", "")),
        "volume": str(ji.get("Volume", "")) or "",
        "issue": str(ji.get("Issue", "")) or "",
        "pages": pages,
        "year": year_str,
        "source": "pubmed",
    }


def _parse_pubmed_article(article: dict, query: str) -> Reference | None:
    """Converte um registro PubMed XML em Reference."""
    try:
        medline = article.get("MedlineCitation", {})
        pmid_val = str(medline.get("PMID", ""))
        art_data = medline.get("Article", {})

        # Título
        title = str(art_data.get("ArticleTitle", ""))

        # Autores
        authors = []
        author_list = art_data.get("AuthorList", [])
        for author in author_list:
            last = author.get("LastName", "")
            fore = author.get("ForeName", "")
            if last:
                authors.append(f"{last} {fore}".strip())

        # Journal
        journal_info = art_data.get("Journal", {})
        journal = str(journal_info.get("Title", ""))
        ji = journal_info.get("JournalIssue", {})
        volume = str(ji.get("Volume", "")) or None
        issue = str(ji.get("Issue", "")) or None

        # Ano
        pub_date = ji.get("PubDate", {})
        year_str = str(pub_date.get("Year", ""))
        # Fallback: MedlineDate
        if not year_str and "MedlineDate" in pub_date:
            import re
            m = re.search(r"(\d{4})", str(pub_date["MedlineDate"]))
            year_str = m.group(1) if m else ""
        year = int(year_str) if year_str.isdigit() else None

        # Páginas
        pages = str(art_data.get("Pagination", {}).get("MedlinePgn", "")) or None

        # DOI
        doi = None
        article_ids = art_data.get("ELocationID", [])
        for eid in article_ids:
            if str(eid.attributes.get("EIdType", "")) == "doi":
                doi = str(eid)
                break

        # DOI fallback: PubmedData
        if not doi:
            pubmed_data = article.get("PubmedData", {})
            for id_item in pubmed_data.get("ArticleIdList", []):
                if str(id_item.attributes.get("IdType", "")) == "doi":
                    doi = str(id_item)
                    break

        # Abstract
        abstract_parts = art_data.get("Abstract", {}).get("AbstractText", [])
        abstract = " ".join(str(part) for part in abstract_parts) if abstract_parts else None

        # Pub type
        pub_types = art_data.get("PublicationTypeList", [])
        pub_type = str(pub_types[0]) if pub_types else None

        # URL
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_val}/"

        ref = Reference(
            id=f"pubmed_{pmid_val}",
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            pmid=pmid_val,
            url=url,
            journal=journal,
            abstract=abstract,
            volume=volume,
            issue=issue,
            pages=pages,
            source_api="pubmed",
            search_query=query,
            pub_type=pub_type,
            source="api",
        )
        return ref

    except Exception:
        return None
