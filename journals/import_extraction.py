"""Importação validada das extrações LLM — barreira anti-alucinação.

Formato de entrada (JSON, produzido pelos subagentes baratos):

[
  {
    "issn_l": "1471-2458",
    "url": "https://bmcpublichealth.biomedcentral.com/",
    "tempo_1a_decisao_dias": 8,
    "tempo_trecho": "Submission to first decision (median): 8 days",
    "taxa_aceite_pct": null,
    "taxa_trecho": null,
    "tipos_artigo": ["research article", "systematic review"],
    "tipos_trecho": "Article types: Research, Systematic Review, ..."
  }, ...
]

Regras de aceitação (por campo):
1. O snapshot (issn_l, url) precisa existir na tabela snapshots.
2. O trecho citado precisa ser SUBSTRING do texto do snapshot
   (comparação com espaços normalizados). Sem trecho → campo rejeitado.
3. Valor null é sempre aceito (significa "não está na página").
Campo aceito grava valor + fonte (url) + verificado_em de hoje.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .db import get_periodico, hoje_iso, upsert_periodico

CAMPOS = {
    "tempo_1a_decisao_dias": ("tempo_trecho", "tempo_1a_decisao_dias",
                              "tempo_fonte", "tempo_verificado_em"),
    "taxa_aceite_pct": ("taxa_trecho", "taxa_aceite_pct",
                        "taxa_fonte", "taxa_verificado_em"),
    "tipos_artigo": ("tipos_trecho", "tipos_artigo",
                     "tipos_fonte", "tipos_verificado_em"),
}


def _normalizar_espacos(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().lower()


def importar(conn: sqlite3.Connection, arquivo: Path) -> int:
    registros = json.loads(arquivo.read_text(encoding="utf-8"))
    if isinstance(registros, dict):
        registros = registros.get("extracoes", [])

    aceitos = rejeitados = nulos = 0
    for reg in registros:
        issn_l = reg.get("issn_l")
        url = reg.get("url")
        row = get_periodico(conn, issn_l or "")
        if row is None:
            print(f"  ✗ {issn_l}: periódico não está na base — registro inteiro rejeitado")
            rejeitados += 1
            continue
        snap = conn.execute(
            "SELECT texto_path FROM snapshots WHERE issn_l = ? AND url = ?",
            (row["issn_l"], url),
        ).fetchone()
        if snap is None or not Path(snap["texto_path"]).exists():
            print(f"  ✗ {issn_l}: sem snapshot para {url} — registro rejeitado")
            rejeitados += 1
            continue
        texto_snapshot = _normalizar_espacos(
            Path(snap["texto_path"]).read_text(encoding="utf-8")
        )

        for campo, (chave_trecho, col_valor, col_fonte, col_data) in CAMPOS.items():
            valor = reg.get(campo)
            trecho = reg.get(chave_trecho)
            if valor is None:
                nulos += 1
                continue
            if not trecho or _normalizar_espacos(trecho) not in texto_snapshot:
                print(
                    f"  ✗ {issn_l}.{campo}: trecho citado NÃO consta do "
                    f"snapshot — rejeitado (possível alucinação)"
                )
                rejeitados += 1
                continue
            if campo == "tipos_artigo":
                valor_gravar = json.dumps(valor, ensure_ascii=False)
            else:
                valor_gravar = valor
            upsert_periodico(
                conn, row["issn_l"], **{
                    col_valor: valor_gravar,
                    col_fonte: url,
                    col_data: hoje_iso(),
                },
            )
            print(f"  ✓ {issn_l}.{campo} = {valor!r} (fonte: {url})")
            aceitos += 1

    print(
        f"\nImportação: {aceitos} campos aceitos, {rejeitados} rejeitados, "
        f"{nulos} nulos (dado ausente na página — ok)"
    )
    return 0
