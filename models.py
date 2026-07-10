"""Data model do pipeline — Reference, enums e grade computation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(Enum):
    """Qualidade da fonte por domínio."""
    T1 = 1  # Periódico revisado por pares, indexado
    T2 = 2  # Fonte institucional de referência
    T3 = 3  # Relatório técnico / especializado
    T4 = 4  # Notícia, blog, site corporativo
    UNKNOWN = 0


class Relevance(Enum):
    """Relevância para o escopo do projeto."""
    DIRECT = 2      # Assunto principal coincide com o escopo
    TANGENTIAL = 1  # Toca no tema mas foco é outro
    OFF_TOPIC = 0   # Sem relação


class AccessStatus(Enum):
    """Status de acessibilidade da referência."""
    ACCESSIBLE = "accessible"
    RESTRICTED = "restricted"   # Paywall / bloqueio de bot
    BROKEN = "broken"           # 404 / timeout persistente
    NO_URL = "no_url"
    OPEN_ACCESS = "open_access"


class CompositeGrade(Enum):
    """Classificação composta final."""
    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"
    DISCARD = "discard"
    UNGRADED = "ungraded"


class Modality(Enum):
    """Modalidade do pipeline."""
    PESQUISA_BASE = "pesquisa-base"
    REVISAO_INTEGRATIVA = "revisao-integrativa"
    REVISAO_SISTEMATICA = "revisao-sistematica"
    REVISAO_ESCOPO = "revisao-escopo"
    META_REVISAO = "meta-revisao"  # umbrella review — só inclui revisões

REVIEW_MODALITIES = {
    Modality.REVISAO_INTEGRATIVA,
    Modality.REVISAO_SISTEMATICA,
    Modality.REVISAO_ESCOPO,
    Modality.META_REVISAO,
}


@dataclass
class Reference:
    """Referência bibliográfica — modelo central do pipeline.

    Campos são preenchidos progressivamente ao longo das 10 etapas.
    """

    # --- Identidade (etapa 2: busca) ---
    id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    pmid: str | None = None
    openalex_id: str | None = None
    url: str | None = None
    journal: str | None = None
    abstract: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    source_api: str = ""
    search_query: str = ""
    pub_type: str | None = None

    # --- Dedup (etapa 3) ---
    is_duplicate: bool = False
    canonical_id: str | None = None

    # --- Verificação (etapa 4) ---
    doi_resolves: bool | None = None
    crossref_match: bool | None = None
    crossref_title_similarity: float | None = None
    authors_verified: bool | None = None
    verified_via: str | None = None  # crossref | pubmed | doi.org | title-pubmed | title-crossref
    verified_authors: list[str] = field(default_factory=list)
    verified_journal: str | None = None
    verified_volume: str | None = None
    verified_issue: str | None = None
    verified_pages: str | None = None
    verification_issues: list[str] = field(default_factory=list)

    # --- Classificação (etapa 5) ---
    tier: Tier = Tier.UNKNOWN
    domain: str | None = None

    # --- Relevância (etapa 6) ---
    relevance: Relevance = Relevance.OFF_TOPIC
    relevance_method: str = ""

    # --- Triagem semântica por LLM (etapa 6b — critérios de I/E em prosa) ---
    llm_verdict: str | None = None  # include | exclude | maybe
    llm_reason: str | None = None
    llm_criteria: list[str] = field(default_factory=list)  # ex: ["I1", "E2"]

    # --- Integridade (etapa 7) ---
    retracted: bool | None = None
    has_correction: bool | None = None

    # --- Acesso (etapa 8) ---
    access_status: AccessStatus = AccessStatus.NO_URL
    is_open_access: bool | None = None
    oa_url: str | None = None

    # --- Ranking e triagem (etapa 6+) ---
    relevance_score: float = 0.0
    mapped_axes: list[str] = field(default_factory=list)
    source: str = ""  # "api", "seed", "manual"
    citation_count: int | None = None
    canonical: bool = False  # Paper canônico (alta citação)

    # --- Grade composta (calculada após etapas 5-8) ---
    grade: CompositeGrade = CompositeGrade.UNGRADED

    def verification_pendencies(self) -> list[str]:
        """Pendências de verificação que exigem revisão humana.

        Princípio zero-trust: nenhuma pendência pode ser silenciosa.
        Uma ref com pendência nunca recebe grade acima de BRONZE.
        """
        pendencies = []
        if self.authors_verified is False:
            pendencies.append("autores divergem da fonte autoritativa")
        if self.doi_resolves is True and self.crossref_match is False:
            pendencies.append(
                "título diverge do registrado para o DOI "
                f"(similaridade {self.crossref_title_similarity or 0:.0f})"
            )
        if not self.doi and not self.pmid and not self.verified_via:
            pendencies.append(
                "sem DOI/PMID e não verificada por título — verificar manualmente"
            )
        if self.doi_resolves is False:
            pendencies.append("DOI não resolve (CrossRef e doi.org)")
        return pendencies

    def compute_grade(self) -> CompositeGrade:
        """Calcula grade composta com base em tier, relevância e integridade.

        Regras (v2 — acesso NÃO reduz grade):
        - Retracted / off-topic / T4 / broken → DISCARD
        - T1-T2 + DIRECT (independente de acesso) → GOLD
        - T3 + DIRECT, ou T1-T2 + TANGENTIAL → SILVER
        - T3 + TANGENTIAL → BRONZE

        v3 (zero-trust): pendência de verificação (autores divergentes,
        título divergente, ref inverificável) limita a grade a BRONZE —
        a ref só sobe após resolução humana da pendência.

        Acesso vira campo informativo (determina se o MD terá conteúdo
        completo ou apenas abstract). Papers canônicos mantêm grade.
        """
        # Descarte automático
        if self.retracted is True:
            self.grade = CompositeGrade.DISCARD
            return self.grade
        if self.relevance == Relevance.OFF_TOPIC:
            self.grade = CompositeGrade.DISCARD
            return self.grade
        if self.tier == Tier.T4:
            self.grade = CompositeGrade.DISCARD
            return self.grade
        if self.access_status == AccessStatus.BROKEN:
            self.grade = CompositeGrade.DISCARD
            return self.grade

        # Trava zero-trust: pendência de verificação limita a BRONZE
        if self.verification_pendencies():
            self.grade = CompositeGrade.BRONZE
            return self.grade

        is_high_tier = self.tier in (Tier.T1, Tier.T2)

        # GOLD: T1-T2 + DIRECT (independente de acesso)
        if is_high_tier and self.relevance == Relevance.DIRECT:
            self.grade = CompositeGrade.GOLD
            return self.grade

        # SILVER: T3 + DIRECT, ou T1-T2 + TANGENTIAL
        if self.tier == Tier.T3 and self.relevance == Relevance.DIRECT:
            self.grade = CompositeGrade.SILVER
            return self.grade
        if is_high_tier and self.relevance == Relevance.TANGENTIAL:
            self.grade = CompositeGrade.SILVER
            return self.grade

        # BRONZE: T3 + TANGENTIAL
        if self.tier == Tier.T3 and self.relevance == Relevance.TANGENTIAL:
            self.grade = CompositeGrade.BRONZE
            return self.grade

        # Fallback
        self.grade = CompositeGrade.BRONZE
        return self.grade

    def to_dict(self) -> dict:
        """Serializa para JSON."""
        return {
            "id": self.id,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "doi": self.doi,
            "pmid": self.pmid,
            "openalex_id": self.openalex_id,
            "url": self.url,
            "journal": self.journal,
            "abstract": self.abstract,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "source_api": self.source_api,
            "search_query": self.search_query,
            "pub_type": self.pub_type,
            "is_duplicate": self.is_duplicate,
            "canonical_id": self.canonical_id,
            "doi_resolves": self.doi_resolves,
            "crossref_match": self.crossref_match,
            "crossref_title_similarity": self.crossref_title_similarity,
            "authors_verified": self.authors_verified,
            "verified_via": self.verified_via,
            "verified_authors": self.verified_authors,
            "verified_journal": self.verified_journal,
            "verified_volume": self.verified_volume,
            "verified_issue": self.verified_issue,
            "verified_pages": self.verified_pages,
            "verification_issues": self.verification_issues,
            "tier": self.tier.name,
            "domain": self.domain,
            "relevance": self.relevance.value,
            "relevance_method": self.relevance_method,
            "llm_verdict": self.llm_verdict,
            "llm_reason": self.llm_reason,
            "llm_criteria": self.llm_criteria,
            "retracted": self.retracted,
            "has_correction": self.has_correction,
            "access_status": self.access_status.value,
            "is_open_access": self.is_open_access,
            "oa_url": self.oa_url,
            "relevance_score": self.relevance_score,
            "mapped_axes": self.mapped_axes,
            "source": self.source,
            "citation_count": self.citation_count,
            "canonical": self.canonical,
            "grade": self.grade.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Reference:
        """Deserializa de JSON."""
        ref = cls()
        ref.id = data.get("id", "")
        ref.title = data.get("title", "")
        ref.authors = data.get("authors", [])
        ref.year = data.get("year")
        ref.doi = data.get("doi")
        ref.pmid = data.get("pmid")
        ref.openalex_id = data.get("openalex_id")
        ref.url = data.get("url")
        ref.journal = data.get("journal")
        ref.abstract = data.get("abstract")
        ref.volume = data.get("volume")
        ref.issue = data.get("issue")
        ref.pages = data.get("pages")
        ref.source_api = data.get("source_api", "")
        ref.search_query = data.get("search_query", "")
        ref.pub_type = data.get("pub_type")
        ref.is_duplicate = data.get("is_duplicate", False)
        ref.canonical_id = data.get("canonical_id")
        ref.doi_resolves = data.get("doi_resolves")
        ref.crossref_match = data.get("crossref_match")
        ref.crossref_title_similarity = data.get("crossref_title_similarity")
        ref.authors_verified = data.get("authors_verified")
        ref.verified_via = data.get("verified_via")
        ref.verified_authors = data.get("verified_authors", [])
        ref.verified_journal = data.get("verified_journal")
        ref.verified_volume = data.get("verified_volume")
        ref.verified_issue = data.get("verified_issue")
        ref.verified_pages = data.get("verified_pages")
        ref.verification_issues = data.get("verification_issues", [])
        tier_name = data.get("tier", "UNKNOWN")
        ref.tier = Tier[tier_name] if tier_name in Tier.__members__ else Tier.UNKNOWN
        ref.domain = data.get("domain")
        ref.relevance = Relevance(data.get("relevance", 0))
        ref.llm_verdict = data.get("llm_verdict")
        ref.llm_reason = data.get("llm_reason")
        ref.llm_criteria = data.get("llm_criteria", [])
        ref.relevance_method = data.get("relevance_method", "")
        ref.retracted = data.get("retracted")
        ref.has_correction = data.get("has_correction")
        access_val = data.get("access_status", "no_url")
        ref.access_status = AccessStatus(access_val)
        ref.is_open_access = data.get("is_open_access")
        ref.oa_url = data.get("oa_url")
        ref.relevance_score = data.get("relevance_score", 0.0)
        ref.mapped_axes = data.get("mapped_axes", [])
        ref.source = data.get("source", "")
        ref.citation_count = data.get("citation_count")
        ref.canonical = data.get("canonical", False)
        grade_val = data.get("grade", "ungraded")
        ref.grade = CompositeGrade(grade_val)
        return ref


@dataclass
class ResearchAxis:
    """Eixo de pesquisa com keywords e sinônimos."""
    name: str = ""
    keywords: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)


@dataclass
class SearchConfig:
    """Configuração de busca extraída do scope.yaml."""
    project_name: str = ""
    modality: Modality = Modality.PESQUISA_BASE
    research_question: str = ""
    keyword_blocks: list[dict[str, list[str]]] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    decs_terms: list[str] = field(default_factory=list)
    inclusion_criteria: list[str] = field(default_factory=list)
    exclusion_criteria: list[str] = field(default_factory=list)
    exclusion_keywords: list[str] = field(default_factory=list)
    year_range: tuple[int, int] = (2020, 2026)
    languages: list[str] = field(default_factory=lambda: ["en", "pt"])
    apis: list[str] = field(default_factory=lambda: ["pubmed", "openalex"])
    max_results_per_api: int = 100
    max_final_refs: int = 40
    relevance_keywords_direct: list[str] = field(default_factory=list)
    relevance_keywords_tangential: list[str] = field(default_factory=list)
    relevance_keywords_off_topic: list[str] = field(default_factory=list)
    research_axes: list[ResearchAxis] = field(default_factory=list)
    quality_framework: str | None = None
    # Áreas do Semantic Scholar (fieldsOfStudy); lista vazia = sem filtro
    fields_of_study: list[str] = field(default_factory=lambda: ["Medicine"])
