"""Regeneração da Nota de Revistas do vault com dados verificados.

Gera o CONTEÚDO ATUALIZADO da Nota "Revistas para publicação — IA em
saúde": mesmos periódicos curados, reagrupados pela CONSOLIDAÇÃO da Área
22 (critério vigente, ciclo 2025-2028) — não mais pelo Qualis, que virou
retrospectivo — e com APC verificada (DOAJ) no lugar da APC de 2025.

Por padrão escreve um PREVIEW (arquivo separado) — a substituição da
Nota real é decisão de Raphael após ver o diff.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .config import NOTA_REVISTAS
from .db import get_periodico


def exportar_nota(conn: sqlite3.Connection, output: Path | None = None) -> int:
    curados = conn.execute(
        "SELECT * FROM curados WHERE issn_l IS NOT NULL ORDER BY titulo"
    ).fetchall()
    if not curados:
        print("Tabela curados vazia — rode seed-from-vault primeiro.")
        return 1

    # Agrupa pela consolidação Área 22 (critério VIGENTE), não mais pelo
    # Qualis (retrospectivo). Ordem: consolidado > borderline > fora de porta.
    grupos: dict[str, list[str]] = {}
    for c in curados:
        row = get_periodico(conn, c["issn_l"])
        if row is None:
            continue
        grupos.setdefault(_grupo_consolidacao(row), []).append(_bullet(c, row))

    hoje = date.today().isoformat()
    linhas = [
        "---",
        'titulo: "Revistas para publicação — IA em saúde"',
        "criado: 2025-05-23",
        f"atualizado: {hoje}",
        "Objeto: Nota",
        "---",
        "",
        "# Revistas para publicação — IA em saúde",
        "",
        "Lista curada de periódicos com escopo de IA em saúde / informática "
        "médica / saúde pública digital. **Agrupados pela consolidação da Área "
        f"22 (ciclo CAPES 2025-2028), reavaliada em {hoje}** — critério vigente "
        "que substituiu o Qualis Periódicos (descontinuado). Consolidação via "
        "quartil SJR é *proxy* do CiteScore/Scopus — confirmar em "
        "scopus.com/sources antes de submeter. APCs verificadas no DOAJ na "
        "data indicada; \"não disponível\" = nenhuma fonte aberta confiável.",
        "",
    ]
    ordem_grupos = [
        ("consolidado", "Consolidados (Porta A Q1/Q2 ou Porta B SciELO-SP)"),
        ("borderline", "Borderline — saúde só em Q3/Q4 pelo SJR (conferir CiteScore)"),
        ("fora", "Fora de porta pelo Scopus — só via destaque qualitativo"),
        ("sem_dado", "Sem dado de categoria — verificar manual"),
    ]
    for chave, titulo_secao in ordem_grupos:
        if chave not in grupos:
            continue
        linhas += [f"## {titulo_secao}", ""]
        linhas += grupos[chave]
        linhas.append("")

    linhas += [
        "## Notas",
        "",
        "- Base completa (APC, OA, MEDLINE, categorias Scopus, consolidação): "
        "`~/bin/escrita-tooling/data/journals/periodicos.db` "
        "(`python -m tools journals status`).",
        "- Recomendações por manuscrito/semente: skill `periodicos-alvo`.",
        "- O Qualis 2021-2024 (mostrado como legado em cada item) só classifica "
        "artigos publicados até 2024 — a CAPES descontinuou o Qualis Periódicos.",
        "",
    ]

    conteudo = "\n".join(linhas)
    destino = output or NOTA_REVISTAS.with_suffix(".preview.md")
    destino.write_text(conteudo, encoding="utf-8")
    print(f"Preview gerado: {destino}")
    print("Revisar o diff com a Nota antes de substituir "
          f"({NOTA_REVISTAS}).")
    return 0


def _grupo_consolidacao(row: sqlite3.Row) -> str:
    """Chave de agrupamento por consolidação Área 22."""
    if row["consolidado"] == 1:
        return "consolidado"
    if row["porta_a_status"] == "nao_por_scimago":
        return "borderline"
    if row["porta_a_status"] == "sem_categoria_saude":
        return "fora"
    return "sem_dado"


def _bullet(curado: sqlite3.Row, row: sqlite3.Row) -> str:
    partes = [f"- **{curado['titulo']}**"]
    if curado["escopo_texto"]:
        partes.append(f" — {curado['escopo_texto']}.")
    # Consolidação (evidência)
    if row["consolidado"] == 1:
        vias = []
        if row["porta_a_status"] == "consolidado":
            vias.append(f"Porta A: {row['porta_a_categoria']} {row['porta_a_quartil']}")
        if row["porta_b_scielo_sp"] == 1:
            vias.append("Porta B: SciELO-SP")
        partes.append(f" Consolidado ({'; '.join(vias)}).")
    elif row["porta_a_status"] == "nao_por_scimago":
        partes.append(
            f" Saúde só em {row['porta_a_quartil']} pelo SJR — conferir CiteScore."
        )
    elif row["porta_a_status"] == "sem_categoria_saude":
        partes.append(" Sem categoria de saúde no Scopus.")
    # APC
    if row["apc_valor"] == 0:
        apc = "Taxa: zero"
    elif row["apc_valor"] is not None:
        apc = f"Taxa {row['apc_moeda']} {row['apc_valor']:,.0f}"
    elif row["apc_usd"] is not None:
        apc = f"Taxa ~US$ {row['apc_usd']:,.0f}"
    else:
        apc = "Taxa: não disponível"
    verificado = row["apc_verificado_em"] or row["doaj_verificado_em"]
    if verificado and "não disponível" not in apc:
        apc += f" (verificada em {verificado})"
    partes.append(f" {apc}.")
    # Qualis legado
    if row["qualis_estrato"]:
        partes.append(f" Qualis 2021-24 (legado): {row['qualis_estrato']}.")
    url = curado["url"] or row["homepage_url"]
    if url:
        partes.append(f" [Link]({url})")
    return "".join(partes)
