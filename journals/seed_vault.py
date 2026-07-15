"""Carrega listas de curadoria do vault para a tabela `curados` ("semear").

TERMINOLOGIA (regra dura, 2026-07-15): o substantivo aqui é sempre
"lista de curadoria" — NUNCA "semente", que é objeto canônico do vault
(~/PKM/Sementes/, ideia de publicação) e não pode nomear outro conceito.
O verbo "semear" para esta ação foi aceito por Raphael; o comando é
`seed-from-vault`. Ver wiki/sistema/radar-qualis.md §Vocabulário.

Fonte primária: ~/PKM/Notas/Revistas para publicação — IA em saúde.md
(bullets `- **Nome** — escopo. Taxa .... [Link](url)` sob headers ## A1..A4).

O estrato e a APC da lista são registrados como *texto de origem* em
`curados` (não são fonte de verdade — Qualis vem de qualis_raw e APC do
DOAJ). A resolução de ISSN é por título via OpenAlex /sources (dá o
ISSN-L canônico) com fallback Crossref /journals; abaixo do limiar de
similaridade o periódico fica pendente (issn_l NULL) — nunca se chuta.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
from rapidfuzz import fuzz

from ..config import DEFAULT_TIMEOUT, get_api_config
from .db import normalizar_issn, upsert_periodico

LIMIAR_SIMILARIDADE = 85.0

_BULLET_RE = re.compile(r"^\s*-\s+\*\*(?P<titulo>.+?)\*\*\s*[—–-]\s*(?P<resto>.+)$")
_ESTRATO_RE = re.compile(r"^##\s+(A[1-4]|B[1-5]|C)\s*$")
_LINK_RE = re.compile(r"\[Link\]\((?P<url>[^)]+)\)")
_TAXA_RE = re.compile(r"Taxa:?\s*(?P<apc>.+?)(?=\s+Citado|\s+\[Link\]|$)")


def parse_lista_markdown(path: Path) -> list[dict]:
    """Extrai periódicos de uma lista curada em Markdown."""
    entradas: list[dict] = []
    estrato = None
    for linha in path.read_text(encoding="utf-8").splitlines():
        m_estrato = _ESTRATO_RE.match(linha.strip())
        if m_estrato:
            estrato = m_estrato.group(1)
            continue
        if linha.strip().startswith("##"):
            estrato = None  # seção que não é estrato (ex.: ## Notas)
            continue
        m = _BULLET_RE.match(linha)
        if not m:
            continue
        # Estrato é opcional: listas curadas por tema (sem headers de estrato
        # Qualis) entram com estrato_origem=None. Bullets sem título em negrito
        # (`- texto`) não casam _BULLET_RE e são ignorados.
        resto = m.group("resto").strip()
        url = None
        m_link = _LINK_RE.search(resto)
        if m_link:
            url = m_link.group("url").strip()
            resto = _LINK_RE.sub("", resto).strip()
        apc_texto = None
        m_taxa = _TAXA_RE.search(resto)
        if m_taxa:
            apc_texto = m_taxa.group("apc").strip().rstrip(".")
        escopo = resto.split(" Taxa")[0].strip().rstrip(".")
        entradas.append(
            {
                "titulo": m.group("titulo").strip(),
                "estrato_origem": estrato,
                "escopo_texto": escopo or None,
                "apc_texto": apc_texto,
                "url": url,
            }
        )
    return entradas


def _buscar_openalex(titulo: str, email: str) -> dict | None:
    """Busca o periódico no OpenAlex /sources por título."""
    params = {"search": titulo, "per-page": 5}
    if email:
        params["mailto"] = email
    resp = requests.get(
        "https://api.openalex.org/sources", params=params, timeout=DEFAULT_TIMEOUT
    )
    resp.raise_for_status()
    melhores = (None, 0.0)
    for src in resp.json().get("results", []):
        nome = src.get("display_name") or ""
        sim = fuzz.token_set_ratio(titulo.lower(), nome.lower())
        if sim > melhores[1]:
            melhores = (src, sim)
    src, sim = melhores
    if not src:
        return None
    issns = [n for n in (normalizar_issn(i) for i in src.get("issn") or []) if n]
    issn_l = normalizar_issn(src.get("issn_l"))
    if not issn_l:
        return None
    return {
        "issn_l": issn_l,
        "issns": issns or [issn_l],
        "titulo_fonte": src.get("display_name"),
        "similaridade": sim,
        "via": "openalex",
    }


def _buscar_crossref(titulo: str, email: str) -> dict | None:
    """Fallback: Crossref /journals por título (sem ISSN-L; usa 1º ISSN)."""
    params = {"query": titulo, "rows": 5}
    if email:
        params["mailto"] = email
    resp = requests.get(
        "https://api.crossref.org/journals", params=params, timeout=DEFAULT_TIMEOUT
    )
    resp.raise_for_status()
    melhores = (None, 0.0)
    for item in resp.json().get("message", {}).get("items", []):
        nome = item.get("title") or ""
        sim = fuzz.token_set_ratio(titulo.lower(), nome.lower())
        if sim > melhores[1]:
            melhores = (item, sim)
    item, sim = melhores
    if not item:
        return None
    issns = [n for n in (normalizar_issn(i) for i in item.get("ISSN") or []) if n]
    if not issns:
        return None
    return {
        "issn_l": issns[0],
        "issns": issns,
        "titulo_fonte": item.get("title"),
        "similaridade": sim,
        "via": "crossref",
    }


def semear(
    conn: sqlite3.Connection,
    path: Path,
    origem: str,
    resolver: bool = True,
) -> dict:
    """Carrega a lista curada em `curados` e resolve ISSNs por título.

    Periódicos resolvidos também ganham linha mínima em `periodicos`
    (issn_l, issns, titulo) para o enriquecimento posterior.
    """
    entradas = parse_lista_markdown(path)
    email = get_api_config().get("OPENALEX_EMAIL", "")
    agora = datetime.now().isoformat(timespec="seconds")
    resolvidos = 0
    pendentes: list[str] = []

    for e in entradas:
        resolucao = None
        if resolver:
            try:
                resolucao = _buscar_openalex(e["titulo"], email)
            except requests.RequestException as exc:
                print(f"    OpenAlex indisponível para {e['titulo']!r}: {exc}")
            if not resolucao or resolucao["similaridade"] < LIMIAR_SIMILARIDADE:
                try:
                    alt = _buscar_crossref(e["titulo"], email)
                except requests.RequestException:
                    alt = None
                if alt and alt["similaridade"] > (
                    resolucao["similaridade"] if resolucao else 0
                ):
                    resolucao = alt
            time.sleep(0.2)

        aceito = bool(
            resolucao and resolucao["similaridade"] >= LIMIAR_SIMILARIDADE
        )
        conn.execute(
            "INSERT OR REPLACE INTO curados (origem, titulo, issn_l, issns, "
            "estrato_origem, apc_texto, url, escopo_texto, similaridade, "
            "resolvido_via, resolvido_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                origem,
                e["titulo"],
                resolucao["issn_l"] if aceito else None,
                json.dumps(resolucao["issns"]) if aceito else None,
                e["estrato_origem"],
                e["apc_texto"],
                e["url"],
                e["escopo_texto"],
                resolucao["similaridade"] if resolucao else None,
                resolucao["via"] if aceito else None,
                agora if aceito else None,
            ),
        )
        if aceito:
            upsert_periodico(
                conn,
                resolucao["issn_l"],
                titulo=e["titulo"],
                issns=json.dumps(resolucao["issns"]),
                homepage_url=e["url"],
            )
            resolvidos += 1
            print(
                f"  ✓ {e['titulo']} → {resolucao['issn_l']} "
                f"({resolucao['via']}, sim {resolucao['similaridade']:.0f})"
            )
        else:
            pendentes.append(e["titulo"])
            sim = resolucao["similaridade"] if resolucao else 0
            print(f"  ✗ {e['titulo']} — não resolvido (melhor sim {sim:.0f})")

    conn.commit()
    return {
        "entradas": len(entradas),
        "resolvidos": resolvidos,
        "pendentes": pendentes,
    }
