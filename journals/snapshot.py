"""Snapshot de páginas de editora — base auditável da extração LLM.

Captura o HTML da homepage do periódico (e páginas de métricas
conhecidas por editora), converte para texto e grava em
data/journals/snapshots/. A extração LLM (subagentes baratos) só pode
citar trechos que existam nesses textos — validação em
import_extraction.py.

Páginas JS-heavy (ex.: sciencedirect.com) rendem texto pobre: o dado
fica "não disponível", nunca é inferido.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from .config import SNAPSHOTS_DIR, TTL_SNAPSHOT, garantir_dirs
from .db import get_periodico, normalizar_issn


def urls_candidatas(row: sqlite3.Row) -> list[str]:
    """Homepage + páginas de métricas conhecidas por editora."""
    urls = []
    if row["homepage_url"]:
        urls.append(row["homepage_url"])
    editora = (row["editora"] or "").lower()
    if "elsevier" in editora:
        # Journal Insights expõe tempo de revisão por ISSN
        urls.append(f"https://journalinsights.elsevier.com/journals/{row['issn_l']}")
    return urls


def capturar(
    conn: sqlite3.Connection,
    issns: list[str] | None = None,
    finalistas_path: str | None = None,
) -> int:
    """Captura snapshots para os ISSNs dados ou para um JSON de finalistas."""
    garantir_dirs()
    alvos: list[str] = [n for n in map(normalizar_issn, issns or []) if n]
    if finalistas_path:
        dados = json.loads(Path(finalistas_path).read_text(encoding="utf-8"))
        alvos += [f["issn_l"] for f in dados.get("finalistas", [])]
    if not alvos:
        print("Nada a capturar. Use --issn ou --finalistas.")
        return 1

    capturados = pulados = falhas = 0
    for issn in dict.fromkeys(alvos):
        row = get_periodico(conn, issn)
        if row is None:
            print(f"  {issn}: não está na base — enrich primeiro")
            continue
        for url in urls_candidatas(row):
            estado = _capturar_url(conn, row["issn_l"], url)
            if estado == "fresco":
                pulados += 1
            elif estado == "ok":
                capturados += 1
            else:
                falhas += 1
    print(
        f"\nSnapshots: {capturados} capturados, {pulados} frescos (cache), "
        f"{falhas} falhas"
    )
    return 0


def _capturar_url(conn: sqlite3.Connection, issn_l: str, url: str) -> str:
    existente = conn.execute(
        "SELECT capturado_em, texto_path FROM snapshots "
        "WHERE issn_l = ? AND url = ?",
        (issn_l, url),
    ).fetchone()
    if existente and Path(existente["texto_path"]).exists():
        capturado = date.fromisoformat(existente["capturado_em"][:10])
        if date.today() - capturado <= timedelta(days=TTL_SNAPSHOT):
            return "fresco"

    try:
        resp = requests.get(
            url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  {issn_l} {url}: falha ({e})")
        return "falha"

    texto = html_para_texto(resp.text)
    if len(texto) < 200:
        print(f"  {issn_l} {url}: texto muito curto ({len(texto)} chars) — "
              "página provavelmente JS-heavy; dado ficará indisponível")
        return "falha"

    slug = re.sub(r"[^a-z0-9]+", "-", url.lower())[:80].strip("-")
    destino = SNAPSHOTS_DIR / f"{issn_l}__{slug}.txt"
    destino.write_text(texto, encoding="utf-8")
    conn.execute(
        "INSERT OR REPLACE INTO snapshots (issn_l, url, capturado_em, "
        "texto_path) VALUES (?,?,?,?)",
        (issn_l, url, date.today().isoformat(), str(destino)),
    )
    conn.commit()
    print(f"  {issn_l}: {url} → {destino.name} ({len(texto):,} chars)")
    return "ok"


def html_para_texto(html: str) -> str:
    """HTML → texto limpo (remove script/style/nav, colapsa espaços)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    texto = soup.get_text(separator="\n")
    linhas = [l.strip() for l in texto.splitlines()]
    return "\n".join(l for l in linhas if l)
