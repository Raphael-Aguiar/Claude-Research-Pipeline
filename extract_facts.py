"""Facts Registry — extração preventiva de fatos verificados das referências.

Camada PRÉ-ESCRITA: extrai fatos das referências ANTES de escrever,
criando um registro estruturado como "single source of truth".

Complementa o verify_claims.py (camada CORRETIVA pós-escrita).

Uso:
    python -m tools extract-facts "Livro Editora Atheneu" --refs 11-17
    python -m tools facts-import "Livro Editora Atheneu"
    python -m tools facts-status "Livro Editora Atheneu" --section 16.3
    python -m tools facts-report "Livro Editora Atheneu"
    python -m tools facts-crosscheck "Livro Editora Atheneu"
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from rapidfuzz import fuzz


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FactType(Enum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"
    CONCEPTUAL = "conceptual"


class FactStatus(Enum):
    VERIFIED = "verified"                    # Citação exata confirmada + valor cruzado
    CONTEXT_SUPPORTED = "context_supported"  # Qualitativo — contexto confirma
    DERIVED = "derived"                      # Calculado + derivação verificada
    UNVERIFIABLE_SOURCE = "unverifiable"     # Fonte web/legislação inacessível
    VERIFIED_WEB = "verified_web"            # Verificado via WebFetch
    PENDING = "pending"                      # Aguardando validação
    STALE = "stale"                          # Ref mudou — precisa re-extrair
    REJECTED = "rejected"                    # Não encontrado ou rejeitado


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NumberCandidate:
    """Número extraído automaticamente do texto de uma ref."""
    value: float
    raw_text: str           # "3,000", "$400,000", "85%"
    context: str            # ±150 chars ao redor
    position: int           # Posição no texto
    category: str           # "percentage", "currency", "count", "metric"


@dataclass
class ExtractedFact:
    """Um fato extraído com evidência."""
    id: str                           # UUID curto 8 chars
    ref_number: int
    ref_doi: str
    ref_title: str
    ref_content_hash: str             # SHA256[:16] do conteúdo normalizado

    fact_type: FactType
    status: FactStatus

    # O fato em si
    statement: str                    # Afirmação concisa em PT-BR
    exact_quote: str                  # Citação EXATA do texto fonte
    quote_context: str                # ±200 chars ao redor

    # Quantitativos
    value: float | None = None
    unit: str = ""
    original_format: str = ""         # Formato original na fonte

    # Derivados
    derivation_formula: str = ""      # "percentage_reduction", "cagr", "sum"
    source_values: list[float] = field(default_factory=list)
    calculated_result: float | None = None
    stated_result: float | None = None

    # Mapeamento
    sections: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Auditoria
    extracted_by: str = "claude"
    extracted_at: str = ""
    source_level: str = ""
    cross_validation_detail: str = ""
    web_verified_at: str = ""
    notes: str = ""


@dataclass
class FactsRegistry:
    project: str
    canonical_file: str
    facts: list[ExtractedFact] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    refs_processed: list[int] = field(default_factory=list)
    ref_hashes: dict[int, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regras de status por tipo de fato
# ---------------------------------------------------------------------------

VALID_STATUS_BY_TYPE: dict[FactType, set[FactStatus]] = {
    FactType.QUANTITATIVE: {
        FactStatus.VERIFIED, FactStatus.DERIVED,
        FactStatus.VERIFIED_WEB, FactStatus.UNVERIFIABLE_SOURCE,
    },
    FactType.QUALITATIVE: {
        FactStatus.VERIFIED, FactStatus.CONTEXT_SUPPORTED,
        FactStatus.VERIFIED_WEB, FactStatus.UNVERIFIABLE_SOURCE,
    },
    FactType.CONCEPTUAL: {
        FactStatus.VERIFIED, FactStatus.CONTEXT_SUPPORTED,
        FactStatus.VERIFIED_WEB, FactStatus.UNVERIFIABLE_SOURCE,
    },
}


# ---------------------------------------------------------------------------
# Fórmulas de derivação
# ---------------------------------------------------------------------------

DERIVATION_FORMULAS: dict[str, callable] = {
    "percentage_reduction": lambda vals: ((vals[0] - vals[1]) / vals[0]) * 100,
    "percentage_increase": lambda vals: ((vals[1] - vals[0]) / vals[0]) * 100,
    "cagr": lambda vals: ((vals[1] / vals[0]) ** (1 / vals[2]) - 1) * 100,
    "ratio": lambda vals: vals[0] / vals[1],
    "difference": lambda vals: vals[0] - vals[1],
    "sum": lambda vals: sum(vals),
}


# ---------------------------------------------------------------------------
# Serialização
# ---------------------------------------------------------------------------

def _fact_to_dict(fact: ExtractedFact) -> dict:
    """Converte ExtractedFact para dict serializável."""
    return {
        "id": fact.id,
        "ref_number": fact.ref_number,
        "ref_doi": fact.ref_doi,
        "ref_title": fact.ref_title,
        "ref_content_hash": fact.ref_content_hash,
        "fact_type": fact.fact_type.value,
        "status": fact.status.value,
        "statement": fact.statement,
        "exact_quote": fact.exact_quote,
        "quote_context": fact.quote_context,
        "value": fact.value,
        "unit": fact.unit,
        "original_format": fact.original_format,
        "derivation_formula": fact.derivation_formula,
        "source_values": fact.source_values,
        "calculated_result": fact.calculated_result,
        "stated_result": fact.stated_result,
        "sections": fact.sections,
        "tags": fact.tags,
        "extracted_by": fact.extracted_by,
        "extracted_at": fact.extracted_at,
        "source_level": fact.source_level,
        "cross_validation_detail": fact.cross_validation_detail,
        "web_verified_at": fact.web_verified_at,
        "notes": fact.notes,
    }


def _fact_from_dict(d: dict) -> ExtractedFact:
    """Reconstrói ExtractedFact a partir de dict."""
    return ExtractedFact(
        id=d["id"],
        ref_number=d["ref_number"],
        ref_doi=d.get("ref_doi", ""),
        ref_title=d.get("ref_title", ""),
        ref_content_hash=d.get("ref_content_hash", ""),
        fact_type=FactType(d["fact_type"]),
        status=FactStatus(d["status"]),
        statement=d["statement"],
        exact_quote=d["exact_quote"],
        quote_context=d.get("quote_context", ""),
        value=d.get("value"),
        unit=d.get("unit", ""),
        original_format=d.get("original_format", ""),
        derivation_formula=d.get("derivation_formula", ""),
        source_values=d.get("source_values", []),
        calculated_result=d.get("calculated_result"),
        stated_result=d.get("stated_result"),
        sections=d.get("sections", []),
        tags=d.get("tags", []),
        extracted_by=d.get("extracted_by", "claude"),
        extracted_at=d.get("extracted_at", ""),
        source_level=d.get("source_level", ""),
        cross_validation_detail=d.get("cross_validation_detail", ""),
        web_verified_at=d.get("web_verified_at", ""),
        notes=d.get("notes", ""),
    )


def _registry_to_json(registry: FactsRegistry) -> dict:
    """Converte FactsRegistry para dict serializável."""
    return {
        "project": registry.project,
        "canonical_file": registry.canonical_file,
        "created_at": registry.created_at,
        "updated_at": registry.updated_at,
        "refs_processed": registry.refs_processed,
        "ref_hashes": {str(k): v for k, v in registry.ref_hashes.items()},
        "facts": [_fact_to_dict(f) for f in registry.facts],
    }


def _registry_from_json(data: dict) -> FactsRegistry:
    """Reconstrói FactsRegistry a partir de dict."""
    reg = FactsRegistry(
        project=data["project"],
        canonical_file=data.get("canonical_file", ""),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        refs_processed=data.get("refs_processed", []),
        ref_hashes={int(k): v for k, v in data.get("ref_hashes", {}).items()},
    )
    reg.facts = [_fact_from_dict(f) for f in data.get("facts", [])]
    return reg


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------

def _get_registry_path(project: str) -> Path:
    """Retorna caminho do .facts-registry.json."""
    from .config import get_project_dir
    return get_project_dir(project) / ".facts-registry.json"


def _get_pending_path(project: str) -> Path:
    """Retorna caminho do .facts-pending.jsonl."""
    from .config import get_project_dir
    return get_project_dir(project) / ".facts-pending.jsonl"


def _get_candidates_path(project: str) -> Path:
    """Retorna caminho do .facts-candidates.md."""
    from .config import get_project_dir
    return get_project_dir(project) / ".facts-candidates.md"


def load_registry(project: str) -> FactsRegistry:
    """Carrega registry do disco ou cria um novo."""
    path = _get_registry_path(project)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return _registry_from_json(data)
    return FactsRegistry(
        project=project,
        canonical_file="",
        created_at=_now_iso(),
    )


def save_registry(registry: FactsRegistry, backup: bool = True) -> Path:
    """Salva registry no disco. Cria backup se já existir."""
    path = _get_registry_path(registry.project)
    if backup and path.exists():
        bak = path.with_suffix(".json.bak")
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    registry.updated_at = _now_iso()
    path.write_text(
        json.dumps(_registry_to_json(registry), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def add_fact(registry: FactsRegistry, fact: ExtractedFact) -> bool:
    """Adiciona fato com dedup dual. Retorna True se adicionado."""
    # Dedup para quantitativos: (ref_number, value, unit)
    if fact.fact_type == FactType.QUANTITATIVE and fact.value is not None:
        for existing in registry.facts:
            if (
                existing.ref_number == fact.ref_number
                and existing.value is not None
                and existing.unit == fact.unit
                and abs(existing.value - fact.value) < 0.001
            ):
                return False

    # Dedup para qualitativos/conceituais: fuzzy match em exact_quote
    if fact.fact_type in (FactType.QUALITATIVE, FactType.CONCEPTUAL):
        for existing in registry.facts:
            if existing.ref_number != fact.ref_number:
                continue
            if _quotes_match(existing.exact_quote, fact.exact_quote):
                return False

    registry.facts.append(fact)
    return True


def find_facts(
    registry: FactsRegistry,
    *,
    ref_number: int | None = None,
    section: str | None = None,
    tags: list[str] | None = None,
    fact_type: FactType | None = None,
) -> list[ExtractedFact]:
    """Busca fatos no registry por filtros."""
    results = registry.facts
    if ref_number is not None:
        results = [f for f in results if f.ref_number == ref_number]
    if section is not None:
        results = [f for f in results if section in f.sections]
    if tags:
        tag_set = set(tags)
        results = [f for f in results if tag_set & set(f.tags)]
    if fact_type is not None:
        results = [f for f in results if f.fact_type == fact_type]
    return results


# ---------------------------------------------------------------------------
# Hash e staleness
# ---------------------------------------------------------------------------

def _normalize_content_for_hash(content: str) -> str:
    """Normaliza conteúdo antes de hashear (resolve D1).

    Strip, lowercase, remover whitespace duplicado, remover cabeçalho markdown.
    """
    text = content.strip().lower()
    # Remover cabeçalho do pipeline (linhas antes do primeiro ---)
    parts = text.split("---", 1)
    if len(parts) > 1:
        text = parts[1]
    # Colapsar whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compute_ref_hash(content: str) -> str:
    """Computa SHA256[:16] do conteúdo normalizado da ref."""
    normalized = _normalize_content_for_hash(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def check_registry_staleness(
    registry: FactsRegistry,
    refs: dict[int, object],
) -> list[tuple[int, str]]:
    """Compara hashes das refs no registry com conteúdo atual.

    Args:
        registry: Registry carregado
        refs: Dict ref_number → RefContent (do verify_claims.resolve_references)

    Returns:
        Lista de (ref_number, reason) para refs com conteúdo alterado
    """
    stale: list[tuple[int, str]] = []
    for ref_num, stored_hash in registry.ref_hashes.items():
        ref = refs.get(ref_num)
        if ref is None:
            continue
        current_hash = _compute_ref_hash(ref.content)
        if current_hash != stored_hash:
            stale.append((ref_num, f"Hash mudou: {stored_hash} → {current_hash}"))
            # Marcar fatos como STALE
            for fact in registry.facts:
                if fact.ref_number == ref_num and fact.status != FactStatus.REJECTED:
                    fact.status = FactStatus.STALE
                    fact.notes = (
                        f"Ref [{ref_num}] alterada — re-extrair. "
                        f"Hash anterior: {stored_hash}"
                    )
    return stale


# ---------------------------------------------------------------------------
# Extração automática de números (Camada 1)
# ---------------------------------------------------------------------------

# Regex para números com contexto monetário/percentual
_NUMBER_RE = re.compile(
    r"(?:(?:US?\$|R\$|\$)\s*)"           # Prefixo monetário opcional
    r"(\d[\d,. ]*\d|\d)"                  # Número principal
    r"(?:\s*(?:%)?"                        # Sufixo percentual
    r"(?:\s*(?:million|billion|trillion|thousand|"  # Sufixos de grandeza EN
    r"mil|milh[õo]es|bilh[õo]es|trilh[õo]es))?"   # Sufixos PT
    r")?",
    re.IGNORECASE,
)

# Regex mais agressivo para capturar qualquer número significativo
_ANY_NUMBER_RE = re.compile(
    r"(?:(?:US?\$|R\$|\$)\s*)?"
    r"(\d[\d,.']*\d|\d)"
    r"(?:\s*%)?",
)


def _parse_number_value(raw: str, context: str) -> float | None:
    """Parseia string numérica para float, considerando formato EN e PT."""
    s = raw.strip().replace(" ", "").replace("'", "")

    # Determinar se vírgula é separador de milhar ou decimal
    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        # Ex: "1,234.56" ou "1.234,56"
        if s.rindex(",") > s.rindex("."):
            # PT: "1.234,56" → "1234.56"
            s = s.replace(".", "").replace(",", ".")
        else:
            # EN: "1,234.56" → "1234.56"
            s = s.replace(",", "")
    elif has_comma:
        # Pode ser "3,000" (milhar EN) ou "6,2" (decimal PT)
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            # Provável separador de milhar: "3,000"
            s = s.replace(",", "")
        else:
            # Provável decimal PT: "6,2"
            s = s.replace(",", ".")
    # has_dot only: normal float

    try:
        val = float(s)
    except ValueError:
        return None

    # Aplicar multiplicador do contexto
    ctx_lower = context.lower()
    if re.search(r"\b(trillion|trilh[õo]es)\b", ctx_lower):
        val *= 1_000_000_000_000
    elif re.search(r"\b(billion|bilh[õo]es)\b", ctx_lower):
        val *= 1_000_000_000
    elif re.search(r"\b(million|milh[õo]es)\b", ctx_lower):
        val *= 1_000_000
    elif re.search(r"\b(thousand|mil)\b", ctx_lower):
        # Cuidado: "mil" pode ser sufixo ("469 mil") ou parte de palavra
        if re.search(r"\d\s+mil\b", ctx_lower):
            val *= 1_000

    return val


def _classify_number(raw_text: str, context: str) -> str:
    """Classifica um número em categoria."""
    if "%" in context[max(0, len(context)//2 - 20):len(context)//2 + 20]:
        return "percentage"
    if re.search(r"US?\$|R\$|\$|dólar|dollar|USD|BRL", context, re.IGNORECASE):
        return "currency"
    # Unidades de medida comuns
    if re.search(
        r"(minutes?|hours?|days?|weeks?|years?|months?|"
        r"minutos?|horas?|dias?|semanas?|anos?|meses?|"
        r"patients?|pacientes?|beds?|leitos?|"
        r"min/week|per week|per year|por semana|por ano)",
        context, re.IGNORECASE,
    ):
        return "metric"
    return "count"


def _extract_all_numbers(content: str) -> list[NumberCandidate]:
    """Extrai TODOS os números relevantes de um texto, filtrando ruído.

    Reutiliza lógica de _strip_metadata_for_search e _is_noise_number
    do verify_claims.py.
    """
    from .verify_claims import _strip_metadata_for_search, _is_noise_number

    # Limpar conteúdo
    cleaned = _strip_metadata_for_search(content)

    candidates: list[NumberCandidate] = []
    seen_positions: set[int] = set()

    # Buscar números com regex
    for m in _ANY_NUMBER_RE.finditer(cleaned):
        pos = m.start()
        if pos in seen_positions:
            continue

        num_str = m.group(1)
        full_match = m.group(0)

        # Filtrar ruído
        if _is_noise_number(cleaned, pos, num_str):
            continue

        # Filtrar números muito curtos (1 dígito isolado, exceto se % ou $)
        if len(num_str) == 1 and "%" not in full_match and "$" not in full_match:
            continue

        # Extrair contexto ±150 chars
        ctx_start = max(0, pos - 150)
        ctx_end = min(len(cleaned), pos + len(full_match) + 150)
        context = cleaned[ctx_start:ctx_end]

        # Parsear valor
        value = _parse_number_value(num_str, context)
        if value is None:
            continue

        # Filtrar valores que são provavelmente ruído (0, 1, etc.)
        if value == 0:
            continue

        category = _classify_number(full_match, context)

        candidates.append(NumberCandidate(
            value=value,
            raw_text=full_match.strip(),
            context=context.strip(),
            position=pos,
            category=category,
        ))
        seen_positions.add(pos)

    return candidates


# ---------------------------------------------------------------------------
# Geração de relatório de candidatos (Camada 1 output)
# ---------------------------------------------------------------------------

def _generate_candidates_report(
    ref_candidates: dict[int, tuple[object, list[NumberCandidate]]],
) -> str:
    """Gera .facts-candidates.md legível para revisão pelo Claude.

    Args:
        ref_candidates: Dict ref_num → (RefContent, list[NumberCandidate])
    """
    lines = [
        "# Candidatos a Fatos — Extração Automática (Camada 1)",
        "",
        f"Gerado em: {_now_iso()}",
        "",
        "---",
        "",
    ]

    total_candidates = 0

    for ref_num in sorted(ref_candidates.keys()):
        ref, candidates = ref_candidates[ref_num]
        lines.append(f"## Ref [{ref_num}] — {ref.title} [{ref.source_level.value}]")
        lines.append("")

        if not candidates:
            lines.append("*Nenhum candidato quantitativo extraído.*")
            lines.append("")
            continue

        for i, cand in enumerate(candidates, 1):
            total_candidates += 1
            lines.append(f"### CANDIDATO {i}: {cand.raw_text}")
            lines.append(f"  Contexto: \"...{cand.context}...\"")
            lines.append(f"  Tipo provável: {cand.category}")
            lines.append(f"  Valor: {cand.value}")

            # Detectar inconsistências no contexto
            _add_context_warnings(lines, cand)

            lines.append("")

        lines.append("---")
        lines.append("")

    lines.insert(5, f"Total de candidatos: {total_candidates}")
    lines.insert(6, "")

    return "\n".join(lines)


def _add_context_warnings(lines: list[str], cand: NumberCandidate) -> None:
    """Adiciona avisos se o contexto sugere inconsistência."""
    ctx = cand.context.lower()
    # Detectar "exceeding X" quando o valor extraído pode diferir
    if "exceeding" in ctx or "more than" in ctx or "over" in ctx:
        lines.append(
            f"  \u26a0\ufe0f NOTA: contexto sugere valor aproximado "
            f"(\"exceeding\"/\"more than\"/\"over\")"
        )
    if "approximately" in ctx or "about" in ctx or "cerca de" in ctx:
        lines.append(
            f"  \u26a0\ufe0f NOTA: contexto indica aproximação"
        )


# ---------------------------------------------------------------------------
# Validação de citações (híbrido: substring + fuzzy)
# ---------------------------------------------------------------------------

def validate_quote_in_source(
    quote: str,
    content: str,
) -> tuple[bool, str]:
    """Verifica se a citação existe no conteúdo da ref.

    Abordagem híbrida por tamanho (resolve Q4):
    - Citações curtas (< 40 chars): substring match case-insensitive
    - Citações longas (>= 40 chars): rapidfuzz.fuzz.partial_ratio >= 93
    """
    if not quote or not content:
        return False, "Citação ou conteúdo vazio"

    from .verify_claims import _strip_metadata_for_search

    cleaned = _strip_metadata_for_search(content)
    quote_clean = quote.strip()

    if len(quote_clean) < 40:
        # Substring match case-insensitive
        if quote_clean.lower() in cleaned.lower():
            return True, f"Substring match (case-insensitive, {len(quote_clean)} chars)"
        return False, f"Substring NÃO encontrada ({len(quote_clean)} chars)"
    else:
        # Fuzzy match
        score = fuzz.partial_ratio(quote_clean.lower(), cleaned.lower()) / 100.0
        if score >= 0.93:
            return True, f"Fuzzy match (partial_ratio={score:.3f}, threshold=0.93)"
        return False, f"Fuzzy match FALHOU (partial_ratio={score:.3f}, threshold=0.93)"


# ---------------------------------------------------------------------------
# Validação cruzada de números
# ---------------------------------------------------------------------------

def _cross_validate_value(
    fact_value: float,
    fact_unit: str,
    ref_numbers: list[NumberCandidate],
    tolerance: float = 0.05,
) -> tuple[bool, str]:
    """Verifica se o valor do fato aparece nos números extraídos da ref.

    Tolerance 5% cobre arredondamento (84.7% → 85%) mas rejeita
    fabricação (400K → 469K = 17.3%).

    Returns:
        (encontrado, detalhe)
    """
    if not ref_numbers:
        return False, "Nenhum número extraído da referência para comparação"

    # Buscar match exato ou dentro da tolerância
    closest_value = None
    closest_diff = float("inf")

    for cand in ref_numbers:
        if fact_value == 0:
            if cand.value == 0:
                return True, f"Valor 0 encontrado na posição {cand.position}"
            continue

        diff = abs(cand.value - fact_value) / abs(fact_value)
        if diff < closest_diff:
            closest_diff = diff
            closest_value = cand

        if diff <= tolerance:
            return True, (
                f"Valor {fact_value} encontrado (ref: {cand.value}, "
                f"diff: {diff*100:.1f}%, posição {cand.position})"
            )

    # Não encontrado — reportar mais próximo
    if closest_value is not None:
        return False, (
            f"Valor {fact_value} NÃO encontrado. "
            f"Mais próximo: {closest_value.value} "
            f"(diff {closest_diff*100:.1f}%, posição {closest_value.position})"
        )
    return False, f"Valor {fact_value} NÃO encontrado — nenhum número na ref"


# ---------------------------------------------------------------------------
# Verificação de derivados
# ---------------------------------------------------------------------------

def _verify_derivation(fact: ExtractedFact) -> tuple[bool, str]:
    """Calcula resultado a partir dos source_values e compara com stated_result.

    Exemplo: source_values=[96,26], formula="percentage_reduction"
    → calculated=72.9%, stated=65% → MISMATCH
    """
    formula = fact.derivation_formula
    if formula not in DERIVATION_FORMULAS:
        return False, f"Fórmula desconhecida: {formula}"

    if not fact.source_values:
        return False, "source_values vazio"

    if fact.stated_result is None:
        return False, "stated_result não definido"

    try:
        func = DERIVATION_FORMULAS[formula]
        calculated = func(fact.source_values)
    except (ZeroDivisionError, IndexError, ValueError) as e:
        return False, f"Erro no cálculo: {e}"

    fact.calculated_result = round(calculated, 4)

    # Tolerância de 1% para arredondamento
    if abs(calculated - fact.stated_result) <= max(abs(calculated) * 0.01, 0.1):
        return True, (
            f"Derivação verificada: {formula}({fact.source_values}) = "
            f"{calculated:.2f}, afirmado: {fact.stated_result}"
        )

    return False, (
        f"MISMATCH: {formula}({fact.source_values}) = {calculated:.2f}, "
        f"afirmado: {fact.stated_result}"
    )


# ---------------------------------------------------------------------------
# Import de .facts-pending.jsonl
# ---------------------------------------------------------------------------

def import_pending_facts(project: str) -> dict:
    """Importa fatos do .facts-pending.jsonl para o registry.

    Processa linha a linha com try/except (resolve A1).
    Backup automático do registry (resolve W3).
    Limpa pending após sucesso.

    Returns:
        Dict com estatísticas: imported, rejected, invalid, details
    """
    from .verify_claims import resolve_references, _strip_metadata_for_search
    from .verify_markdown_refs import parse_vancouver_refs
    from .config import get_project_dir

    pending_path = _get_pending_path(project)
    if not pending_path.exists():
        return {"imported": 0, "rejected": 0, "invalid": 0,
                "details": ["Nenhum .facts-pending.jsonl encontrado"]}

    # Carregar registry (com backup automático)
    registry = load_registry(project)

    # Ler linhas do JSONL
    lines = pending_path.read_text(encoding="utf-8").strip().split("\n")
    if not lines or (len(lines) == 1 and not lines[0].strip()):
        return {"imported": 0, "rejected": 0, "invalid": 0,
                "details": [".facts-pending.jsonl está vazio"]}

    stats = {"imported": 0, "rejected": 0, "invalid": 0, "details": []}

    # Resolver refs necessárias para validação cruzada
    ref_numbers_needed = set()
    parsed_facts: list[tuple[int, dict]] = []

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            parsed_facts.append((line_num, data))
            ref_numbers_needed.add(data.get("ref_number", 0))
        except json.JSONDecodeError as e:
            stats["invalid"] += 1
            stats["details"].append(f"Linha {line_num}: JSON inválido — {e}")

    if not parsed_facts:
        return stats

    # Resolver conteúdo das refs para validação cruzada
    # Tentar carregar canonical file para parsear refs
    project_dir = get_project_dir(project)
    ref_contents: dict[int, object] = {}
    ref_numbers_cache: dict[int, list[NumberCandidate]] = {}

    # Buscar arquivo canônico para obter refs
    canonical_files = list(project_dir.glob("capitulo*.md")) + list(project_dir.glob("artigo*.md"))
    if canonical_files:
        canonical_text = canonical_files[0].read_text(encoding="utf-8")
        parsed_refs = parse_vancouver_refs(canonical_text)
        if parsed_refs:
            needed_refs = [r for r in parsed_refs if r["number"] in ref_numbers_needed]
            if needed_refs:
                ref_contents = resolve_references(needed_refs, project=project)

    # Extrair números de cada ref para cross-validation
    for ref_num, ref in ref_contents.items():
        if ref.content:
            ref_numbers_cache[ref_num] = _extract_all_numbers(ref.content)

    # Processar cada fato
    for line_num, data in parsed_facts:
        try:
            fact = _pending_to_fact(data, ref_contents, ref_numbers_cache)
            if fact.status == FactStatus.REJECTED:
                stats["rejected"] += 1
                stats["details"].append(
                    f"Linha {line_num}: REJEITADO — {fact.notes}"
                )
            elif add_fact(registry, fact):
                stats["imported"] += 1
                # Atualizar ref_hashes e refs_processed
                if fact.ref_number not in registry.refs_processed:
                    registry.refs_processed.append(fact.ref_number)
                ref = ref_contents.get(fact.ref_number)
                if ref and ref.content:
                    registry.ref_hashes[fact.ref_number] = _compute_ref_hash(
                        ref.content
                    )
            else:
                stats["rejected"] += 1
                stats["details"].append(
                    f"Linha {line_num}: Duplicata — ref [{data.get('ref_number')}] "
                    f"value={data.get('value')}"
                )
        except Exception as e:
            stats["invalid"] += 1
            stats["details"].append(f"Linha {line_num}: Erro — {e}")

    # Salvar registry
    save_registry(registry, backup=True)

    # Limpar pending
    pending_path.unlink()

    return stats


def _pending_to_fact(
    data: dict,
    ref_contents: dict[int, object],
    ref_numbers_cache: dict[int, list[NumberCandidate]],
) -> ExtractedFact:
    """Converte um dict do JSONL pending em ExtractedFact validado."""
    ref_num = data["ref_number"]
    fact_type = FactType(data.get("fact_type", "quantitative"))

    # Dados da ref
    ref = ref_contents.get(ref_num)
    ref_doi = ref.doi if ref else data.get("ref_doi", "")
    ref_title = ref.title if ref else data.get("ref_title", "")
    ref_hash = _compute_ref_hash(ref.content) if ref and ref.content else ""

    # Criar fato
    fact = ExtractedFact(
        id=_generate_id(),
        ref_number=ref_num,
        ref_doi=ref_doi,
        ref_title=ref_title,
        ref_content_hash=ref_hash,
        fact_type=fact_type,
        status=FactStatus.PENDING,
        statement=data.get("statement", ""),
        exact_quote=data.get("exact_quote", ""),
        quote_context=data.get("quote_context", ""),
        value=data.get("value"),
        unit=data.get("unit", ""),
        original_format=data.get("original_format", ""),
        derivation_formula=data.get("derivation_formula", ""),
        source_values=data.get("source_values", []),
        stated_result=data.get("stated_result"),
        sections=data.get("sections", []),
        tags=data.get("tags", []),
        extracted_by=data.get("extracted_by", "claude"),
        extracted_at=_now_iso(),
        source_level=ref.source_level.value if ref else "",
        notes=data.get("notes", ""),
    )

    # Validação de citação
    if ref and ref.content and fact.exact_quote:
        quote_ok, quote_detail = validate_quote_in_source(
            fact.exact_quote, ref.content,
        )
        if not quote_ok:
            fact.status = FactStatus.REJECTED
            fact.notes = f"Citação não encontrada na ref: {quote_detail}"
            return fact

    # Validação cruzada para quantitativos
    if fact_type == FactType.QUANTITATIVE and fact.value is not None:
        ref_nums = ref_numbers_cache.get(ref_num, [])
        if ref_nums:
            val_ok, val_detail = _cross_validate_value(
                fact.value, fact.unit, ref_nums,
            )
            fact.cross_validation_detail = val_detail
            if not val_ok:
                fact.status = FactStatus.REJECTED
                fact.notes = f"Validação cruzada falhou: {val_detail}"
                return fact

    # Verificação de derivados
    if fact.derivation_formula and fact.source_values and fact.stated_result is not None:
        deriv_ok, deriv_detail = _verify_derivation(fact)
        fact.cross_validation_detail = deriv_detail
        if not deriv_ok:
            fact.status = FactStatus.REJECTED
            fact.notes = f"Derivação falhou: {deriv_detail}"
            return fact
        fact.status = FactStatus.DERIVED
        return fact

    # Determinar status final
    if fact_type == FactType.QUANTITATIVE:
        fact.status = FactStatus.VERIFIED
    else:
        # Qualitativo/conceitual: VERIFIED se citação exata, CONTEXT_SUPPORTED caso contrário
        if ref and ref.content and fact.exact_quote:
            quote_ok, _ = validate_quote_in_source(fact.exact_quote, ref.content)
            fact.status = FactStatus.VERIFIED if quote_ok else FactStatus.CONTEXT_SUPPORTED
        else:
            fact.status = FactStatus.CONTEXT_SUPPORTED

    # Override para fontes web/legislação
    if ref and ref.ref_type in ("web", "legislation"):
        if fact.status == FactStatus.VERIFIED:
            fact.status = FactStatus.VERIFIED_WEB
        elif fact.status not in (FactStatus.REJECTED, FactStatus.DERIVED):
            fact.status = FactStatus.UNVERIFIABLE_SOURCE

    return fact


# ---------------------------------------------------------------------------
# Cross-check claims vs registry (resolve A2)
# ---------------------------------------------------------------------------

def cross_check_claims_vs_registry(
    claims_data_path: Path,
    registry: FactsRegistry,
) -> list[dict]:
    """Cruza output do verify-claims (.claims-data.json) com o Facts Registry.

    Para cada claim quantitativo: busca fatos correspondentes por
    ref_number + valor ±5%.

    Returns:
        Lista de dicts com: claim_text, ref_number, value, status, detail
    """
    if not claims_data_path.exists():
        return [{"error": f"Arquivo não encontrado: {claims_data_path}"}]

    data = json.loads(claims_data_path.read_text(encoding="utf-8"))
    claims = data.get("claims", [])

    results: list[dict] = []

    for claim in claims:
        claim_type = claim.get("claim_type", "")
        if claim_type != "quantitative":
            continue

        ref_numbers = claim.get("ref_numbers", [])
        data_points = claim.get("data_points", [])

        for dp in data_points:
            value = dp.get("value")
            if value is None:
                continue

            # Buscar no registry
            found = False
            for ref_num in ref_numbers:
                matching = find_facts(
                    registry, ref_number=ref_num,
                    fact_type=FactType.QUANTITATIVE,
                )
                for fact in matching:
                    if fact.value is not None and abs(value) > 0:
                        diff = abs(fact.value - value) / abs(value)
                        if diff <= 0.05:
                            found = True
                            results.append({
                                "claim_text": claim.get("text", "")[:100],
                                "ref_number": ref_num,
                                "value": value,
                                "status": "REGISTRADO",
                                "detail": f"fact_id={fact.id}, "
                                          f"registry_value={fact.value}",
                            })
                            break
                if found:
                    break

            if not found:
                results.append({
                    "claim_text": claim.get("text", "")[:100],
                    "ref_number": ref_numbers[0] if ref_numbers else None,
                    "value": value,
                    "status": "NAO_REGISTRADO",
                    "detail": f"\u26a0\ufe0f DADO NÃO REGISTRADO: "
                              f"{value} {dp.get('unit', '')} "
                              f"não está no Facts Registry",
                })

    return results


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------

def generate_facts_report(registry: FactsRegistry) -> str:
    """Gera relatório markdown completo do registry."""
    lines = [
        f"# Facts Registry — {registry.project}",
        "",
        f"Atualizado em: {registry.updated_at}",
        f"Total de fatos: {len(registry.facts)}",
        f"Refs processadas: {sorted(registry.refs_processed)}",
        "",
    ]

    # Estatísticas por status
    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for f in registry.facts:
        status_counts[f.status.value] = status_counts.get(f.status.value, 0) + 1
        type_counts[f.fact_type.value] = type_counts.get(f.fact_type.value, 0) + 1

    lines.append("## Estatísticas")
    lines.append("")
    lines.append("| Status | Quantidade |")
    lines.append("|--------|-----------|")
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.append("")
    lines.append("| Tipo | Quantidade |")
    lines.append("|------|-----------|")
    for ftype, count in sorted(type_counts.items()):
        lines.append(f"| {ftype} | {count} |")
    lines.append("")

    # Fatos por ref
    lines.append("## Fatos por Referência")
    lines.append("")

    facts_by_ref: dict[int, list[ExtractedFact]] = {}
    for f in registry.facts:
        facts_by_ref.setdefault(f.ref_number, []).append(f)

    for ref_num in sorted(facts_by_ref.keys()):
        facts = facts_by_ref[ref_num]
        lines.append(f"### Ref [{ref_num}] — {facts[0].ref_title}")
        lines.append("")
        for f in facts:
            status_icon = _status_icon(f.status)
            if f.fact_type == FactType.QUANTITATIVE:
                lines.append(
                    f"- {status_icon} **{f.value} {f.unit}** — {f.statement}"
                )
            else:
                lines.append(f"- {status_icon} {f.statement}")
            if f.cross_validation_detail:
                lines.append(f"  - Validação: {f.cross_validation_detail}")
            if f.notes:
                lines.append(f"  - Nota: {f.notes}")
        lines.append("")

    return "\n".join(lines)


def generate_section_brief(
    registry: FactsRegistry,
    section: str,
) -> str:
    """Gera section brief conciso (~2-5 KB) para uso durante escrita (resolve W2)."""
    facts = find_facts(registry, section=section)

    # Se não há fatos mapeados para a seção, mostrar todos
    if not facts:
        facts = registry.facts

    lines = [
        f"# Facts Brief — Seção {section}",
        "",
        f"Fatos disponíveis: {len(facts)}",
        "",
    ]

    # Agrupar por ref
    by_ref: dict[int, list[ExtractedFact]] = {}
    for f in facts:
        by_ref.setdefault(f.ref_number, []).append(f)

    for ref_num in sorted(by_ref.keys()):
        ref_facts = by_ref[ref_num]
        lines.append(f"### Ref [{ref_num}]")
        for f in ref_facts:
            status_icon = _status_icon(f.status)
            if f.fact_type == FactType.QUANTITATIVE:
                lines.append(
                    f"- {status_icon} {f.value} {f.unit} — {f.statement}"
                )
            else:
                lines.append(f"- {status_icon} {f.statement}")
        lines.append("")

    lines.append("---")
    lines.append("Legenda: \u2705=verified \u2611\ufe0f=context_supported "
                  "\u2699\ufe0f=derived \U0001f310=verified_web "
                  "\u26a0\ufe0f=stale \u274c=rejected")

    return "\n".join(lines)


def _status_icon(status: FactStatus) -> str:
    """Retorna emoji para status."""
    return {
        FactStatus.VERIFIED: "\u2705",
        FactStatus.CONTEXT_SUPPORTED: "\u2611\ufe0f",
        FactStatus.DERIVED: "\u2699\ufe0f",
        FactStatus.VERIFIED_WEB: "\U0001f310",
        FactStatus.UNVERIFIABLE_SOURCE: "\u2753",
        FactStatus.PENDING: "\u23f3",
        FactStatus.STALE: "\u26a0\ufe0f",
        FactStatus.REJECTED: "\u274c",
    }.get(status, "\u2753")


# ---------------------------------------------------------------------------
# Helpers de dedup
# ---------------------------------------------------------------------------

def _quotes_match(q1: str, q2: str) -> bool:
    """Verifica se duas citações são equivalentes (resolve Q4)."""
    if not q1 or not q2:
        return False
    q1 = q1.strip()
    q2 = q2.strip()

    shorter = min(len(q1), len(q2))
    if shorter < 40:
        return q1.lower() == q2.lower()
    return fuzz.ratio(q1.lower(), q2.lower()) / 100.0 >= 0.93


# ---------------------------------------------------------------------------
# Helpers gerais
# ---------------------------------------------------------------------------

def _generate_id() -> str:
    """Gera ID curto de 8 chars."""
    return uuid.uuid4().hex[:8]


def _now_iso() -> str:
    """Retorna timestamp ISO atual."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_extract_facts(
    project: str,
    ref_range: list[int] | None = None,
    section: str | None = None,
) -> int:
    """Camada 1: resolve refs + extrai candidatos quantitativos.

    Gera [projeto]/.facts-candidates.md
    """
    from .verify_claims import resolve_references
    from .verify_markdown_refs import parse_vancouver_refs
    from .config import get_project_dir

    project_dir = get_project_dir(project)

    # Verificar se há pending não importado
    pending_path = _get_pending_path(project)
    if pending_path.exists():
        pending_lines = [
            l for l in pending_path.read_text(encoding="utf-8").split("\n")
            if l.strip()
        ]
        if pending_lines:
            print(
                f"\n\u26a0\ufe0f  Há {len(pending_lines)} fatos pendentes em "
                f".facts-pending.jsonl — rode facts-import primeiro.\n"
            )

    # Encontrar arquivo canônico
    canonical_files = (
        list(project_dir.glob("capitulo*.md"))
        + list(project_dir.glob("artigo*.md"))
    )
    if not canonical_files:
        print(f"ERRO: Nenhum arquivo canônico encontrado em {project_dir}")
        return 1

    canonical = canonical_files[0]
    print(f"Arquivo canônico: {canonical.name}")

    # Parsear referências
    text = canonical.read_text(encoding="utf-8")
    parsed_refs = parse_vancouver_refs(text)
    if not parsed_refs:
        print("ERRO: Nenhuma referência Vancouver encontrada.")
        return 1

    # Filtrar por range se especificado
    if ref_range:
        parsed_refs = [r for r in parsed_refs if r["number"] in ref_range]
        print(f"Refs selecionadas: {sorted(r['number'] for r in parsed_refs)}")
    else:
        print(f"Total de refs: {len(parsed_refs)}")

    # Resolver conteúdo das refs
    print("\nResolvendo conteúdo das referências...")
    ref_contents = resolve_references(parsed_refs, project=project)

    # Verificar staleness se registry existir
    registry = load_registry(project)
    if registry.ref_hashes:
        stale = check_registry_staleness(registry, ref_contents)
        if stale:
            print(f"\n\u26a0\ufe0f  {len(stale)} refs com conteúdo alterado (STALE):")
            for ref_num, reason in stale:
                print(f"  Ref [{ref_num}]: {reason}")
            save_registry(registry, backup=True)

    # Extrair candidatos de cada ref
    print("\nExtraindo candidatos quantitativos...")
    ref_candidates: dict[int, tuple] = {}

    for ref_num, ref in sorted(ref_contents.items()):
        if ref.content and ref.source_level.value != "none":
            candidates = _extract_all_numbers(ref.content)
            ref_candidates[ref_num] = (ref, candidates)
            print(f"  Ref [{ref_num}]: {len(candidates)} candidatos")
        else:
            ref_candidates[ref_num] = (ref, [])
            print(f"  Ref [{ref_num}]: sem conteúdo disponível")

    # Gerar relatório
    report = _generate_candidates_report(ref_candidates)
    output_path = _get_candidates_path(project)
    output_path.write_text(report, encoding="utf-8")

    total = sum(len(c) for _, c in ref_candidates.values())
    print(f"\n{total} candidatos quantitativos de {len(ref_candidates)} refs.")
    print(f"Ver: {output_path.name}")

    return 0


