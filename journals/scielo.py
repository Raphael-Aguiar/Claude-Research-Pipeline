"""Ingestão da coleção SciELO Saúde Pública (Porta B da Área 22).

Fonte: articlemeta (coleção 'spa'), lista estável de ~20 periódicos.
O JSON é gerado uma vez (data/journals/raw/scielo_saude_publica.json) e
ingerido aqui. Membership é necessária para a Porta B; o limiar de h5 >
percentil 60 (Google Scholar Metrics) permanece verificação MANUAL —
não há API para o h5.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import hoje_iso, normalizar_issn

FONTE = "Coleção SciELO Saúde Pública (articlemeta, coleção 'spa')"


def ingerir_scielo_sp(conn: sqlite3.Connection, arquivo: Path) -> dict:
    """Carrega o JSON da coleção SciELO Saúde Pública em scielo_sp."""
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    agora = hoje_iso()
    registros = []
    for j in dados:
        titulo = j.get("titulo")
        for issn in j.get("issns", [j.get("issn")]):
            norm = normalizar_issn(issn)
            if norm:
                registros.append((norm, titulo, FONTE, agora))
    conn.executemany(
        "INSERT OR REPLACE INTO scielo_sp (issn, titulo, fonte, ingerido_em) "
        "VALUES (?,?,?,?)",
        registros,
    )
    conn.commit()
    return {"issns": len(registros), "periodicos": len(dados)}
