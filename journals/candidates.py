"""Geração de candidatos e comando recommend (F2).

Candidatos = periódicos-fonte dos trabalhos mais similares ao tema no
OpenAlex /works (descoberta) ∪ lista curada do vault (curados) ∪ base
local já enriquecida. Novos ISSNs são enriquecidos on-the-fly
(cache-first), depois o rank.py filtra e pré-pontua.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

import requests

from ..config import DEFAULT_TIMEOUT, get_api_config
from .db import normalizar_issn
from .enrich import Contador, enriquecer_um

MAX_DESCOBERTA = 30   # máximo de periódicos novos enriquecidos por execução
POR_PAGINA = 100      # trabalhos analisados na descoberta


def montar_consulta(
    tema: str | None, perfil_path: str | None, manuscrito: str | None
) -> dict:
    """Constrói o perfil de busca a partir de tema, perfil JSON ou manuscrito."""
    if perfil_path:
        perfil = json.loads(Path(perfil_path).read_text(encoding="utf-8"))
        if not perfil.get("keywords_en") and not perfil.get("tema"):
            raise ValueError("perfil JSON precisa de 'keywords_en' ou 'tema'")
        return perfil
    if tema:
        return {"tema": tema, "keywords_en": [], "tipo_de_estudo": None}
    if manuscrito:
        texto = Path(manuscrito).read_text(encoding="utf-8")
        titulo = next(
            (l.lstrip("# ").strip() for l in texto.splitlines()
             if l.startswith("#")),
            "",
        )
        corpo = re.sub(r"[#*\[\]`>-]", " ", texto)
        primeiras = " ".join(corpo.split()[:120])
        return {
            "tema": f"{titulo}. {primeiras}",
            "keywords_en": [],
            "tipo_de_estudo": None,
        }
    raise ValueError("informe --tema, --perfil ou --manuscrito")


def descobrir_fontes(consulta: dict, email: str) -> Counter:
    """OpenAlex /works: fontes (ISSN-L) dos trabalhos mais similares ao tema."""
    termos = consulta.get("keywords_en") or []
    busca = " ".join(termos) if termos else consulta.get("tema", "")
    if not busca.strip():
        return Counter()
    params = {"search": busca[:500], "per-page": POR_PAGINA,
              "select": "primary_location"}
    if email:
        params["mailto"] = email
    resp = requests.get(
        "https://api.openalex.org/works", params=params,
        timeout=DEFAULT_TIMEOUT * 2,
    )
    resp.raise_for_status()
    contagem: Counter = Counter()
    for w in resp.json().get("results", []):
        src = ((w.get("primary_location") or {}).get("source") or {})
        issn_l = normalizar_issn(src.get("issn_l"))
        if issn_l:
            contagem[issn_l] += 1
    return contagem


def recomendar(
    conn: sqlite3.Connection,
    tema: str | None = None,
    perfil_path: str | None = None,
    manuscrito: str | None = None,
    top: int = 12,
    output: str | None = None,
    descoberta: bool = True,
) -> int:
    """Pipeline determinístico: candidatos → enriquecer → filtrar → pré-score."""
    from .rank import ranquear

    consulta = montar_consulta(tema, perfil_path, manuscrito)
    cfg = get_api_config()
    print(f"Tema: {consulta.get('tema', '')[:100]}")

    frequencia: Counter = Counter()
    if descoberta:
        print("Descobrindo periódicos via OpenAlex /works...")
        frequencia = descobrir_fontes(consulta, cfg.get("OPENALEX_EMAIL", ""))
        print(f"  {len(frequencia)} periódicos distintos nos "
              f"{sum(frequencia.values())} trabalhos mais similares")

    curados_issns = {
        r["issn_l"]
        for r in conn.execute(
            "SELECT issn_l FROM curados WHERE issn_l IS NOT NULL"
        )
    }
    ja_na_base = {
        r["issn_l"] for r in conn.execute("SELECT issn_l FROM periodicos")
    }

    novos = [
        issn for issn, _ in frequencia.most_common(MAX_DESCOBERTA * 3)
        if issn not in ja_na_base
    ][:MAX_DESCOBERTA]
    if novos:
        print(f"Enriquecendo {len(novos)} periódicos novos (cache-first)...")
        contador = Contador()
        for issn in novos:
            try:
                enriquecer_um(conn, issn, cfg, contador)
            except requests.RequestException as e:
                print(f"  {issn}: erro de rede ({e}) — pulando")
            time.sleep(0.1)

    candidatos = sorted(curados_issns | ja_na_base | set(novos))
    print(f"Candidatos totais: {len(candidatos)}")

    resultado = ranquear(
        conn, candidatos,
        frequencia=frequencia,
        curados=curados_issns,
        consulta=consulta,
        top=top,
    )
    resultado["consulta"] = consulta

    destino = Path(output) if output else Path("finalistas.json")
    destino.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nFinalistas ({len(resultado['finalistas'])}) salvos em {destino}")
    print("  (consolidação Área 22 — critério vigente; Qualis é retrospectivo)")
    for f in resultado["finalistas"]:
        if f.get("consolidado"):
            via = "A/B" if f["porta_b_scielo_sp"] and f["porta_a_status"] == "consolidado" \
                else ("B" if f["porta_b_scielo_sp"] else f["porta_a_quartil"])
            consol = f"✅ {via}"
        elif f["porta_a_status"] == "nao_por_scimago":
            consol = "⚠️ Q3/Q4"
        elif f["porta_a_status"] == "sem_categoria_saude":
            consol = "❌ s/saúde"
        else:
            consol = "❓ s/dado"
        print(
            f"  {f['pre_score']:.3f}  {consol:<9} APC {f['apc_display']:<14} "
            f"{f['titulo'][:50]}"
        )
    if resultado["excluidos"]:
        print(f"\nSem registro na base ({len(resultado['excluidos'])}):")
        for e in resultado["excluidos"][:10]:
            print(f"  - {e['titulo'][:55]} — {e['razao']}")
    return 0
