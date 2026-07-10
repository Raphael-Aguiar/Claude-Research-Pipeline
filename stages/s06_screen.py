"""Etapa 6 — Triagem de Relevância (v2).

4 filtros em cascata + ranking com bônus de recência, OA e canônico.
Mapeamento obrigatório a eixos de pesquisa. Gera lista de validação.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..config import get_pipeline_dir
from ..models import Modality, Reference, Relevance, SearchConfig


def screen_references(
    refs: list[Reference],
    config: SearchConfig,
) -> list[Reference]:
    """Triagem de relevância por co-ocorrência multi-bloco + ranking.

    4 filtros em cascata:
    1. Exclusão hard por pub_type (editoriais, cartas)
    2. Exclusão hard por exclusion_keywords (off-topic)
    3. Co-ocorrência multi-bloco com word boundaries
    4. Mapeamento obrigatório a eixos de pesquisa

    Após os filtros: ranking com bônus de recência, OA e canônico.
    Gera lista de validação (~50 candidatos) para revisão do autor.
    """
    print("\n  Triagem de relevância (v2 — co-ocorrência multi-bloco)...")

    include_on_doubt = config.modality in (
        Modality.REVISAO_INTEGRATIVA,
        Modality.REVISAO_SISTEMATICA,
        Modality.REVISAO_ESCOPO,
        Modality.META_REVISAO,
    )

    # Preparar keywords com word boundaries
    direct_patterns = _compile_patterns(config.relevance_keywords_direct)
    exclusion_patterns = _compile_patterns(config.exclusion_keywords)

    # Preparar keyword blocks como patterns
    block_patterns = []
    for block in config.keyword_blocks:
        terms = block.get("terms", [])
        if terms:
            block_patterns.append(_compile_patterns(terms))

    # Contadores PRISMA
    counts = {
        "total": 0,
        "excluded_pub_type": 0,
        "excluded_not_review": 0,
        "excluded_off_topic": 0,
        "classified_direct": 0,
        "classified_tangential": 0,
        "classified_off_topic": 0,
        "llm_promoted": 0,
        "llm_divergent": 0,
    }

    for ref in refs:
        if ref.is_duplicate:
            continue
        counts["total"] += 1

        text = _get_evaluation_text(ref).lower()
        title_text = (ref.title or "").lower()

        if not text.strip():
            ref.relevance = Relevance.TANGENTIAL if include_on_doubt else Relevance.OFF_TOPIC
            ref.relevance_method = "no_text"
            counts["classified_off_topic" if ref.relevance == Relevance.OFF_TOPIC else "classified_tangential"] += 1
            continue

        # --- FILTRO 1: Exclusão por pub_type ---
        if ref.pub_type and _is_excluded_pub_type(ref.pub_type):
            ref.relevance = Relevance.OFF_TOPIC
            ref.relevance_method = "pub_type_excluded"
            counts["excluded_pub_type"] += 1
            continue

        # --- FILTRO 1b (só meta-revisão): apenas revisões entram ---
        if config.modality == Modality.META_REVISAO and not _is_review_like(ref):
            ref.relevance = Relevance.OFF_TOPIC
            ref.relevance_method = "nao_e_revisao"
            counts["excluded_not_review"] += 1
            continue

        # --- FILTRO 2: Exclusão hard por keywords ---
        if exclusion_patterns and _has_match(title_text, exclusion_patterns):
            # Verificar se também tem keywords diretas (não descartar se ambíguo)
            if not (direct_patterns and _has_match(text, direct_patterns)):
                ref.relevance = Relevance.OFF_TOPIC
                ref.relevance_method = "exclusion_keyword"
                counts["excluded_off_topic"] += 1
                continue

        # --- FILTRO 3: Co-ocorrência multi-bloco ---
        blocks_matched = 0
        for bp in block_patterns:
            if _has_match(text, bp):
                blocks_matched += 1

        # Para pesquisa-base: precisa match em ≥2 blocos para DIRECT
        if blocks_matched >= 2:
            ref.relevance = Relevance.DIRECT
            ref.relevance_method = f"co_occurrence_{blocks_matched}_blocks"
        elif blocks_matched == 1:
            # Match em 1 bloco + keyword direta = DIRECT
            if direct_patterns and _has_match(text, direct_patterns):
                ref.relevance = Relevance.DIRECT
                ref.relevance_method = "single_block_plus_direct_keyword"
            else:
                ref.relevance = Relevance.TANGENTIAL
                ref.relevance_method = "single_block"
        else:
            # Sem match em blocos — verificar keywords diretas
            if direct_patterns and _has_match(text, direct_patterns):
                ref.relevance = Relevance.TANGENTIAL
                ref.relevance_method = "direct_keyword_only"
            elif include_on_doubt:
                ref.relevance = Relevance.TANGENTIAL
                ref.relevance_method = "default_include"
            else:
                ref.relevance = Relevance.OFF_TOPIC
                ref.relevance_method = "no_match"

        # --- FILTRO 4: Mapeamento a eixos de pesquisa ---
        if config.research_axes and ref.relevance != Relevance.OFF_TOPIC:
            mapped = _map_to_axes(text, config)
            ref.mapped_axes = mapped
            # DIRECT sem eixo → rebaixar para TANGENTIAL
            if ref.relevance == Relevance.DIRECT and not mapped:
                ref.relevance = Relevance.TANGENTIAL
                ref.relevance_method += "_no_axis"

        # --- FILTRO 5: Combinação com triagem semântica LLM (dupla triagem) ---
        # O LLM aplica os critérios de I/E em prosa (screen-export/import).
        # Concordância refina; divergência NUNCA descarta silenciosamente —
        # a ref fica viva e marcada para o revisor humano.
        if ref.llm_verdict:
            if ref.llm_verdict == "include":
                if ref.relevance == Relevance.TANGENTIAL:
                    ref.relevance = Relevance.DIRECT
                    ref.relevance_method += "|llm_include"
                    counts["llm_promoted"] += 1
                elif ref.relevance == Relevance.OFF_TOPIC:
                    # Divergente: LLM inclui, keywords excluem → humano decide
                    ref.relevance = Relevance.TANGENTIAL
                    ref.relevance_method += "|llm_divergent_include"
                    counts["llm_divergent"] += 1
            elif ref.llm_verdict == "exclude":
                if ref.relevance != Relevance.OFF_TOPIC:
                    # Divergente: keywords incluem, LLM exclui → humano decide
                    ref.relevance_method += "|llm_divergent_exclude"
                    counts["llm_divergent"] += 1
            elif ref.llm_verdict == "maybe":
                ref.relevance_method += "|llm_maybe"

        # Contar
        if ref.relevance == Relevance.DIRECT:
            counts["classified_direct"] += 1
        elif ref.relevance == Relevance.TANGENTIAL:
            counts["classified_tangential"] += 1
        else:
            counts["classified_off_topic"] += 1

    # --- RANKING ---
    _compute_ranking(refs, config)

    # --- DETECTAR CANÔNICOS ---
    _detect_canonical(refs)

    # --- CAMADA SEMÂNTICA (opt-in: semantic_rerank no scope.yaml) ---
    # Re-ranqueamento por embeddings + resgate de refs que as keywords
    # não capturaram. Depois do ranking (bônus aditivo), antes da
    # validation-list (resgatadas aparecem para o revisor humano).
    if config.semantic_rerank:
        from ..semantic import apply_semantic_layer
        apply_semantic_layer(refs, config)

    # Print stats
    unique = counts["total"]
    print(f"  → DIRECT: {counts['classified_direct']} ({counts['classified_direct']/unique*100:.0f}%)" if unique else "")
    print(f"  → TANGENTIAL: {counts['classified_tangential']} ({counts['classified_tangential']/unique*100:.0f}%)" if unique else "")
    print(f"  → OFF_TOPIC: {counts['classified_off_topic']} ({counts['classified_off_topic']/unique*100:.0f}%)" if unique else "")
    print(f"  → Excluídos por pub_type: {counts['excluded_pub_type']}")
    print(f"  → Excluídos por exclusion_keyword: {counts['excluded_off_topic']}")
    if config.modality == Modality.META_REVISAO:
        print(f"  → Excluídos por não serem revisão (meta-revisão): {counts['excluded_not_review']}")
    if counts["llm_promoted"] or counts["llm_divergent"]:
        print(f"  → Triagem LLM: {counts['llm_promoted']} promovidas, "
              f"{counts['llm_divergent']} divergências (ver screening-report.md)")

    # --- GERAR LISTA DE VALIDAÇÃO ---
    _generate_validation_list(refs, config, counts)

    return refs


_REVIEW_TITLE_RE = re.compile(
    r"systematic review|meta-?analysis|scoping review|umbrella review"
    r"|integrative review|rapid review|overview of (systematic )?reviews"
    r"|revis[aã]o sistem[aá]tica|revis[aã]o de escopo|revis[aã]o integrativa"
    r"|metan[aá]lise|meta-an[aá]lise|revis[aã]o guarda-chuva",
    re.IGNORECASE,
)

_REVIEW_PUB_TYPES = {
    "review", "systematic-review", "systematic review", "meta-analysis",
    "journal-review",
}


def _is_review_like(ref: Reference) -> bool:
    """Heurística: a referência é uma revisão? (para modalidade meta-revisão).

    Sinais: pub_type de revisão OU título contendo termo de revisão
    (EN/PT). Abstract não é usado — muitos artigos primários citam
    'systematic review' no abstract sem sê-lo.
    """
    if ref.pub_type and ref.pub_type.strip().lower() in _REVIEW_PUB_TYPES:
        return True
    if ref.title and _REVIEW_TITLE_RE.search(ref.title):
        return True
    return False


def _compile_patterns(keywords: list[str]) -> list[re.Pattern]:
    """Compila keywords em regex patterns com word boundaries."""
    patterns = []
    for kw in keywords:
        try:
            pattern = re.compile(r'\b' + re.escape(kw.lower()) + r'\b', re.IGNORECASE)
            patterns.append(pattern)
        except re.error:
            continue
    return patterns


def _has_match(text: str, patterns: list[re.Pattern]) -> bool:
    """Verifica se o texto contém match para algum pattern."""
    return any(p.search(text) for p in patterns)


def _count_matches(text: str, patterns: list[re.Pattern]) -> int:
    """Conta quantos patterns fazem match no texto."""
    return sum(1 for p in patterns if p.search(text))


def _is_excluded_pub_type(pub_type: str) -> bool:
    """Verifica se o pub_type deve ser excluído."""
    excluded = {
        "editorial", "letter", "comment", "news",
        "newspaper article", "personal narrative",
        "published erratum", "retracted publication",
    }
    return pub_type.lower().strip() in excluded


def _map_to_axes(text: str, config: SearchConfig) -> list[str]:
    """Mapeia texto a eixos de pesquisa usando keywords + sinônimos."""
    mapped = []
    for axis in config.research_axes:
        # Verificar keywords do eixo
        kw_patterns = _compile_patterns(axis.keywords)
        syn_patterns = _compile_patterns(axis.synonyms)

        if _has_match(text, kw_patterns) or _has_match(text, syn_patterns):
            mapped.append(axis.name)

    return mapped


def _compute_ranking(refs: list[Reference], config: SearchConfig) -> None:
    """Computa score de ranking para refs não-duplicadas e não-off-topic.

    Fórmula:
      score = nº_eixos_mapeados × 3
            + nº_keywords_aplicação × 2
            + nº_keywords_domínio × 1
            + bônus_recência (2025-26: +3, 2023-24: +2, 2021-22: +1)
            + bônus_OA (+2)
            + bônus_canônico (min(citation_count/50, 5))
    """
    # Preparar patterns por conceito de bloco
    app_patterns = []
    domain_patterns = []
    for block in config.keyword_blocks:
        concept = block.get("concept", "").lower()
        terms = block.get("terms", [])
        if "aplicação" in concept or "application" in concept:
            app_patterns = _compile_patterns(terms)
        elif "domínio" in concept or "domain" in concept:
            domain_patterns = _compile_patterns(terms)

    direct_patterns = _compile_patterns(config.relevance_keywords_direct)

    for ref in refs:
        if ref.is_duplicate or ref.relevance == Relevance.OFF_TOPIC:
            ref.relevance_score = 0.0
            continue

        text = _get_evaluation_text(ref).lower()
        score = 0.0

        # Eixos mapeados × 3
        score += len(ref.mapped_axes) * 3

        # Keywords de aplicação × 2
        if app_patterns:
            score += _count_matches(text, app_patterns) * 2

        # Keywords de domínio × 1
        if domain_patterns:
            score += _count_matches(text, domain_patterns) * 1

        # Keywords diretas (bônus menor para evitar duplicação com blocos)
        if direct_patterns:
            score += min(_count_matches(text, direct_patterns), 5) * 0.5

        # Bônus de recência
        if ref.year:
            if ref.year >= 2025:
                score += 3
            elif ref.year >= 2023:
                score += 2
            elif ref.year >= 2021:
                score += 1

        # Bônus OA
        if ref.is_open_access:
            score += 2

        # Bônus canônico (citation count)
        if ref.citation_count and ref.citation_count > 0:
            score += min(ref.citation_count / 50, 5)

        ref.relevance_score = round(score, 2)


def _detect_canonical(refs: list[Reference]) -> None:
    """Detecta papers canônicos por alta contagem de citações.

    Canônico = top 10% por citações ou >100 citações absolutas.
    """
    # Coletar citation counts de refs não-duplicadas com contagem
    active_with_citations = [
        r for r in refs
        if not r.is_duplicate and r.citation_count and r.citation_count > 0
    ]

    if not active_with_citations:
        return

    counts = sorted([r.citation_count for r in active_with_citations], reverse=True)
    top_10_threshold = counts[max(0, len(counts) // 10 - 1)] if counts else 100

    for ref in refs:
        if ref.is_duplicate:
            continue
        if ref.citation_count and (
            ref.citation_count >= 100 or ref.citation_count >= top_10_threshold
        ):
            ref.canonical = True


def _get_evaluation_text(ref: Reference) -> str:
    """Combina título, abstract e journal para avaliação."""
    parts = []
    if ref.title:
        parts.append(ref.title)
    if ref.abstract:
        parts.append(ref.abstract)
    if ref.journal:
        parts.append(ref.journal)
    return " ".join(parts)


def _generate_validation_list(
    refs: list[Reference],
    config: SearchConfig,
    counts: dict,
) -> None:
    """Gera lista de ~50 candidatos para validação rápida pelo autor."""
    pipeline_dir = get_pipeline_dir(config.project_name)

    # Refs rankeáveis: DIRECT ou TANGENTIAL, não-duplicadas
    candidates = [
        r for r in refs
        if not r.is_duplicate and r.relevance in (Relevance.DIRECT, Relevance.TANGENTIAL)
    ]
    candidates.sort(key=lambda r: r.relevance_score, reverse=True)

    max_final = config.max_final_refs
    top_n = min(max_final + 10, len(candidates))  # ~50 candidatos
    recommended = min(max_final, len(candidates))

    lines = [
        f"# Lista de Validação — {config.project_name}",
        "",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Candidatos listados:** {top_n} (top-{recommended} recomendados)",
        f"**Critérios aplicados:** co-ocorrência multi-bloco + mapeamento a eixos",
        "",
        "Revise os títulos abaixo. Marque com `[x]` os que devem ser **incluídos**.",
        "Os top-{} são recomendados pelo pipeline.".format(recommended),
        "",
        "---",
        "",
        "| # | Rec? | Score | Título | Ano | Journal | Eixos | Rel. |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for i, ref in enumerate(candidates[:top_n], 1):
        rec = "✓" if i <= recommended else ""
        title = ref.title[:70] + "..." if len(ref.title) > 70 else ref.title
        journal = (ref.journal or "?")[:25]
        axes = ", ".join(ref.mapped_axes[:3]) if ref.mapped_axes else "-"
        rel = ref.relevance.name
        lines.append(
            f"| {i} | {rec} | {ref.relevance_score:.1f} | {title} | "
            f"{ref.year or '?'} | {journal} | {axes} | {rel} |"
        )

    # Refs suspeitas (próximas do cutoff)
    if len(candidates) > top_n:
        borderline = candidates[top_n:top_n + 10]
        if borderline:
            lines.extend([
                "",
                "---",
                "",
                "## Candidatos borderline (para revisão opcional)",
                "",
                "| # | Score | Título | Ano | Eixos |",
                "|---|---|---|---|---|",
            ])
            for i, ref in enumerate(borderline, top_n + 1):
                title = ref.title[:70] + "..." if len(ref.title) > 70 else ref.title
                axes = ", ".join(ref.mapped_axes[:3]) if ref.mapped_axes else "-"
                lines.append(
                    f"| {i} | {ref.relevance_score:.1f} | {title} | {ref.year or '?'} | {axes} |"
                )

    # Resumo PRISMA simplificado
    lines.extend([
        "",
        "---",
        "",
        "## Resumo da Triagem",
        "",
        f"- Total triados: {counts['total']}",
        f"- Excluídos por pub_type: {counts['excluded_pub_type']}",
        f"- Excluídos por off-topic keywords: {counts['excluded_off_topic']}",
        f"- DIRECT: {counts['classified_direct']}",
        f"- TANGENTIAL: {counts['classified_tangential']}",
        f"- OFF_TOPIC (outros): {counts['classified_off_topic']}",
        f"- Candidatos rankeados: {len(candidates)}",
        f"- Top-{recommended} recomendados",
    ])

    validation_path = pipeline_dir / "validation-list.md"
    validation_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → Lista de validação: {validation_path}")
