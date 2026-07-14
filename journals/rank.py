"""Filtros duros + pré-score ponderado (pesos.yaml) para seleção de finalistas.

O pré-score existe só para não mandar centenas de candidatos ao modelo
forte; o ranking apresentado a Raphael é a narrativa de trade-offs do
julgamento (F3), nunca este número.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import yaml

PESOS_PATH = Path(__file__).parent / "pesos.yaml"


def carregar_pesos(path: Path = PESOS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


_STOPWORDS = {
    "the", "of", "in", "and", "for", "with", "a", "an", "to", "on", "by",
    "de", "da", "do", "em", "e", "para", "com", "um", "uma", "no", "na",
}


def _tokens(texto: str) -> set[str]:
    return {
        t for t in "".join(
            c.lower() if c.isalnum() else " " for c in texto
        ).split()
        if len(t) > 2 and t not in _STOPWORDS
    }


def ranquear(
    conn: sqlite3.Connection,
    candidatos: list[str],
    frequencia: Counter,
    curados: set[str],
    consulta: dict,
    top: int = 12,
    pesos_path: Path = PESOS_PATH,
) -> dict:
    """Pré-score por consolidação Área 22 + fit. Retorna finalistas ordenados.

    Sem filtro duro: nada é excluído por não-consolidação (a rota do
    destaque qualitativo permanece); periódicos fora de porta entram
    ranqueados e sinalizados. `excluidos` guarda só ISSNs sem registro.
    """
    cfg = carregar_pesos(pesos_path)
    pesos = cfg["pesos"]
    soma_pesos = sum(pesos.values())
    max_freq = max(frequencia.values()) if frequencia else 1
    tokens_consulta = _tokens(
        " ".join([consulta.get("tema") or "",
                  *(consulta.get("keywords_en") or [])])
    )

    finalistas = []
    excluidos = []
    for issn_l in candidatos:
        row = conn.execute(
            "SELECT * FROM periodicos WHERE issn_l = ?", (issn_l,)
        ).fetchone()
        if row is None:
            excluidos.append(
                {"issn_l": issn_l, "titulo": issn_l,
                 "razao": "sem registro na base (não enriquecido)"}
            )
            continue

        flags: list[str] = []
        comp: dict[str, float] = {}

        # Consolidação Área 22 (eixo dominante do critério vigente).
        # NÃO há filtro duro por estrato — o Qualis é retrospectivo. Nada é
        # excluído por não-consolidação: a rota do destaque qualitativo
        # permanece, então o periódico entra ranqueado e sinalizado.
        comp["consolidacao"] = _valor_consolidacao(row, cfg, flags)

        # Fit temático determinístico (proxy — julgamento real é do LLM):
        # frequência na busca + sobreposição consulta×tópicos OpenAlex +
        # bônus de curadoria e de categoria de saúde de interesse.
        freq_norm = frequencia.get(issn_l, 0) / max_freq
        overlap = 0.0
        if tokens_consulta and row["topicos"]:
            tokens_topicos = _tokens(" ".join(json.loads(row["topicos"])))
            overlap = len(tokens_consulta & tokens_topicos) / len(tokens_consulta)
        bonus_curado = 0.3 if issn_l in curados else 0.0
        bonus_cat = 0.0
        cats_saude = {
            c["categoria"] for c in json.loads(row["porta_a_categorias"] or "[]")
        }
        if cats_saude & {c.strip() for c in cfg.get("categorias_fit_interesse", [])}:
            bonus_cat = 0.15
        comp["fit_tematico"] = min(
            1.0, 0.5 * freq_norm + 0.5 * overlap + bonus_curado + bonus_cat
        )

        # APC (acessibilidade)
        if row["apc_usd"] is not None:
            comp["apc"] = max(0.0, 1.0 - row["apc_usd"] / cfg["apc_teto_usd"])
        elif row["apc_valor"] is not None and row["apc_valor"] == 0:
            comp["apc"] = 1.0
        else:
            comp["apc"] = 0.5
            flags.append("APC não disponível")

        # Open access
        comp["oa"] = {1: 1.0, 0: 0.3}.get(row["is_oa"], 0.5)
        if row["is_oa"] is None:
            flags.append("status OA não disponível")

        # Seletividade proxy
        if row["citedness_2yr"] is not None:
            comp["seletividade"] = min(1.0, row["citedness_2yr"] / 10.0)
        else:
            comp["seletividade"] = 0.3
            flags.append("métricas não disponíveis")

        # Indexação
        comp["indexacao"] = {1: 1.0, 0: 0.0}.get(row["medline_indexado"], 0.4)
        if row["medline_indexado"] is None:
            flags.append("indexação MEDLINE não verificada")

        score = sum(pesos[k] * comp[k] for k in pesos) / soma_pesos

        finalistas.append(_montar_finalista(row, score, comp, flags, frequencia))

    finalistas.sort(key=lambda f: f["pre_score"], reverse=True)
    return {"finalistas": finalistas[:top], "excluidos": excluidos}


def _valor_consolidacao(row, cfg: dict, flags: list) -> float:
    """Componente de consolidação Área 22 (0-1) + flags honestas."""
    vc = cfg["valor_consolidacao"]
    status = row["porta_a_status"]
    porta_b = row["porta_b_scielo_sp"] == 1
    quartil = row["porta_a_quartil"]

    if status == "consolidado" and quartil == "Q1":
        valor = vc["porta_a_q1"]
    elif status == "consolidado" and quartil == "Q2":
        valor = vc["porta_a_q2"]
    elif porta_b:
        valor = vc["porta_b"]
        flags.append("consolidação via Porta B (SciELO-SP) — confirmar h5 > P60")
    elif status == "nao_por_scimago":
        valor = vc["nao_por_scimago"]
        flags.append(
            f"saúde só em {quartil} pelo SJR — pode cumprir via CiteScore/JIF real"
        )
    elif status == "sem_categoria_saude":
        valor = vc["sem_categoria_saude"]
        flags.append(
            "SEM categoria de saúde no Scopus — não cumpre Porta A "
            "(rota: destaque qualitativo)"
        )
    else:  # sem_dado ou None
        valor = vc["sem_dado"]
        flags.append("consolidação não determinável — verificar Scopus/JCR manual")

    if porta_b and valor < vc["porta_b"]:
        valor = vc["porta_b"]  # Porta B resgata mesmo com Porta A fraca
    return valor


def _montar_finalista(
    row: sqlite3.Row, score: float, comp: dict, flags: list, freq: Counter
) -> dict:
    if row["apc_valor"] == 0:
        apc_display = "zero"
    elif row["apc_valor"] is not None:
        apc_display = f"{row['apc_moeda'] or ''} {row['apc_valor']:,.0f}".strip()
    elif row["apc_usd"] is not None:
        apc_display = f"USD {row['apc_usd']:,.0f} (OpenAlex)"
    else:
        apc_display = "não disponível"

    return {
        "issn_l": row["issn_l"],
        "titulo": row["titulo"],
        "editora": row["editora"],
        # Consolidação Área 22 (critério vigente)
        "consolidado": row["consolidado"],
        "porta_a_status": row["porta_a_status"],
        "porta_a_categoria": row["porta_a_categoria"],
        "porta_a_quartil": row["porta_a_quartil"],
        "porta_a_categorias": json.loads(row["porta_a_categorias"] or "[]"),
        "porta_b_scielo_sp": row["porta_b_scielo_sp"],
        "consolidacao_fonte": row["consolidacao_fonte"],
        "consolidacao_verificado_em": row["consolidacao_verificado_em"],
        # Qualis 2021-2024 = legado retrospectivo
        "qualis_estrato": row["qualis_estrato"],
        "qualis_fonte": row["qualis_fonte"],
        "qualis_areas": json.loads(row["qualis_areas"] or "[]"),
        "apc_display": apc_display,
        "apc_valor": row["apc_valor"],
        "apc_moeda": row["apc_moeda"],
        "apc_usd": row["apc_usd"],
        "apc_waiver": row["apc_waiver"],
        "apc_fonte": row["apc_fonte"],
        "apc_verificado_em": row["apc_verificado_em"],
        "is_oa": row["is_oa"],
        "in_doaj": row["in_doaj"],
        "licenca": row["licenca"],
        "peer_review": row["peer_review"],
        "medline_indexado": row["medline_indexado"],
        "medline_verificado_em": row["medline_verificado_em"],
        "citedness_2yr": row["citedness_2yr"],
        "h_index": row["h_index"],
        "sjr": row["sjr"],
        "sjr_quartil": row["sjr_quartil"],
        "sjr_ano": row["sjr_ano"],
        "tempo_1a_decisao_dias": row["tempo_1a_decisao_dias"],
        "tempo_fonte": row["tempo_fonte"],
        "taxa_aceite_pct": row["taxa_aceite_pct"],
        "taxa_fonte": row["taxa_fonte"],
        "tipos_artigo": json.loads(row["tipos_artigo"] or "[]"),
        "topicos": json.loads(row["topicos"] or "[]"),
        "homepage_url": row["homepage_url"],
        "frequencia_busca": freq.get(row["issn_l"], 0),
        "pre_score": round(score, 4),
        "componentes": {k: round(v, 3) for k, v in comp.items()},
        "flags": flags,
        "metricas_verificado_em": row["metricas_verificado_em"],
        "doaj_verificado_em": row["doaj_verificado_em"],
    }