def run_facts_import(project: str) -> int:
    """Camada 3: valida e importa de .facts-pending.jsonl."""
    print(f"Importando fatos para {project}...")
    stats = import_pending_facts(project)

    print(f"\nResultado da importação:")
    print(f"  Importados: {stats['imported']}")
    print(f"  Rejeitados: {stats['rejected']}")
    print(f"  Inválidos:  {stats['invalid']}")

    if stats["details"]:
        print(f"\nDetalhes:")
        for d in stats["details"]:
            print(f"  {d}")

    return 0


def run_facts_status(project: str, section: str | None = None) -> int:
    """Exibe status do registry (section brief para escrita)."""
    registry = load_registry(project)

    if not registry.facts:
        print(f"Facts Registry vazio para {project}.")
        print("Rode extract-facts e facts-import primeiro.")
        return 0

    if section:
        brief = generate_section_brief(registry, section)
        print(brief)
    else:
        print(f"Facts Registry — {project}")
        print(f"Total: {len(registry.facts)} fatos")
        print(f"Refs processadas: {sorted(registry.refs_processed)}")
        print()
        # Resumo por status
        status_counts: dict[str, int] = {}
        for f in registry.facts:
            status_counts[f.status.value] = status_counts.get(f.status.value, 0) + 1
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

    return 0


