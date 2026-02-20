"""Verificador de afirmações (claims) em textos acadêmicos.

Verifica se as afirmações no texto são realmente suportadas
pelas referências citadas. Camada 1 — verificação automatizada.

Uso:
    python -m tools verify-claims "Livro Editora Atheneu/capitulo16.md"
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ClaimType(Enum):
    """Tipo de afirmação."""
    A = "quantitative"   # Números, percentagens, valores monetários
    B = "qualitative"    # Descritivo específico (métodos, modelos, instituições)
    C = "conceptual"     # Frameworks, conceitos gerais


class VerificationStatus(Enum):
    """Status da verificação."""
    VERIFIED = "VERIFIED"
    APPROXIMATE_MATCH = "APPROXIMATE_MATCH"
    ENTITY_FOUND = "ENTITY_FOUND"
    THEMATIC_MATCH = "THEMATIC_MATCH"
    NOT_FOUND_ABSTRACT = "NOT_FOUND_ABSTRACT"
    NOT_FOUND_FULLTEXT = "NOT_FOUND_FULLTEXT"
    NOT_FOUND_ALL_REFS = "NOT_FOUND_ALL_REFS"
    MATH_ERROR = "MATH_ERROR"
    UNVERIFIABLE = "UNVERIFIABLE"


class SourceLevel(Enum):
    """Nível de profundidade do conteúdo obtido."""
    FULL_TEXT = "full_text"
    ABSTRACT = "abstract"
    NONE = "none"


class FabricationRisk(Enum):
    """Risco de fabricação."""
    NONE = "NONE"
    LOW = "LOW"
    LOW_MEDIUM = "LOW-MEDIUM"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DataPoint:
    """Um ponto de dado numérico extraído de um claim."""
    raw_text: str           # "469 mil dólares", "85%", "6,2%"
    value: float            # 469000, 85, 6.2
    unit: str               # "%", "USD", "dias", "bilhões USD", ""
    is_percentage: bool     # True se é percentagem


@dataclass
class Claim:
    """Uma afirmação extraída do texto com suas referências."""
    text: str               # Texto da afirmação
    ref_numbers: list[int]  # [13] ou [11,17]
    claim_type: ClaimType   # A, B ou C
    data_points: list[DataPoint] = field(default_factory=list)
    has_calculation: bool = False  # True se contém absolutos + % derivada
    paragraph_context: str = ""    # Parágrafo completo para contexto
    line_number: int = 0


@dataclass
class RefContent:
    """Conteúdo resolvido de uma referência."""
    number: int
    title: str
    content: str            # Texto completo ou abstract
    source_level: SourceLevel
    source_description: str  # "pipeline/refs/file.md", "PubMed abstract", etc.
    doi: str = ""
    pmid: str = ""
    ref_type: str = ""      # "journal", "web", "legislation"


@dataclass
class ClaimResult:
    """Resultado da verificação de um claim."""
    claim: Claim
    status: VerificationStatus
    fabrication_risk: FabricationRisk
    details: str            # Explicação do resultado
    matched_ref: int | None = None       # Qual ref verificou o dado
    matched_text: str = ""               # Trecho encontrado na ref
    context: str = ""                    # ±150 chars ao redor do match
    source_level: SourceLevel = SourceLevel.NONE
    approximate_value: float | None = None  # Para APPROXIMATE_MATCH
    math_expected: str = ""              # Para MATH_ERROR
    math_found: str = ""                 # Para MATH_ERROR


# ---------------------------------------------------------------------------
# Mapeamento status → risco
# ---------------------------------------------------------------------------

STATUS_RISK_MAP: dict[VerificationStatus, FabricationRisk] = {
    VerificationStatus.VERIFIED: FabricationRisk.NONE,
    VerificationStatus.APPROXIMATE_MATCH: FabricationRisk.LOW,
    VerificationStatus.ENTITY_FOUND: FabricationRisk.LOW_MEDIUM,
    VerificationStatus.THEMATIC_MATCH: FabricationRisk.NONE,
    VerificationStatus.NOT_FOUND_ABSTRACT: FabricationRisk.MEDIUM,
    VerificationStatus.NOT_FOUND_FULLTEXT: FabricationRisk.HIGH,
    VerificationStatus.NOT_FOUND_ALL_REFS: FabricationRisk.CRITICAL,
    VerificationStatus.MATH_ERROR: FabricationRisk.HIGH,
    VerificationStatus.UNVERIFIABLE: FabricationRisk.UNKNOWN,
}


# ---------------------------------------------------------------------------
# Extração de claims
# ---------------------------------------------------------------------------

# Regex para citações inline: [1], [1,2], [11,17]
_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# Regex para números PT-BR
_NUMBER_PT_RE = re.compile(
    r"(\d[\d.,]*)\s*"
    r"(%|mil|milh[õo]es|bilh[õo]es|trilh[õo]es|"
    r"dólares|reais|dias|anos|meses|horas|"
    r"leitos|pacientes|contratos|processos)?"
)

# Palavras-chave que indicam dados quantitativos
_QUANT_INDICATORS = re.compile(
    r"\d+[,.]?\d*\s*%|"           # percentagens
    r"\d[\d.,]*\s*(mil|milh|bilh|trilh)|"  # grandezas
    r"R\$|US\$|\$|dólares|reais|"  # moeda
    r"MAPE|acurácia|redução de|economia de|"
    r"crescimento de|taxa de|margem de|"
    r"\d+\s*para\s*\d+"            # X para Y
)

# Entidades comuns em claims tipo B (saúde/IA)
_ENTITY_INDICATORS = re.compile(
    r"XGBoost|Random Forest|LSTM|CNN|NLP|"
    r"machine learning|deep learning|"
    r"FHIR|HL7|TISS|LGPD|AMAM|HIMSS|"
    r"gêmeos? digitais|digital twin|"
    r"OMS|WHO|DATASUS|ANS|SUS"
)


def extract_claims(markdown_text: str) -> list[Claim]:
    """Extrai claims citados do texto markdown.

    Divide o texto em unidades de claim (texto entre citações)
    e classifica cada uma como A, B ou C.
    """
    claims: list[Claim] = []

    # Remover seção de referências para não analisar a lista
    ref_section = re.search(
        r"^##?\s*Referências\s*$",
        markdown_text,
        re.MULTILINE,
    )
    if ref_section:
        text_body = markdown_text[:ref_section.start()]
    else:
        text_body = markdown_text

    # Dividir em parágrafos (linhas não-vazias contíguas)
    paragraphs = re.split(r"\n\s*\n", text_body)

    for para_idx, paragraph in enumerate(paragraphs):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        # Pular cabeçalhos
        if paragraph.startswith("#"):
            continue

        # Encontrar todas as citações neste parágrafo
        citation_matches = list(_CITATION_RE.finditer(paragraph))
        if not citation_matches:
            continue

        # Extrair claims: texto associado a cada citação
        for i, match in enumerate(citation_matches):
            ref_str = match.group(1)
            ref_numbers = [int(n.strip()) for n in ref_str.split(",")]

            # Texto do claim: do fim da citação anterior (ou início do parágrafo)
            # até o fim desta citação
            start = citation_matches[i - 1].end() if i > 0 else 0
            end = match.start()
            claim_text = paragraph[start:end].strip()

            # Limpar artefatos de markdown
            claim_text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", claim_text)
            claim_text = re.sub(r"^\s*[—–\-]\s*", "", claim_text)

            if len(claim_text) < 10:
                # Muito curto para ser um claim útil — pegar mais contexto
                # Usar frase inteira que contém a citação
                sentence_start = paragraph.rfind(".", 0, match.start())
                if sentence_start == -1:
                    sentence_start = 0
                else:
                    sentence_start += 1
                claim_text = paragraph[sentence_start:match.start()].strip()

            if len(claim_text) < 10:
                continue

            # Classificar
            claim_type = _classify_claim(claim_text)

            # Extrair data points se tipo A
            data_points = []
            has_calc = False
            if claim_type == ClaimType.A:
                data_points = _extract_data_points(claim_text)
                has_calc = _has_calculation_check(claim_text, data_points)

            claim = Claim(
                text=claim_text,
                ref_numbers=ref_numbers,
                claim_type=claim_type,
                data_points=data_points,
                has_calculation=has_calc,
                paragraph_context=paragraph,
                line_number=para_idx,
            )
            claims.append(claim)

    return claims


def _classify_claim(text: str) -> ClaimType:
    """Classifica um claim como A (quantitativo), B (qualitativo) ou C (conceitual).

    Tipo A requer dados numéricos SUBSTANTIVOS (percentagens, valores monetários,
    métricas). Números ordinais ("cinco características", "quatro níveis",
    "oito estágios") e anos não contam.
    """
    # Primeiro verificar se os "números" são apenas ordinais/contagens conceituais
    # "cinco características", "5 Vs", "quatro níveis", "oito estágios"
    conceptual_numbers = re.search(
        r"(?:cinco|quatro|três|seis|sete|oito|nove|dez)\s+"
        r"(?:características|níveis|estágios|camadas|princípios|frentes|domínios|etapas|tipos|Vs)|"
        r"\d\s+Vs\b",
        text, re.IGNORECASE,
    )

    # Tipo A: contém dados numéricos substantivos
    quant_match = _QUANT_INDICATORS.search(text)
    if quant_match and not conceptual_numbers:
        # Verificar se o match não é só um ano (2024, 2019, etc.)
        matched = quant_match.group(0).strip()
        if re.fullmatch(r"\d{4}", matched):
            pass  # É só um ano, não conta
        else:
            return ClaimType.A

    # Tipo B: menciona entidades específicas (métodos, modelos, instituições)
    if _ENTITY_INDICATORS.search(text):
        return ClaimType.B

    # Tipo C: conceitual/temático
    return ClaimType.C


def _extract_data_points(text: str) -> list[DataPoint]:
    """Extrai pontos de dados numéricos de um claim tipo A."""
    data_points: list[DataPoint] = []
    seen_values: set[str] = set()

    # Pattern 1: percentagens "85%", "6,2%", "65%"
    for m in re.finditer(r"(\d+(?:[,.]\d+)?)\s*%", text):
        raw = m.group(0)
        if raw in seen_values:
            continue
        seen_values.add(raw)
        value = _parse_pt_number(m.group(1))
        # Filtrar anos erroneamente capturados (ex: "em 2017 para 95,83% em 2024")
        if 1900 <= value <= 2099:
            continue
        data_points.append(DataPoint(
            raw_text=raw, value=value, unit="%", is_percentage=True,
        ))

    # Pattern 2: valores monetários "469 mil dólares", "3,4 bilhões"
    for m in re.finditer(
        r"(\d+(?:[,.]\d+)?)\s*(mil|milh[õo]es|bilh[õo]es|trilh[õo]es)\s*"
        r"(?:de\s+)?(dólares|reais|d[oó]lares)?",
        text,
    ):
        raw = m.group(0)
        if raw in seen_values:
            continue
        seen_values.add(raw)
        base_val = _parse_pt_number(m.group(1))
        multiplier_word = m.group(2).lower()
        multiplier = {
            "mil": 1_000,
            "milhões": 1_000_000, "milhoes": 1_000_000,
            "bilhões": 1_000_000_000, "bilhoes": 1_000_000_000,
            "trilhões": 1_000_000_000_000, "trilhoes": 1_000_000_000_000,
        }.get(multiplier_word, 1)
        value = base_val * multiplier
        currency = m.group(3) or ""
        unit = f"{multiplier_word} {currency}".strip() if currency else multiplier_word
        data_points.append(DataPoint(
            raw_text=raw, value=value, unit=unit, is_percentage=False,
        ))

    # Pattern 3: números simples com contexto "96 para 26 dias"
    for m in re.finditer(
        r"(\d+(?:[,.]\d+)?)\s*(?:para|→)\s*(\d+(?:[,.]\d+)?)\s*(dias|anos|meses|horas)?",
        text,
    ):
        raw = m.group(0)
        if raw in seen_values:
            continue
        v1 = _parse_pt_number(m.group(1))
        v2 = _parse_pt_number(m.group(2))
        # Filtrar anos (1900-2099)
        if 1900 <= v1 <= 2099 or 1900 <= v2 <= 2099:
            continue
        seen_values.add(raw)
        unit = m.group(3) or ""
        data_points.append(DataPoint(
            raw_text=f"{m.group(1)}", value=v1, unit=unit, is_percentage=False,
        ))
        data_points.append(DataPoint(
            raw_text=f"{m.group(2)}", value=v2, unit=unit, is_percentage=False,
        ))

    # Pattern 4: números isolados com unidade (ex: "2,8 bilhões de registros")
    # Já coberto acima em padrões 2 e 3; aqui pegamos sobras simples
    for m in re.finditer(r"(\d+(?:[,.]\d+)?)\s*(leitos|pacientes|contratos|registros)", text):
        raw = m.group(0)
        if raw in seen_values:
            continue
        seen_values.add(raw)
        value = _parse_pt_number(m.group(1))
        data_points.append(DataPoint(
            raw_text=raw, value=value, unit=m.group(2), is_percentage=False,
        ))

    return data_points


def _has_calculation_check(text: str, data_points: list[DataPoint]) -> bool:
    """Detecta se o claim contém valores absolutos + percentagem DERIVADA desses absolutos.

    Positivo: "reduziu de 96 para 26 dias — uma melhoria de 65%"
    → 96 e 26 são absolutos, 65% é derivada deles → True

    Negativo: "50 a 80 bilhões... crescimento anual de 11% a 19%"
    → 50B e 80B são um range, 11-19% são taxas independentes → False

    Negativo: "2,1 bi em 2024... 15,2 bi até 2032 — crescimento anual de 28%"
    → 28% é CAGR, não variação simples → False
    """
    percentages = [dp for dp in data_points if dp.is_percentage]
    absolutes = [dp for dp in data_points if not dp.is_percentage]

    if len(absolutes) < 2 or not percentages:
        return False

    # Excluir CAGR: "crescimento anual" indica taxa composta, não variação simples
    if re.search(r"crescimento\s+anual|taxa\s+anual|CAGR", text, re.IGNORECASE):
        return False

    # Excluir ranges independentes: "entre X% e Y%", "de X% a Y%"
    # Se as percentagens formam um range (conectadas por "e", "a", "entre"),
    # não são derivadas dos absolutos
    if len(percentages) >= 2:
        pct_values = sorted(dp.value for dp in percentages)
        if re.search(
            r"entre\s+\d+[,.]?\d*\s*%\s+e\s+\d+|"
            r"de\s+\d+[,.]?\d*\s*%\s+a\s+\d+",
            text, re.IGNORECASE,
        ):
            return False

    # Requer padrão explícito: "de X para Y" + percentagem próxima
    # (não apenas coexistência de absolutos e percentagens)
    has_transition = re.search(
        r"\d+\s*(?:para|→)\s*\d+", text,
    )
    has_derived_pct = re.search(
        r"(?:redu[çc]ão|melhor(?:ia|ou)|queda|varia[çc]ão|aument)\s+(?:de\s+)?\d+[,.]?\d*\s*%|"
        r"\d+\s*(?:para|→)\s*\d+.*?(?:—|–|-)\s*(?:uma?\s+)?(?:redu|melhor|queda|aument)\S*\s+(?:de\s+)?\d+[,.]?\d*\s*%",
        text, re.IGNORECASE,
    )

    return bool(has_transition and has_derived_pct)


def _parse_pt_number(s: str) -> float:
    """Converte número PT-BR para float.

    "6,2" → 6.2
    "469.000" → 469000.0
    "1.234,56" → 1234.56
    """
    s = s.strip()
    if "," in s and "." in s:
        # 1.234,56 → 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Pode ser decimal (6,2) ou milhar (1,000)
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            # Provavelmente milhar EN: 1,000
            s = s.replace(",", "")
        else:
            # Decimal PT: 6,2
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Geração de variantes numéricas PT↔EN
# ---------------------------------------------------------------------------

def _generate_number_variants(dp: DataPoint) -> list[str]:
    """Gera variantes de busca para um data point.

    Converte formato PT-BR → variantes EN e vice-versa.
    Retorna lista de strings para buscar no conteúdo da referência.
    """
    variants: list[str] = []
    val = dp.value

    if dp.is_percentage:
        # "85%" → ["85%", "85 %", "85 percent", "0.85"]
        if val == int(val):
            variants.append(f"{int(val)}%")
            variants.append(f"{int(val)} %")
            variants.append(f"{int(val)} percent")
        else:
            # 6.2% → "6.2%", "6.2 %"
            variants.append(f"{val}%")
            variants.append(f"{val} %")
            variants.append(f"{val} percent")
            # Com ponto e vírgula
            pt_str = str(val).replace(".", ",")
            variants.append(f"{pt_str}%")
        return variants

    # Valores monetários e grandes números
    if val >= 1_000_000_000_000:
        t = val / 1_000_000_000_000
        variants.extend(_format_large_number(t, "trillion", "trilhão", "trilhões"))
    elif val >= 1_000_000_000:
        b = val / 1_000_000_000
        variants.extend(_format_large_number(b, "billion", "bilhão", "bilhões"))
    elif val >= 1_000_000:
        m = val / 1_000_000
        variants.extend(_format_large_number(m, "million", "milhão", "milhões"))
    elif val >= 1_000:
        # $469,000 ou $469K ou 469 mil
        if val == int(val):
            iv = int(val)
            variants.append(f"{iv:,}")        # 469,000
            variants.append(f"{iv}")           # 469000
            variants.append(f"${iv:,}")        # $469,000
            variants.append(f"${iv}")          # $469000
            # Em mil
            if iv % 1000 == 0:
                k = iv // 1000
                variants.append(f"{k}K")
                variants.append(f"{k},000")
                variants.append(f"${k}K")
                variants.append(f"${k},000")
                variants.append(f"{k} mil")
                variants.append(f"{k} thousand")
        else:
            variants.append(f"{val:,.2f}")
            variants.append(f"${val:,.2f}")
    else:
        # Número simples
        if val == int(val):
            variants.append(str(int(val)))
        else:
            variants.append(str(val))
            # Variante PT com vírgula
            variants.append(str(val).replace(".", ","))

    # Variantes com $ e USD
    if "dólar" in dp.unit.lower() or "usd" in dp.unit.lower():
        base_variants = list(variants)
        for v in base_variants:
            if not v.startswith("$"):
                variants.append(f"${v}")
            variants.append(f"{v} USD")
            variants.append(f"US${v}")

    return list(dict.fromkeys(variants))  # Dedup preservando ordem


def _format_large_number(
    val: float, en_word: str, pt_singular: str, pt_plural: str
) -> list[str]:
    """Formata número grande em variantes PT e EN."""
    variants = []
    word_pt = pt_plural if val != 1 else pt_singular

    if val == int(val):
        iv = int(val)
        variants.extend([
            f"{iv} {en_word}",
            f"${iv} {en_word}",
            f"{iv} {word_pt}",
            f"{str(iv).replace('.', ',')} {word_pt}",
        ])
    else:
        en_str = f"{val:.1f}" if val * 10 == int(val * 10) else f"{val}"
        pt_str = en_str.replace(".", ",")
        variants.extend([
            f"{en_str} {en_word}",
            f"${en_str} {en_word}",
            f"{pt_str} {word_pt}",
            f"{en_str}",
            f"{pt_str}",
        ])

    return variants


# ---------------------------------------------------------------------------
# Resolução de referências
# ---------------------------------------------------------------------------

def resolve_references(
    parsed_refs: list[dict],
    project: str | None = None,
) -> dict[int, RefContent]:
    """Resolve conteúdo das referências por hierarquia de fontes.

    Hierarquia:
    1. pipeline/refs/*.md (cache local com full text)
    2. PubMed Central full text XML via efetch (se PMCID)
    3. Abstract PubMed via fetch_pubmed_metadata()
    4. Abstract CrossRef via get_crossref_metadata()
    5. Fallback: apenas título/raw da referência

    Args:
        parsed_refs: Lista de dicts de parse_vancouver_refs()
        project: Nome do projeto (para localizar pipeline/refs/)

    Returns:
        Dict ref_number → RefContent
    """
    from .config import get_api_config, get_project_dir

    api_config = get_api_config()
    email = api_config.get("NCBI_EMAIL", "")

    results: dict[int, RefContent] = {}
    total = len(parsed_refs)

    # Carregar cache se existir
    cache = _load_refs_cache(project) if project else {}

    for i, ref in enumerate(parsed_refs):
        num = ref["number"]
        doi = ref.get("doi", "")
        ref_type = ref.get("ref_type", "unknown")
        title = ref.get("title", "")
        print(f"  [{i + 1}/{total}] Resolvendo ref [{num}]...", end=" ")

        # Fontes web/legislação são UNVERIFIABLE pela Camada 1
        if ref_type in ("web", "legislation"):
            results[num] = RefContent(
                number=num,
                title=title,
                content=ref.get("raw", ""),
                source_level=SourceLevel.NONE,
                source_description=f"ref_type={ref_type} (não verificável automaticamente)",
                doi=doi,
                ref_type=ref_type,
            )
            print("→ UNVERIFIABLE (web/legislação)")
            continue

        # Verificar cache
        cache_key = doi if doi else f"ref_{num}"
        if cache_key in cache:
            cached = cache[cache_key]
            results[num] = RefContent(
                number=num,
                title=title,
                content=cached["content"],
                source_level=SourceLevel(cached["source_level"]),
                source_description=f"cache ({cached['source']})",
                doi=doi,
                ref_type=ref_type,
            )
            print(f"→ cache ({cached['source_level']})")
            continue

        # Nível 1: pipeline/refs/*.md
        content, source = _try_pipeline_refs(ref, project)
        if content:
            results[num] = RefContent(
                number=num, title=title, content=content,
                source_level=SourceLevel.FULL_TEXT,
                source_description=source, doi=doi, ref_type=ref_type,
            )
            _update_cache(cache, cache_key, content, "full_text", source)
            print(f"→ full text (pipeline)")
            continue

        # Nível 2: PubMed Central full text (se PMCID disponível)
        content, source = _try_pmc_fulltext(ref, email)
        if content:
            results[num] = RefContent(
                number=num, title=title, content=content,
                source_level=SourceLevel.FULL_TEXT,
                source_description=source, doi=doi, ref_type=ref_type,
            )
            _update_cache(cache, cache_key, content, "full_text", source)
            print(f"→ full text (PMC)")
            continue

        # Nível 3: Abstract PubMed
        content, source = _try_pubmed_abstract(ref, email)
        if content:
            results[num] = RefContent(
                number=num, title=title, content=content,
                source_level=SourceLevel.ABSTRACT,
                source_description=source, doi=doi, ref_type=ref_type,
            )
            _update_cache(cache, cache_key, content, "abstract", source)
            print(f"→ abstract (PubMed)")
            continue

        # Nível 4: CrossRef abstract
        content, source = _try_crossref_abstract(ref, email)
        if content:
            results[num] = RefContent(
                number=num, title=title, content=content,
                source_level=SourceLevel.ABSTRACT,
                source_description=source, doi=doi, ref_type=ref_type,
            )
            _update_cache(cache, cache_key, content, "abstract", source)
            print(f"→ abstract (CrossRef)")
            continue

        # Nível 5: Nada encontrado
        results[num] = RefContent(
            number=num, title=title, content=ref.get("raw", ""),
            source_level=SourceLevel.NONE,
            source_description="nenhum conteúdo disponível",
            doi=doi, ref_type=ref_type,
        )
        print("→ sem conteúdo")

    # Salvar cache atualizado
    if project:
        _save_refs_cache(cache, project)

    return results


def _try_pipeline_refs(ref: dict, project: str | None) -> tuple[str, str]:
    """Tenta encontrar conteúdo no pipeline/refs/*.md por DOI match."""
    if not project:
        return "", ""

    from .config import get_project_dir

    refs_dir = get_project_dir(project) / "pipeline" / "refs"
    if not refs_dir.exists():
        return "", ""

    doi = ref.get("doi", "")
    title = ref.get("title", "").lower()

    for md_file in refs_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Match por DOI
        if doi and doi.lower() in content.lower():
            return content, f"pipeline/refs/{md_file.name}"

        # Match por título (primeiras 40 chars, case-insensitive)
        if title and len(title) > 20:
            first_line = content.split("\n")[0].lower()
            if title[:40] in first_line:
                return content, f"pipeline/refs/{md_file.name}"

    return "", ""


def _try_pmc_fulltext(ref: dict, email: str) -> tuple[str, str]:
    """Tenta obter full text via PubMed Central."""
    if not email:
        return "", ""

    # Precisamos de PMID para buscar PMCID
    doi = ref.get("doi", "")
    if not doi:
        return "", ""

    try:
        from Bio import Entrez
        Entrez.email = email

        # Buscar PMID pelo DOI
        handle = Entrez.esearch(db="pubmed", term=f"{doi}[doi]", retmax=1)
        result = Entrez.read(handle)
        handle.close()
        ids = result.get("IdList", [])
        if not ids:
            return "", ""

        pmid = ids[0]
        time.sleep(0.34)

        # Buscar PMCID via elink
        handle = Entrez.elink(dbfrom="pubmed", db="pmc", id=pmid)
        link_result = Entrez.read(handle)
        handle.close()
        time.sleep(0.34)

        pmcid = None
        for linkset in link_result:
            for linksetdb in linkset.get("LinkSetDb", []):
                if linksetdb.get("DbTo") == "pmc":
                    links = linksetdb.get("Link", [])
                    if links:
                        pmcid = links[0]["Id"]
                        break

        if not pmcid:
            return "", ""

        # Fetch full text XML
        handle = Entrez.efetch(db="pmc", id=pmcid, rettype="xml")
        xml_text = handle.read()
        handle.close()
        time.sleep(0.34)

        # Extrair texto plano do XML (simplificado)
        if isinstance(xml_text, bytes):
            xml_text = xml_text.decode("utf-8", errors="replace")

        # Remover tags XML, manter texto
        plain = re.sub(r"<[^>]+>", " ", xml_text)
        plain = re.sub(r"\s+", " ", plain).strip()

        if len(plain) > 500:
            return plain, f"PMC full text (PMCID: {pmcid})"

    except Exception:
        pass

    return "", ""


def _try_pubmed_abstract(ref: dict, email: str) -> tuple[str, str]:
    """Tenta obter abstract via PubMed."""
    if not email:
        return "", ""

    doi = ref.get("doi", "")
    if not doi:
        return "", ""

    try:
        from .apis.pubmed import fetch_pubmed_metadata
        meta = fetch_pubmed_metadata(doi=doi, email=email)
        if meta and meta.get("abstract"):
            return meta["abstract"], "PubMed abstract"
    except Exception:
        pass

    return "", ""


def _try_crossref_abstract(ref: dict, email: str) -> tuple[str, str]:
    """Tenta obter abstract via CrossRef."""
    doi = ref.get("doi", "")
    if not doi:
        return "", ""

    try:
        from .apis.crossref import get_crossref_metadata
        cr_email = ""
        try:
            from .config import get_api_config
            cr_email = get_api_config().get("CROSSREF_EMAIL", "")
        except Exception:
            pass

        meta = get_crossref_metadata(doi, email=cr_email)
        if meta:
            abstract = meta.get("abstract", "")
            if abstract:
                # CrossRef abstracts podem ter tags JATS
                abstract = re.sub(r"<[^>]+>", "", abstract)
                return abstract, "CrossRef abstract"
    except Exception:
        pass

    return "", ""


# ---------------------------------------------------------------------------
# Cache de referências
# ---------------------------------------------------------------------------

def _get_cache_path(project: str) -> Path:
    """Retorna caminho do cache de referências."""
    from .config import get_project_dir
    return get_project_dir(project) / ".refs-cache.json"


def _load_refs_cache(project: str) -> dict:
    """Carrega cache de referências do disco."""
    path = _get_cache_path(project)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_refs_cache(cache: dict, project: str) -> None:
    """Salva cache de referências no disco."""
    path = _get_cache_path(project)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_cache(
    cache: dict, key: str, content: str, source_level: str, source: str
) -> None:
    """Atualiza uma entrada no cache."""
    # Limitar tamanho do conteúdo no cache (max 50KB por entrada)
    if len(content) > 50_000:
        content = content[:50_000]
    cache[key] = {
        "content": content,
        "source_level": source_level,
        "source": source,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Verificação de claims
# ---------------------------------------------------------------------------

def verify_claims(
    claims: list[Claim],
    refs: dict[int, RefContent],
) -> list[ClaimResult]:
    """Verifica cada claim contra suas referências.

    Tipo A: busca numérica com variantes PT↔EN
    Tipo B: busca de entidades com extração de contexto
    Tipo C: sobreposição temática por keywords
    """
    results: list[ClaimResult] = []
    total = len(claims)

    for i, claim in enumerate(claims):
        ref_nums_str = ",".join(str(n) for n in claim.ref_numbers)
        print(f"  [{i + 1}/{total}] Verificando claim [{ref_nums_str}]: "
              f"{claim.text[:60]}...")

        if claim.claim_type == ClaimType.A:
            result = _verify_type_a(claim, refs)
        elif claim.claim_type == ClaimType.B:
            result = _verify_type_b(claim, refs)
        else:
            result = _verify_type_c(claim, refs)

        results.append(result)

    return results


def _verify_type_a(claim: Claim, refs: dict[int, RefContent]) -> ClaimResult:
    """Verifica claim quantitativo (Tipo A).

    1. Gera variantes numéricas para cada data_point
    2. Busca em cada ref citada
    3. Se encontrado: VERIFIED
    4. Se valor próximo (±30%): APPROXIMATE_MATCH
    5. Se tem CALCULATION_CHECK: verifica aritmética
    6. Se não encontrado: NOT_FOUND_* conforme nível da fonte
    """
    # Verificar cálculos primeiro
    if claim.has_calculation:
        math_result = _verify_math(claim)
        if math_result:
            return math_result

    if not claim.data_points:
        # Sem data points extraídos — tratar como tipo B
        return _verify_type_b(claim, refs)

    # Para cada data point, buscar em todas as refs citadas
    all_ref_contents: list[tuple[int, RefContent]] = []
    for rn in claim.ref_numbers:
        if rn in refs:
            all_ref_contents.append((rn, refs[rn]))

    if not all_ref_contents:
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.UNVERIFIABLE,
            fabrication_risk=FabricationRisk.UNKNOWN,
            details="Nenhuma referência citada foi resolvida.",
        )

    # Verificar se todas as refs são UNVERIFIABLE
    verifiable_refs = [
        (rn, rc) for rn, rc in all_ref_contents
        if rc.source_level != SourceLevel.NONE
    ]

    if not verifiable_refs:
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.UNVERIFIABLE,
            fabrication_risk=FabricationRisk.UNKNOWN,
            details=f"Refs [{','.join(str(r[0]) for r in all_ref_contents)}] "
                    "sem conteúdo disponível.",
        )

    # Buscar cada data point
    best_status = VerificationStatus.NOT_FOUND_ALL_REFS
    best_details = ""
    best_matched_ref = None
    best_matched_text = ""
    best_context = ""
    best_source_level = SourceLevel.NONE
    best_approx_value = None

    for dp in claim.data_points:
        variants = _generate_number_variants(dp)
        dp_found = False

        for rn, rc in verifiable_refs:
            clean_content = _strip_metadata_for_search(rc.content)
            content_lower = clean_content.lower()

            # Busca exata
            for variant in variants:
                pos = content_lower.find(variant.lower())
                if pos != -1:
                    context = _extract_context(clean_content, pos, len(variant))
                    best_status = VerificationStatus.VERIFIED
                    best_details = (
                        f"Dado '{dp.raw_text}' encontrado como '{variant}' "
                        f"na ref [{rn}] ({rc.source_level.value})."
                    )
                    best_matched_ref = rn
                    best_matched_text = variant
                    best_context = context
                    best_source_level = rc.source_level
                    dp_found = True
                    break

            if dp_found:
                break

            # Busca aproximada (±30%) se não encontrado exato
            if not dp_found and dp.value > 0:
                approx = _search_approximate(dp, clean_content)
                if approx:
                    found_val, found_text, context = approx
                    if best_status not in (
                        VerificationStatus.VERIFIED,
                        VerificationStatus.APPROXIMATE_MATCH,
                    ):
                        best_status = VerificationStatus.APPROXIMATE_MATCH
                        diff_pct = abs(found_val - dp.value) / dp.value * 100
                        best_details = (
                            f"Valor similar encontrado: '{found_text}' "
                            f"(diff {diff_pct:.0f}%) na ref [{rn}]. "
                            f"Texto diz '{dp.raw_text}'. "
                            "Verificar fonte primária."
                        )
                        best_matched_ref = rn
                        best_matched_text = found_text
                        best_context = context
                        best_source_level = rc.source_level
                        best_approx_value = found_val

        if dp_found:
            break  # Pelo menos um data point verificado

    # Se nada encontrado, determinar NOT_FOUND_* baseado no nível da fonte
    if best_status in (
        VerificationStatus.NOT_FOUND_ALL_REFS,
        VerificationStatus.NOT_FOUND_FULLTEXT,
        VerificationStatus.NOT_FOUND_ABSTRACT,
    ):
        has_fulltext = any(
            rc.source_level == SourceLevel.FULL_TEXT for _, rc in verifiable_refs
        )
        has_abstract = any(
            rc.source_level == SourceLevel.ABSTRACT for _, rc in verifiable_refs
        )
        dp_desc = ", ".join(f"'{dp.raw_text}'" for dp in claim.data_points)

        if has_fulltext:
            best_status = VerificationStatus.NOT_FOUND_FULLTEXT
            best_details = (
                f"Dados {dp_desc} NÃO encontrados no full text das refs "
                f"[{','.join(str(rn) for rn, _ in verifiable_refs)}]."
            )
        elif has_abstract:
            best_status = VerificationStatus.NOT_FOUND_ABSTRACT
            best_details = (
                f"Dados {dp_desc} não encontrados nos abstracts das refs "
                f"[{','.join(str(rn) for rn, _ in verifiable_refs)}]. "
                "Dado pode estar no corpo do artigo (não disponível)."
            )
        else:
            best_status = VerificationStatus.NOT_FOUND_ALL_REFS
            best_details = (
                f"Dados {dp_desc} não encontrados em nenhuma referência."
            )

        best_source_level = (
            SourceLevel.FULL_TEXT if has_fulltext
            else SourceLevel.ABSTRACT if has_abstract
            else SourceLevel.NONE
        )

    risk = STATUS_RISK_MAP.get(best_status, FabricationRisk.UNKNOWN)

    return ClaimResult(
        claim=claim,
        status=best_status,
        fabrication_risk=risk,
        details=best_details,
        matched_ref=best_matched_ref,
        matched_text=best_matched_text,
        context=best_context,
        source_level=best_source_level,
        approximate_value=best_approx_value,
    )


def _verify_type_b(claim: Claim, refs: dict[int, RefContent]) -> ClaimResult:
    """Verifica claim qualitativo (Tipo B) por entity matching.

    Busca entidades específicas (métodos, modelos, instituições)
    e extrai contexto ±150 chars para julgamento na Camada 2.
    """
    # Extrair entidades do claim
    entities = _extract_entities(claim.text)
    if not entities:
        # Fallback para tipo C
        return _verify_type_c(claim, refs)

    verifiable_refs = [
        (rn, refs[rn]) for rn in claim.ref_numbers
        if rn in refs and refs[rn].source_level != SourceLevel.NONE
    ]

    if not verifiable_refs:
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.UNVERIFIABLE,
            fabrication_risk=FabricationRisk.UNKNOWN,
            details="Refs sem conteúdo disponível.",
        )

    # Buscar cada entidade nas refs
    found_entities: list[tuple[str, int, str]] = []  # (entity, ref_num, context)

    for entity in entities:
        for rn, rc in verifiable_refs:
            pos = rc.content.lower().find(entity.lower())
            if pos != -1:
                context = _extract_context(rc.content, pos, len(entity), window=150)
                found_entities.append((entity, rn, context))
                break  # Encontrou em pelo menos uma ref

    if found_entities:
        entities_desc = "; ".join(
            f"'{e}' em ref [{rn}]" for e, rn, _ in found_entities
        )
        first_context = found_entities[0][2]
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.ENTITY_FOUND,
            fabrication_risk=FabricationRisk.LOW_MEDIUM,
            details=f"Entidades encontradas: {entities_desc}. "
                    "Julgamento semântico requer Camada 2.",
            matched_ref=found_entities[0][1],
            matched_text=found_entities[0][0],
            context=first_context,
            source_level=verifiable_refs[0][1].source_level,
        )

    # Não encontrado
    has_fulltext = any(
        rc.source_level == SourceLevel.FULL_TEXT for _, rc in verifiable_refs
    )
    status = (
        VerificationStatus.NOT_FOUND_FULLTEXT if has_fulltext
        else VerificationStatus.NOT_FOUND_ABSTRACT
    )

    return ClaimResult(
        claim=claim,
        status=status,
        fabrication_risk=STATUS_RISK_MAP.get(status, FabricationRisk.MEDIUM),
        details=f"Entidades {entities} não encontradas nas refs "
                f"[{','.join(str(rn) for rn, _ in verifiable_refs)}].",
        source_level=(
            SourceLevel.FULL_TEXT if has_fulltext else SourceLevel.ABSTRACT
        ),
    )


def _verify_type_c(claim: Claim, refs: dict[int, RefContent]) -> ClaimResult:
    """Verifica claim conceitual (Tipo C) por sobreposição temática."""
    verifiable_refs = [
        (rn, refs[rn]) for rn in claim.ref_numbers
        if rn in refs and refs[rn].source_level != SourceLevel.NONE
    ]

    if not verifiable_refs:
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.UNVERIFIABLE,
            fabrication_risk=FabricationRisk.UNKNOWN,
            details="Refs sem conteúdo disponível.",
        )

    # Extrair keywords do claim
    claim_keywords = _extract_keywords(claim.text)
    if not claim_keywords:
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.THEMATIC_MATCH,
            fabrication_risk=FabricationRisk.NONE,
            details="Claim conceitual sem keywords específicas para verificar.",
        )

    # Verificar sobreposição temática
    best_overlap = 0
    best_ref = None

    for rn, rc in verifiable_refs:
        content_lower = rc.content.lower()
        matches = sum(1 for kw in claim_keywords if kw.lower() in content_lower)
        overlap = matches / len(claim_keywords)
        if overlap > best_overlap:
            best_overlap = overlap
            best_ref = rn

    if best_overlap >= 0.3:
        matched_kws = [
            kw for kw in claim_keywords
            if kw.lower() in refs[best_ref].content.lower()
        ]
        return ClaimResult(
            claim=claim,
            status=VerificationStatus.THEMATIC_MATCH,
            fabrication_risk=FabricationRisk.NONE,
            details=f"Sobreposição temática {best_overlap:.0%} com ref [{best_ref}]. "
                    f"Keywords encontradas: {', '.join(matched_kws)}.",
            matched_ref=best_ref,
            source_level=refs[best_ref].source_level,
        )

    return ClaimResult(
        claim=claim,
        status=VerificationStatus.NOT_FOUND_ABSTRACT,
        fabrication_risk=FabricationRisk.MEDIUM,
        details=f"Sobreposição temática baixa ({best_overlap:.0%}) com todas as refs.",
    )


# ---------------------------------------------------------------------------
# Funções auxiliares de verificação
# ---------------------------------------------------------------------------

def _verify_math(claim: Claim) -> ClaimResult | None:
    """Verifica consistência aritmética em claims com CALCULATION_CHECK.

    Ex: "reduziu de 96 para 26 dias — melhoria de 65%"
    → Cálculo: (96-26)/96 = 72.9% ≠ 65% → MATH_ERROR
    """
    percentages = [dp for dp in claim.data_points if dp.is_percentage]
    absolutes = [dp for dp in claim.data_points if not dp.is_percentage]

    if len(absolutes) < 2 or not percentages:
        return None

    # Filtrar absolutos: ignorar valores que parecem anos (1900-2099)
    real_absolutes = [
        dp for dp in absolutes
        if not (1900 <= dp.value <= 2099 and dp.unit in ("", "%"))
    ]
    if len(real_absolutes) < 2:
        return None

    # Pegar os dois valores do pattern "X para Y" se possível
    transition_match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(?:para|→)\s*(\d+(?:[,.]\d+)?)",
        claim.text,
    )
    if transition_match:
        v_from = _parse_pt_number(transition_match.group(1))
        v_to = _parse_pt_number(transition_match.group(2))
        bigger = max(v_from, v_to)
        smaller = min(v_from, v_to)
    else:
        vals = sorted([dp.value for dp in real_absolutes], reverse=True)
        bigger, smaller = vals[0], vals[1]

    if bigger == 0:
        return None

    # Calcular variação percentual
    reduction_pct = (bigger - smaller) / bigger * 100
    increase_pct = (bigger - smaller) / smaller * 100 if smaller > 0 else 0

    for pct_dp in percentages:
        claimed_pct = pct_dp.value

        # Verificar se bate com redução
        if abs(reduction_pct - claimed_pct) <= 2:
            return None  # OK, bate
        if abs(increase_pct - claimed_pct) <= 2:
            return None  # OK, bate como aumento

        # Verificar tolerância mais ampla (±5 pontos percentuais)
        if abs(reduction_pct - claimed_pct) <= 5 or abs(increase_pct - claimed_pct) <= 5:
            return None  # Próximo o suficiente (arredondamento)

        # MATH_ERROR
        if abs(reduction_pct - claimed_pct) < abs(increase_pct - claimed_pct):
            expected = reduction_pct
            calc_type = "redução"
        else:
            expected = increase_pct
            calc_type = "aumento"

        return ClaimResult(
            claim=claim,
            status=VerificationStatus.MATH_ERROR,
            fabrication_risk=FabricationRisk.HIGH,
            details=(
                f"Inconsistência aritmética: texto diz {claimed_pct}% de {calc_type}, "
                f"mas ({bigger}−{smaller})/{bigger} = {expected:.1f}%. "
                f"Diferença de {abs(expected - claimed_pct):.1f} pontos percentuais."
            ),
            math_expected=f"{expected:.1f}%",
            math_found=f"{claimed_pct}%",
        )

    return None


def _search_approximate(
    dp: DataPoint, content: str, tolerance: float = 0.30
) -> tuple[float, str, str] | None:
    """Busca valor aproximado (±tolerance) no conteúdo.

    Filtra números em contextos irrelevantes (URLs, DOIs, metadados markdown,
    números de citação interna como [15, 39, 69]).

    Retorna (valor_encontrado, texto_encontrado, contexto) ou None.
    """
    target = dp.value
    if target == 0:
        return None

    lower_bound = target * (1 - tolerance)
    upper_bound = target * (1 + tolerance)

    # Limpar conteúdo: remover metadados markdown do pipeline e URLs
    clean_content = _strip_metadata_for_search(content)

    # Buscar números no conteúdo limpo
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)", clean_content):
        try:
            num_str = m.group(1)
            pos = m.start()

            # Filtrar contextos irrelevantes
            if _is_noise_number(clean_content, pos, num_str):
                continue

            # Tentar parse como EN e PT
            num_val = None
            for parse_str in [num_str, num_str.replace(",", "")]:
                try:
                    num_val = float(parse_str.replace(",", "."))
                    break
                except ValueError:
                    continue

            if num_val is None:
                continue

            # Ajustar por multiplicadores próximos
            for multiplied in [num_val, num_val * 1000, num_val * 1_000_000]:
                if lower_bound <= multiplied <= upper_bound:
                    context = _extract_context(clean_content, pos, len(num_str))
                    # Pegar contexto legível
                    start = max(0, pos - 30)
                    end = min(len(clean_content), m.end() + 40)
                    found_text = clean_content[start:end].strip()
                    return multiplied, found_text, context

        except (ValueError, ZeroDivisionError):
            continue

    return None


def _strip_metadata_for_search(content: str) -> str:
    """Remove metadados markdown do pipeline que geram matches espúrios.

    Remove: cabeçalho com Link/DOI/Autores/Grade, URLs, referências internas [N].
    """
    lines = content.split("\n")
    clean_lines = []
    in_header = True  # Pular metadados iniciais do pipeline ref

    for line in lines:
        # Pular linhas de metadados do pipeline (Link, Autores, Grade, Eixos, Acesso)
        if in_header:
            if line.startswith("**Link:**") or line.startswith("**Autores:"):
                continue
            if line.startswith("**Grade:") or line.startswith("**Eixos:"):
                continue
            if line.startswith("**Acesso:"):
                continue
            if line.startswith("---"):
                in_header = False
                continue
            if line.startswith("# "):
                # Título — manter mas marcar fim do header
                in_header = True
                clean_lines.append(line)
                continue
        # Remover URLs inline
        cleaned = re.sub(r"https?://\S+", "", line)
        # Remover DOIs inline
        cleaned = re.sub(r"10\.\d{4,}/\S+", "", cleaned)
        clean_lines.append(cleaned)

    return "\n".join(clean_lines)


def _is_noise_number(content: str, pos: int, num_str: str) -> bool:
    """Verifica se um número está em contexto irrelevante.

    Filtra: citações internas [N,M], anos isolados, números de página,
    IDs de referência, etc.
    """
    # Contexto ao redor (±15 chars)
    start = max(0, pos - 15)
    end = min(len(content), pos + len(num_str) + 15)
    ctx = content[start:end]

    # Número dentro de colchetes: [15, 39, 69] — citação interna
    if re.search(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", ctx):
        return True
    # Número precedido por [ e seguido por , ou ]
    if re.search(r"\[[\d,\s–-]*" + re.escape(num_str) + r"[\d,\s–-]*\]", ctx):
        return True

    # Ano isolado (1900-2099) — geralmente não é um dado verificável
    try:
        val = float(num_str.replace(",", "."))
        if 1900 <= val <= 2099 and "." not in num_str and "," not in num_str:
            return True
    except ValueError:
        pass

    return False


def _extract_entities(text: str) -> list[str]:
    """Extrai entidades específicas de um claim tipo B."""
    entities = []

    # Nomes de modelos/algoritmos
    for m in re.finditer(
        r"(XGBoost|Random Forest|LSTM|CNN|RNN|SVM|GBM|"
        r"neural network|rede neural|NLP|GPT|LLM|"
        r"deep learning|machine learning|"
        r"regressão logística|logistic regression)",
        text, re.IGNORECASE,
    ):
        entities.append(m.group(0))

    # Siglas e organizações
    for m in re.finditer(
        r"(FHIR|HL7|TISS|LGPD|AMAM|HIMSS|OMS|WHO|"
        r"DATASUS|ANS|SUS|RNDS|DRG|"
        r"digital twin|gêmeos? digitais)",
        text, re.IGNORECASE,
    ):
        entities.append(m.group(0))

    # Termos técnicos compostos (2+ palavras capitalizadas)
    for m in re.finditer(r"([A-Z][a-z]+ (?:[A-Z][a-z]+ )*[A-Z][a-z]+)", text):
        term = m.group(0)
        if len(term) > 5 and term not in ("Big Data",):
            entities.append(term)

    return list(dict.fromkeys(entities))  # Dedup


def _extract_keywords(text: str) -> list[str]:
    """Extrai keywords relevantes de um claim tipo C.

    Prioriza termos compostos (bigramas/trigramas) sobre palavras isoladas,
    e inclui traduções EN dos termos-chave para buscar em conteúdo em inglês.
    """
    # Stop words expandida PT/EN + palavras genéricas acadêmicas
    stop_words = {
        # PT
        "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
        "um", "uma", "uns", "umas", "o", "a", "os", "as", "e", "ou",
        "que", "para", "por", "com", "como", "mais", "não", "se", "é",
        "são", "foi", "ser", "ter", "pode", "the", "of", "in", "and",
        "to", "for", "is", "are", "was", "with", "that", "from", "this",
        "also", "can", "has", "have", "been", "its", "than", "entre",
        "sobre", "pela", "pelo", "cada", "todo", "toda", "todos",
        "ainda", "já", "bem", "muito", "outros", "outras", "qual",
        # Genéricas acadêmicas (geram falsos positivos)
        "sistema", "sistemas", "dados", "uso", "tipo", "forma", "base",
        "caso", "casos", "parte", "área", "meio", "setor", "papel",
        "primeiro", "segundo", "exemplo", "permite", "inclui", "incluem",
        "chamados", "chamada", "também", "além", "assim", "sendo",
        "geral", "real", "maior", "menor", "novo", "nova", "novos",
    }

    keywords: list[str] = []

    # 1. Termos compostos PT↔EN (prioridade máxima)
    compound_terms = {
        # PT → EN (busca ambos)
        "big data": ["big data"],
        "inteligência artificial": ["artificial intelligence", "AI"],
        "machine learning": ["machine learning", "ML"],
        "deep learning": ["deep learning"],
        "gestão hospitalar": ["hospital management", "healthcare management"],
        "gestão financeira": ["financial management"],
        "gestão de leitos": ["bed management"],
        "cadeia de suprimentos": ["supply chain"],
        "supply chain": ["supply chain"],
        "centro cirúrgico": ["operating room", "surgical"],
        "gêmeos digitais": ["digital twin"],
        "digital twin": ["digital twin"],
        "análise preditiva": ["predictive analytics", "predictive"],
        "análise prescritiva": ["prescriptive analytics"],
        "fraude": ["fraud"],
        "fraudes em saúde": ["healthcare fraud"],
        "maturidade analítica": ["analytics maturity", "maturity"],
        "interoperabilidade": ["interoperability"],
        "governança de dados": ["data governance"],
        "prontuário eletrônico": ["electronic health record", "EHR"],
        "viés algorítmico": ["algorithmic bias", "bias"],
        "supervisão humana": ["human oversight", "human supervision"],
        "IA generativa": ["generative AI", "LLM"],
        "proteção de dados": ["data protection", "privacy"],
        "saúde suplementar": ["health insurance", "supplementary health"],
        "faturamento": ["billing"],
        "readmissão": ["readmission"],
        "superlotação": ["overcrowding"],
        "deterioração clínica": ["clinical deterioration"],
    }

    for pt_term, en_variants in compound_terms.items():
        if pt_term.lower() in text.lower():
            keywords.append(pt_term)
            keywords.extend(en_variants)

    # 2. Palavras significativas (≥5 chars, filtradas por stop words)
    words = re.findall(r"[a-záàâãéêíóôõúçA-Z]{5,}", text)
    for w in words:
        if w.lower() not in stop_words and w not in keywords:
            keywords.append(w)

    return list(dict.fromkeys(keywords))


def _extract_context(
    content: str, pos: int, match_len: int, window: int = 100
) -> str:
    """Extrai contexto ao redor de um match."""
    start = max(0, pos - window)
    end = min(len(content), pos + match_len + window)
    ctx = content[start:end]
    if start > 0:
        ctx = "..." + ctx
    if end < len(content):
        ctx = ctx + "..."
    return ctx.replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# Relatório — dual output .md + .json
# ---------------------------------------------------------------------------

def write_report(
    results: list[ClaimResult],
    output_base: Path,
) -> tuple[Path, Path]:
    """Gera relatório dual: .claims-report.md + .claims-data.json.

    Args:
        results: Lista de ClaimResult
        output_base: Caminho base (ex: capitulo16.md → capitulo16)

    Returns:
        Tupla (path_md, path_json)
    """
    md_path = output_base.with_suffix(".claims-report.md")
    json_path = output_base.with_suffix(".claims-data.json")

    # --- JSON ---
    json_data = _results_to_json(results)
    json_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- Markdown ---
    md_content = _results_to_markdown(results, json_data)
    md_path.write_text(md_content, encoding="utf-8")

    return md_path, json_path


def _results_to_json(results: list[ClaimResult]) -> dict:
    """Converte resultados para JSON serializável."""
    # Contar por status
    status_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for r in results:
        s = r.status.value
        status_counts[s] = status_counts.get(s, 0) + 1
        rk = r.fabrication_risk.value
        risk_counts[rk] = risk_counts.get(rk, 0) + 1

    claims_data = []
    for r in results:
        claims_data.append({
            "claim_text": r.claim.text,
            "claim_type": r.claim.claim_type.value,
            "ref_numbers": r.claim.ref_numbers,
            "data_points": [
                {
                    "raw_text": dp.raw_text,
                    "value": dp.value,
                    "unit": dp.unit,
                    "is_percentage": dp.is_percentage,
                }
                for dp in r.claim.data_points
            ],
            "has_calculation": r.claim.has_calculation,
            "status": r.status.value,
            "fabrication_risk": r.fabrication_risk.value,
            "details": r.details,
            "matched_ref": r.matched_ref,
            "matched_text": r.matched_text,
            "context": r.context,
            "source_level": r.source_level.value,
            "approximate_value": r.approximate_value,
            "math_expected": r.math_expected,
            "math_found": r.math_found,
            # Para Camada 2
            "layer2_verdict": None,
            "layer2_notes": "",
        })

    return {
        "metadata": {
            "total_claims": len(results),
            "status_counts": status_counts,
            "risk_counts": risk_counts,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "claims": claims_data,
    }


def _results_to_markdown(results: list[ClaimResult], json_data: dict) -> str:
    """Gera relatório Markdown formatado."""
    lines: list[str] = []
    meta = json_data["metadata"]

    lines.append("# Relatório de Verificação de Claims\n")
    lines.append(f"**Data:** {meta['timestamp']}")
    lines.append(f"**Total de claims:** {meta['total_claims']}\n")

    # Resumo
    lines.append("## Resumo\n")
    lines.append("| Status | Contagem | Risco |")
    lines.append("|--------|----------|-------|")

    status_order = [
        ("MATH_ERROR", "HIGH"),
        ("NOT_FOUND_ALL_REFS", "CRITICAL"),
        ("NOT_FOUND_FULLTEXT", "HIGH"),
        ("NOT_FOUND_ABSTRACT", "MEDIUM"),
        ("APPROXIMATE_MATCH", "LOW"),
        ("ENTITY_FOUND", "LOW-MEDIUM"),
        ("THEMATIC_MATCH", "NONE"),
        ("VERIFIED", "NONE"),
        ("UNVERIFIABLE", "UNKNOWN"),
    ]

    for status, risk in status_order:
        count = meta["status_counts"].get(status, 0)
        if count > 0:
            lines.append(f"| {status} | {count} | {risk} |")

    lines.append("")

    # Claims agrupados por risco
    risk_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW-MEDIUM", "LOW", "NONE", "UNKNOWN"]

    for risk_level in risk_order:
        risk_claims = [r for r in results if r.fabrication_risk.value == risk_level]
        if not risk_claims:
            continue

        lines.append(f"\n## Risco {risk_level}\n")

        for r in risk_claims:
            ref_str = ",".join(str(n) for n in r.claim.ref_numbers)
            lines.append(f"### [{ref_str}] {r.status.value}\n")
            lines.append(f"**Claim:** {r.claim.text}\n")
            lines.append(f"**Tipo:** {r.claim.claim_type.value}")
            lines.append(f"**Status:** {r.status.value}")
            lines.append(f"**Risco:** {r.fabrication_risk.value}\n")

            if r.claim.data_points:
                dp_str = ", ".join(
                    f"{dp.raw_text} (={dp.value}{dp.unit})"
                    for dp in r.claim.data_points
                )
                lines.append(f"**Data points:** {dp_str}\n")

            lines.append(f"**Detalhes:** {r.details}\n")

            if r.matched_text:
                lines.append(f"**Match:** `{r.matched_text}`")
            if r.context:
                lines.append(f"\n> {r.context}\n")
            if r.math_expected:
                lines.append(
                    f"**Esperado:** {r.math_expected} | "
                    f"**Encontrado no texto:** {r.math_found}\n"
                )

            lines.append("---\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Função principal (entry point)
# ---------------------------------------------------------------------------

def run_verify_claims(
    filepath: str | Path,
    project: str | None = None,
    output: str | Path | None = None,
) -> list[ClaimResult]:
    """Executa verificação completa de claims em um arquivo .md.

    1. Lê o arquivo e extrai claims
    2. Parseia referências Vancouver
    3. Resolve conteúdo das referências
    4. Verifica cada claim
    5. Gera relatório dual .md + .json

    Args:
        filepath: Caminho do arquivo .md a verificar
        project: Nome do projeto (para pipeline/refs/)
        output: Caminho base para outputs (default: mesmo dir do arquivo)

    Returns:
        Lista de ClaimResult
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {filepath}")

    print("=" * 60)
    print(f"Verificação de Claims — {filepath.name}")
    print("=" * 60)

    # 1. Ler arquivo
    text = filepath.read_text(encoding="utf-8")

    # 2. Extrair claims
    print("\n[1/4] Extraindo claims do texto...")
    claims = extract_claims(text)
    type_a = sum(1 for c in claims if c.claim_type == ClaimType.A)
    type_b = sum(1 for c in claims if c.claim_type == ClaimType.B)
    type_c = sum(1 for c in claims if c.claim_type == ClaimType.C)
    print(f"  {len(claims)} claims encontrados: {type_a} Tipo A, "
          f"{type_b} Tipo B, {type_c} Tipo C")

    # 3. Parsear e resolver referências
    print("\n[2/4] Parseando referências Vancouver...")
    from .verify_markdown_refs import parse_vancouver_refs
    parsed_refs = parse_vancouver_refs(text)
    print(f"  {len(parsed_refs)} referências encontradas")

    print("\n[3/4] Resolvendo conteúdo das referências...")
    ref_contents = resolve_references(parsed_refs, project=project)
    full_text_count = sum(
        1 for rc in ref_contents.values()
        if rc.source_level == SourceLevel.FULL_TEXT
    )
    abstract_count = sum(
        1 for rc in ref_contents.values()
        if rc.source_level == SourceLevel.ABSTRACT
    )
    none_count = sum(
        1 for rc in ref_contents.values()
        if rc.source_level == SourceLevel.NONE
    )
    print(f"  Resolvidas: {full_text_count} full text, "
          f"{abstract_count} abstract, {none_count} sem conteúdo")

    # 4. Verificar claims
    print("\n[4/4] Verificando claims...")
    results = verify_claims(claims, ref_contents)

    # 5. Gerar relatório
    if output:
        output_base = Path(output)
    else:
        output_base = filepath

    md_path, json_path = write_report(results, output_base)

    # Resumo final
    print("\n" + "=" * 60)
    print("Verificação concluída!\n")

    risk_summary: dict[str, int] = {}
    for r in results:
        rk = r.fabrication_risk.value
        risk_summary[rk] = risk_summary.get(rk, 0) + 1

    for risk in ["CRITICAL", "HIGH", "MEDIUM", "LOW-MEDIUM", "LOW", "NONE", "UNKNOWN"]:
        count = risk_summary.get(risk, 0)
        if count > 0:
            marker = "!!!" if risk in ("CRITICAL", "HIGH") else ""
            print(f"  {risk}: {count} {marker}")

    print(f"\nRelatório: {md_path}")
    print(f"Dados:     {json_path}")
    print("=" * 60)

    return results
