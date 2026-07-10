"""Busca semântica vetorial — re-ranqueamento e resgate por embeddings (Fase 3).

Usa o Ollama local (default: modelo bge-m3, multilíngue PT/EN/ES) para:

1. **Re-ranqueamento**: similaridade de cosseno entre a consulta do projeto
   (pergunta de pesquisa + critérios de inclusão + termos) e cada referência
   (título + abstract) vira bônus no relevance_score.
2. **Resgate semântico**: referência descartada pela triagem por keywords
   (`no_match` — nenhum bloco casou) mas semanticamente próxima da consulta
   é promovida a TANGENTIAL com método `semantic_rescue` — captura sinônimos
   e formulações que as keywords não previram. Nunca silencioso: aparece na
   validation-list e no relatório.

Opt-in: `semantic_rerank: true` no scope.yaml (ou comando `semantic`).
Degradação graciosa: sem Ollama no ar, o pipeline segue sem a camada
semântica, com aviso explícito.

Validação empírica (2026-07-10, bge-m3, consulta×título+abstract):
match direto ≈ 0.85; relevante mas PARAFRASEADO (sinônimos imprevistos,
o alvo do resgate) ≈ 0.68; fora do escopo ≈ 0.36. Threshold default de
resgate: 0.60 — margem de ~0.24 sobre o off-topic; falso positivo custa
pouco (resgatada vai para revisão humana), falso negativo perde evidência.
"""

from __future__ import annotations

import math

import requests

from .models import Reference, Relevance, SearchConfig

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_RESCUE_THRESHOLD = 0.60
DEFAULT_RERANK_WEIGHT = 8.0  # semantic_score (0-1) × peso ≈ escala do ranking
_BATCH_SIZE = 16
_EMBED_TIMEOUT = 120


def ollama_available(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    """Verifica se o Ollama está no ar."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def embed_texts(
    texts: list[str],
    model: str = DEFAULT_EMBED_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
) -> list[list[float]] | None:
    """Gera embeddings via Ollama (/api/embed), em lotes.

    Returns:
        Lista de vetores na mesma ordem, ou None em falha (logada).
    """
    if not texts:
        return []
    vectors: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i:i + _BATCH_SIZE]
        try:
            resp = requests.post(
                f"{base_url}/api/embed",
                json={"model": model, "input": batch},
                timeout=_EMBED_TIMEOUT,
            )
            if resp.status_code != 200:
                print(f"    Ollama embed: HTTP {resp.status_code} — {resp.text[:200]}")
                return None
            vectors.extend(resp.json()["embeddings"])
        except requests.RequestException as e:
            print(f"    Ollama embed: erro ({e})")
            return None
    return vectors


def cosine(a: list[float], b: list[float]) -> float:
    """Similaridade de cosseno entre dois vetores."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _build_query_text(config: SearchConfig) -> str:
    """Texto de consulta: pergunta + critérios de inclusão + termos-chave."""
    parts = [config.research_question]
    parts.extend(config.inclusion_criteria)
    for block in config.keyword_blocks:
        parts.extend(block.get("terms", []))
    return ". ".join(p for p in parts if p)


def _ref_text(ref: Reference) -> str:
    """Texto da referência: título + abstract (truncado)."""
    title = ref.title or ""
    abstract = (ref.abstract or "")[:1500]
    return f"{title}. {abstract}".strip()


def compute_semantic_scores(
    refs: list[Reference],
    config: SearchConfig,
    model: str = DEFAULT_EMBED_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
) -> int:
    """Calcula semantic_score (cosseno consulta×ref) para refs ativas.

    Returns:
        Número de refs pontuadas (0 se Ollama indisponível).
    """
    if not ollama_available(base_url):
        print(
            "    Camada semântica PULADA: Ollama não está acessível em "
            f"{base_url} (inicie o Ollama ou desligue semantic_rerank)."
        )
        return 0

    active = [r for r in refs if not r.is_duplicate and _ref_text(r)]
    if not active:
        return 0

    query_text = _build_query_text(config)
    print(f"    Embeddings via Ollama ({model}): consulta + {len(active)} refs...")

    query_vec_list = embed_texts([query_text], model, base_url)
    if not query_vec_list:
        return 0
    query_vec = query_vec_list[0]

    ref_vecs = embed_texts([_ref_text(r) for r in active], model, base_url)
    if ref_vecs is None:
        return 0

    for ref, vec in zip(active, ref_vecs):
        ref.semantic_score = round(cosine(query_vec, vec), 4)

    return len(active)


def apply_semantic_layer(
    refs: list[Reference],
    config: SearchConfig,
    model: str = DEFAULT_EMBED_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
) -> dict:
    """Aplica a camada semântica completa: scores + resgate + bônus de ranking.

    Chamar APÓS a triagem por keywords (e LLM) e ANTES do ranking final —
    ou standalone via comando `semantic` (que reordena o que existir).
    """
    scored = compute_semantic_scores(refs, config, model, base_url)
    if not scored:
        return {"scored": 0, "rescued": 0}

    threshold = config.semantic_rescue_threshold
    rescued = 0
    for ref in refs:
        if ref.is_duplicate or ref.semantic_score is None:
            continue

        # Resgate: descartada só por falta de match de keyword ('no_match'),
        # mas semanticamente próxima. Exclusões deliberadas (exclusion_keyword,
        # pub_type, nao_e_revisao, llm exclude concordante) NÃO são resgatadas.
        if (
            ref.relevance == Relevance.OFF_TOPIC
            and ref.relevance_method.split("|")[0] == "no_match"
            and ref.semantic_score >= threshold
        ):
            ref.relevance = Relevance.TANGENTIAL
            ref.relevance_method += "|semantic_rescue"
            rescued += 1

        # Bônus de ranking (refs vivas)
        if ref.relevance != Relevance.OFF_TOPIC:
            ref.relevance_score += ref.semantic_score * config.semantic_weight

    print(f"    Semântica: {scored} refs pontuadas; {rescued} resgatadas "
          f"(similaridade ≥ {threshold})")
    return {"scored": scored, "rescued": rescued}