def run_facts_report(project: str, output: str | None = None) -> int:
    """Gera relatório markdown completo."""
    registry = load_registry(project)

    if not registry.facts:
        print(f"Facts Registry vazio para {project}.")
        return 0

    report = generate_facts_report(registry)

    if output:
        Path(output).write_text(report, encoding="utf-8")
        print(f"Relatório salvo em: {output}")
    else:
        print(report)

    return 0


def run_facts_crosscheck(project: str) -> int:
    """Cruza .claims-data.json com registry."""
    from .config import get_project_dir

    project_dir = get_project_dir(project)

    # Encontrar claims-data.json
    claims_files = list(project_dir.glob("*.claims-data.json"))
    if not claims_files:
        print(f"Nenhum .claims-data.json encontrado em {project_dir}")
        print("Rode verify-claims primeiro.")
        return 1

    registry = load_registry(project)
    if not registry.facts:
        print("Facts Registry vazio. Rode extract-facts e facts-import primeiro.")
        return 1

    claims_path = claims_files[0]
    print(f"Claims: {claims_path.name}")
    print(f"Registry: {len(registry.facts)} fatos")
    print()

    results = cross_check_claims_vs_registry(claims_path, registry)

    # Relatório
    registered = [r for r in results if r["status"] == "REGISTRADO"]
    not_registered = [r for r in results if r["status"] == "NAO_REGISTRADO"]

    print(f"Dados quantitativos verificados: {len(registered)}")
    print(f"Dados NÃO registrados: {len(not_registered)}")

    if not_registered:
        print(f"\n\u26a0\ufe0f  Dados usados no texto mas NÃO no Facts Registry:")
        for r in not_registered:
            print(f"  - Ref [{r['ref_number']}] value={r['value']}: {r['detail']}")

    if registered:
        print(f"\n\u2705 Dados registrados e confirmados:")
        for r in registered:
            print(f"  - Ref [{r['ref_number']}] value={r['value']}: {r['detail']}")

    return 0
