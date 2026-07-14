"""Enriquecimento cache-first de periódicos via APIs abertas (sem LLM).

Ordem por periódico: OpenAlex /sources (identidade canônica, métricas,
tópicos, OA) → DOAJ (APC, licença, peer review) → NLM Catalog (MEDLINE)
→ lookups locais (Qualis 2021-2024, SJR). Crossref entra como fallback
de identidade quando o OpenAlex não conhece o ISSN.

Cache = o próprio banco: cada família de campos tem <família>_verificado_em
e um TTL (config.py). Campo fresco não gera chamada HTTP — a segunda
execução consecutiva para o mesmo periódico faz zero requisições.
"""

from __future__ import annotations

import json
import sqlite3
import time

import requests

from ..config import get_api_config
from .config import TTL_DOAJ, TTL_MEDLINE, TTL_METRICAS
from .db import (campo_vencido, get_periodico, hoje_iso, normalizar_issn,
                 qualis_para_issns, sjr_para_issns, upsert_periodico)
from .apis import crossref_journals, doaj, nlm_catalog, openalex_sources

PAUSA_ENTRE_CHAMADAS = 0.15  # rate limit cortês


class Contador:
    """Conta chamadas HTTP para auditoria do cache."""

    def __init__(self):
        self.http = 0


def enriquecer(
    conn: sqlite3.Connection,
    issns: list[str] | None = None,
    todos: bool = False,
    refresh: bool = False,
) -> int:
    """Enriquece os periódicos indicados (ou todos da base)."""
    alvos: list[str] = []
    if issns:
        alvos = [n for n in (normalizar_issn(i) for i in issns) if n]
        invalidos = [i for i in issns if not normalizar_issn(i)]
        for i in invalidos:
            print(f"  ISSN inválido ignorado: {i!r}")
    elif todos:
        alvos = [
            r["issn_l"]
            for r in conn.execute("SELECT issn_l FROM periodicos ORDER BY issn_l")
        ]
    if not alvos:
        print("Nada a enriquecer. Use --issn XXXX-XXXX ou --all.")
        return 1

    cfg = get_api_config()
    contador = Contador()
    for issn in alvos:
        try:
            enriquecer_um(conn, issn, cfg, contador, refresh=refresh)
        except requests.RequestException as e:
            print(f"  {issn}: erro de rede ({e}) — pulando, dado fica NULL")
    print(f"\nConcluído. Chamadas HTTP: {contador.http}")
    return 0


