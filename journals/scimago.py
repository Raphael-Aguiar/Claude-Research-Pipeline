"""Ingestão do Scimago Journal Rank (SJR) — CSV anual, uso não comercial.

Formatos aceitos:
- CSV oficial do scimagojr.com (separador ';', decimal ',', colunas
  Issn / Title / SJR / SJR Best Quartile / H index). O site está atrás
  de Cloudflare — baixar pelo navegador em
  https://www.scimagojr.com/journalrank.php (botão de download).
- CSV simples com header issn,titulo,sjr,quartil,h_index,ano (ex.: gerado
  a partir do espelho parquet do pacote sjrdata).

Re-ingestão anual sugerida: junho (nova edição do SJR).
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .db import hoje_iso, normalizar_issn


def _para_float(valor: str) -> float | None:
    valor = (valor or "").strip().replace(",", ".")
    try:
        return float(valor)
    except ValueError:
        return None


def _para_int(valor: str) -> int | None:
    try:
        return int(float((valor or "").strip().replace(",", ".")))
    except ValueError:
        return None


def ingerir_scimago(
    conn: sqlite3.Connection, arquivo: Path, ano: int | None = None
) -> dict:
    """Carrega um CSV do SJR para scimago_raw. Retorna estatísticas.

    Um registro por ISSN × ano (o campo Issn do Scimago pode listar
    vários ISSNs separados por vírgula — cada um vira uma linha).
    """
    with open(arquivo, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(8192)
        f.seek(0)
        sep = ";" if amostra.count(";") > amostra.count(",") else ","
        reader = csv.DictReader(f, delimiter=sep)
        colunas = {c.strip().lower(): c for c in reader.fieldnames or []}

        def col(*nomes):
            for n in nomes:
                if n in colunas:
                    return colunas[n]
            return None

        c_issn = col("issn")
        c_titulo = col("title", "titulo")
        c_sjr = col("sjr")
        c_quartil = col("sjr best quartile", "quartil", "sjr_best_quartile")
        c_h = col("h index", "h_index")
        c_ano = col("year", "ano")
        if not c_issn or not c_sjr:
            raise ValueError(
                f"CSV sem colunas issn/sjr reconhecíveis: {reader.fieldnames}"
            )

        agora = hoje_iso()
        gravados = 0
        invalidos = 0
        registros = []
        for row in reader:
            ano_row = _para_int(row.get(c_ano, "")) if c_ano else None
            ano_final = ano_row or ano
            if not ano_final:
                raise ValueError(
                    "CSV sem coluna de ano — informe --ano na ingestão"
                )
            for issn_bruto in (row.get(c_issn) or "").split(","):
                norm = normalizar_issn(issn_bruto)
                if not norm:
                    if issn_bruto.strip() and issn_bruto.strip() != "-":
                        invalidos += 1
                    continue
                registros.append(
                    (
                        norm,
                        (row.get(c_titulo) or "").strip() if c_titulo else "",
                        _para_float(row.get(c_sjr, "")),
                        (row.get(c_quartil) or "").strip() or None
                        if c_quartil else None,
                        _para_int(row.get(c_h, "")) if c_h else None,
                        ano_final,
                        agora,
                    )
                )
                gravados += 1

    conn.executemany(
        "INSERT OR REPLACE INTO scimago_raw "
        "(issn, titulo, sjr, quartil, h_index, ano, ingerido_em) "
        "VALUES (?,?,?,?,?,?,?)",
        registros,
    )
    conn.commit()
    return {"gravados": gravados, "issns_invalidos": invalidos}


def ingerir_categorias(conn: sqlite3.Connection, arquivo: Path) -> dict:
    """Carrega o CSV de categorias por quartil (issn, categoria, quartil, ano).

    Base da Porta A da Área 22. SJR é proxy do CiteScore — ver
    consolidacao.py.
    """
    agora = hoje_iso()
    registros = []
    invalidos = 0
    with open(arquivo, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issn = normalizar_issn(row.get("issn"))
            categoria = (row.get("categoria") or "").strip()
            quartil = (row.get("quartil") or "").strip().upper()
            ano = _para_int(row.get("ano", ""))
            if not issn or not categoria or quartil not in ("Q1", "Q2", "Q3", "Q4"):
                invalidos += 1
                continue
            registros.append((issn, categoria, quartil, ano or 0, agora))
    conn.executemany(
        "INSERT OR REPLACE INTO scimago_categorias "
        "(issn, categoria, quartil, ano, ingerido_em) VALUES (?,?,?,?,?)",
        registros,
    )
    conn.commit()
    issns = len({r[0] for r in registros})
    return {"linhas": len(registros), "issns": issns, "invalidos": invalidos}