def enriquecer_um(
    conn: sqlite3.Connection,
    issn: str,
    cfg: dict,
    contador: Contador,
    refresh: bool = False,
) -> None:
    """Enriquece um periódico, família a família, respeitando o cache."""
    row = get_periodico(conn, issn)
    issn_l = row["issn_l"] if row else issn
    rotulo = (row["titulo"] if row and row["titulo"] else issn_l)
    print(f"• {rotulo} ({issn_l})")

    # --- OpenAlex: identidade + métricas + tópicos -------------------------
    if refresh or row is None or campo_vencido(row, "metricas_verificado_em", TTL_METRICAS):
        src = openalex_sources.buscar_source_por_issn(
            issn, email=cfg.get("OPENALEX_EMAIL", "")
        )
        contador.http += 1
        time.sleep(PAUSA_ENTRE_CHAMADAS)
        if src and src.get("issn_l"):
            issn_l = normalizar_issn(src["issn_l"]) or issn_l
            issns_todos = sorted(
                {n for n in map(normalizar_issn, [issn_l, *src["issns"]]) if n}
            )
            upsert_periodico(
                conn, issn_l,
                titulo=src["titulo"],
                issns=json.dumps(issns_todos),
                editora=src["editora"],
                pais=src["pais"],
                homepage_url=src["homepage_url"],
                openalex_id=src["openalex_id"],
                is_oa=_como_int(src["is_oa"]),
                in_doaj=_como_int(src["in_doaj"]),
                topicos=json.dumps(src["topicos"], ensure_ascii=False),
                citedness_2yr=src["citedness_2yr"],
                h_index=src["h_index"],
                works_count=src["works_count"],
                metricas_verificado_em=hoje_iso(),
            )
            # APC de fallback (origem declarada: OpenAlex, que espelha DOAJ)
            if src.get("apc_usd") is not None:
                atual = get_periodico(conn, issn_l)
                if atual["apc_usd"] is None:
                    upsert_periodico(
                        conn, issn_l,
                        apc_usd=float(src["apc_usd"]),
                        apc_fonte="OpenAlex /sources (apc_usd; origem DOAJ)",
                    )
            print("    OpenAlex: métricas/tópicos atualizados")
        else:
            # Fallback de identidade via Crossref
            cj = crossref_journals.buscar_journal_por_issn(
                issn, email=cfg.get("CROSSREF_EMAIL", "")
            )
            contador.http += 1
            time.sleep(PAUSA_ENTRE_CHAMADAS)
            if cj:
                issns_cj = [n for n in map(normalizar_issn, cj["issns"]) if n]
                upsert_periodico(
                    conn, issn_l,
                    titulo=cj["titulo"],
                    issns=json.dumps(sorted(set([issn_l, *issns_cj]))),
                    editora=cj["editora"],
                    metricas_verificado_em=hoje_iso(),
                )
                print("    OpenAlex sem registro; identidade via Crossref")
            else:
                print("    OpenAlex e Crossref sem registro para este ISSN")
    else:
        print("    métricas: cache fresco")

    row = get_periodico(conn, issn_l)
    if row is None:
        print("    periódico não identificável — abortando este ISSN")
        return
    issn_l = row["issn_l"]
    issns_conhecidos = json.loads(row["issns"] or "[]") or [issn_l]

    # --- DOAJ: APC, licença, peer review ----------------------------------
    if refresh or campo_vencido(row, "doaj_verificado_em", TTL_DOAJ):
        dj = None
        for i in issns_conhecidos:
            dj = doaj.buscar_journal_por_issn(i)
            contador.http += 1
            time.sleep(PAUSA_ENTRE_CHAMADAS)
            if dj:
                break
        if dj:
            campos = {
                "in_doaj": 1,
                "licenca": dj["licenca"],
                "peer_review": dj["peer_review"],
                "apc_waiver": _como_int(dj["apc_waiver"]),
                "doaj_verificado_em": hoje_iso(),
            }
            if dj["tem_apc"] and dj["apc_valor"] is not None:
                fonte = f"DOAJ API v4 (issn {issn_l})"
                if dj.get("apc_url"):
                    fonte += f" — fees: {dj['apc_url']}"
                campos.update(
                    apc_valor=float(dj["apc_valor"]),
                    apc_moeda=dj["apc_moeda"],
                    apc_fonte=fonte,
                    apc_verificado_em=hoje_iso(),
                )
                if dj["apc_moeda"] == "USD":
                    campos["apc_usd"] = float(dj["apc_valor"])
            elif not dj["tem_apc"]:
                campos.update(
                    apc_valor=0.0,
                    apc_moeda=None,
                    apc_usd=0.0,
                    apc_fonte=f"DOAJ API v4 — sem APC (issn {issn_l})",
                    apc_verificado_em=hoje_iso(),
                )
            upsert_periodico(conn, issn_l, **campos)
            print("    DOAJ: APC/licença atualizados")
        else:
            upsert_periodico(
                conn, issn_l, in_doaj=0, doaj_verificado_em=hoje_iso()
            )
            print("    DOAJ: não listado (registrado como fora do DOAJ)")
    else:
        print("    DOAJ: cache fresco")

    # --- NLM Catalog: MEDLINE ----------------------------------------------
    row = get_periodico(conn, issn_l)
    if refresh or campo_vencido(row, "medline_verificado_em", TTL_MEDLINE):
        indexado = None
        for i in issns_conhecidos:
            indexado = nlm_catalog.medline_indexado(
                i, email=cfg.get("NCBI_EMAIL", ""),
                api_key=cfg.get("NCBI_API_KEY", ""),
            )
            contador.http += 1
            time.sleep(PAUSA_ENTRE_CHAMADAS)
            if indexado:
                break
        if indexado is not None:
            upsert_periodico(
                conn, issn_l,
                medline_indexado=_como_int(indexado),
                medline_verificado_em=hoje_iso(),
            )
            print(f"    MEDLINE: {'indexado' if indexado else 'não indexado'}")
    else:
        print("    MEDLINE: cache fresco")

    # --- Lookups locais (sem HTTP): Qualis + SJR ---------------------------
    q = qualis_para_issns(conn, issns_conhecidos)
    if q:
        upsert_periodico(
            conn, issn_l,
            qualis_estrato=q["estrato"],
            qualis_areas=q["areas"],
            qualis_fonte=q["fonte"],
            qualis_verificado_em=q["ingerido_em"],
        )
    s = sjr_para_issns(conn, issns_conhecidos)
    if s:
        upsert_periodico(
            conn, issn_l,
            sjr=s["sjr"], sjr_quartil=s["quartil"], sjr_ano=s["ano"],
        )

    # Consolidação Área 22 (critério VIGENTE) — derivada de tabelas locais
    # (categorias Scimago + coleção SciELO SP), sem HTTP.
    from .consolidacao import aplicar as aplicar_consolidacao

    v = aplicar_consolidacao(conn, issn_l, issns_conhecidos)
    qualis_txt = q["estrato"] if q else "não classificado"
    cat_txt = ""
    if v["porta_a_categoria"]:
        cat_txt = f" {v['porta_a_categoria']} {v['porta_a_quartil']}"
    porta_b_txt = "sim" if v["porta_b_scielo_sp"] else "não"
    print(
        f"    Qualis (legado 2021-2024): {qualis_txt} | "
        f"Consolidado Área 22: {'SIM' if v['consolidado'] else 'não'} "
        f"(Porta A: {v['porta_a_status']}{cat_txt}; "
        f"Porta B SciELO-SP: {porta_b_txt})"
    )


def _como_int(valor) -> int | None:
    if valor is None:
        return None
    return 1 if valor else 0
