"""Relatório Markdown da recomendação — todo dado com fonte e data.

Critério VIGENTE: consolidação da Área 22 (ciclo 2025-2028). O Qualis
2021-2024 aparece apenas como campo LEGADO (retrospectivo: só classifica
artigos publicados até 2024).

Sem julgamento LLM: tabela + fichas com proveniência.
Com julgamento (JSON do modelo forte): shortlist narrada primeiro.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .consolidacao import rotulo_status

CAVEATS = (
    "> **Como ler a consolidação (Área 22, ciclo 2025-2028).** O Qualis "
    "Periódicos foi descontinuado; para artigos publicados de 2025 em diante "
    "vale a *consolidação* do periódico. **Porta A**: categoria de saúde no "
    "Scopus/WoS com percentil ≥50 (Q1/Q2). **Porta B**: coleção SciELO Saúde "
    "Pública com h5 > percentil 60.\n"
    ">\n"
    "> **Dois cuidados de honestidade**: (1) usamos o quartil **SJR (Scimago)** "
    "como *proxy* do CiteScore/Scopus — correlato, não idêntico; um verdict "
    "\"consolidado\" deve ser **confirmado no CiteScore (scopus.com/sources)**. "
    "(2) Um verdict **negativo pela Porta A não é definitivo** — o periódico "
    "ainda pode cumprir via CiteScore/JIF real ou via Web of Science, que não "
    "consultamos. A categoria que casou é sempre mostrada.\n"
    ">\n"
    "> **Qualis 2021-2024 = legado**: só vale para classificar artigos "
    "publicados **até 2024**. Não use para decidir onde submeter agora."
)


def _consol_curto(f: dict) -> str:
    """Rótulo curto de consolidação para a tabela."""
    if f.get("consolidado"):
        vias = []
        if f["porta_a_status"] == "consolidado":
            vias.append(f"A:{f['porta_a_categoria']} {f['porta_a_quartil']}")
        if f["porta_b_scielo_sp"]:
            vias.append("B:SciELO-SP")
        return "✅ " + " + ".join(vias)
    if f["porta_a_status"] == "nao_por_scimago":
        return f"⚠️ saúde {f['porta_a_quartil']} (SJR)"
    if f["porta_a_status"] == "sem_categoria_saude":
        return "❌ sem cat. saúde"
    return "❓ sem dado"


def gerar_relatorio(
    finalistas_path: Path,
    julgamento_path: Path | None,
    output: Path,
) -> int:
    dados = json.loads(finalistas_path.read_text(encoding="utf-8"))
    julgamento = (
        json.loads(julgamento_path.read_text(encoding="utf-8"))
        if julgamento_path else None
    )
    linhas: list[str] = []
    consulta = dados.get("consulta", {})

    linhas += [
        f"# Radar Qualis — recomendação de periódicos de {date.today().isoformat()}",
        "",
        f"**Tema**: {consulta.get('tema', '—')}",
    ]
    if consulta.get("tipo_de_estudo"):
        linhas.append(f"**Tipo de estudo**: {consulta['tipo_de_estudo']}")
    linhas += ["", CAVEATS, ""]

    if julgamento:
        linhas += ["## Shortlist (julgamento de fit)", ""]
        aviso = julgamento.get("aviso") or (
            "Chance de aceite é estimativa qualitativa do modelo, "
            "não probabilidade."
        )
        linhas += [f"> {aviso}", ""]
        for i, item in enumerate(julgamento.get("shortlist", []), 1):
            linhas += [
                f"### {i}. {item['titulo']}",
                "",
                f"- **Chance estimada**: {item.get('chance_estimada', '—')} "
                f"(estimativa qualitativa)",
                f"- **Justificativa**: {item.get('justificativa', '—')}",
            ]
            if item.get("trade_offs"):
                linhas.append(f"- **Trade-offs**: {item['trade_offs']}")
            linhas.append("")

    linhas += ["## Finalistas (dados verificados)", ""]
    linhas += [
        "| # | Periódico | Consolidado (Área 22) | Qualis 21-24 (legado) | "
        "APC | OA | MEDLINE | Citações/2a |",
        "|---|-----------|-----------------------|-----------------------|"
        "-----|----|---------| ------------|",
    ]
    for i, f in enumerate(dados["finalistas"], 1):
        oa = {1: "sim", 0: "não"}.get(f["is_oa"], "n/d")
        med = {1: "sim", 0: "não"}.get(f["medline_indexado"], "n/d")
        cit = (f"{f['citedness_2yr']:.2f}"
               if f["citedness_2yr"] is not None else "n/d")
        qualis = f["qualis_estrato"] or "não classif."
        linhas.append(
            f"| {i} | {f['titulo']} | {_consol_curto(f)} | {qualis} "
            f"| {f['apc_display']} | {oa} | {med} | {cit} |"
        )
    linhas.append("")

    linhas += ["## Fichas com proveniência", ""]
    for f in dados["finalistas"]:
        linhas += _ficha(f)

    if dados.get("excluidos"):
        linhas += ["## Sem registro na base", ""]
        for e in dados["excluidos"]:
            linhas.append(f"- **{e['titulo']}** — {e['razao']}")
        linhas.append("")

    output.write_text("\n".join(linhas), encoding="utf-8")
    print(f"Relatório: {output}")
    return 0


def _ficha(f: dict) -> list[str]:
    linhas = [f"### {f['titulo']}", ""]
    linhas.append(f"- ISSN-L: `{f['issn_l']}` · Editora: {f['editora'] or 'n/d'}")

    # Consolidação (critério vigente) — em primeiro lugar
    consol = "**SIM**" if f.get("consolidado") else "**não** (pelo que temos)"
    linhas.append(f"- Consolidado Área 22 (2025-2028): {consol}")
    linhas.append(f"    - Porta A: {rotulo_status(f['porta_a_status'])}")
    if f["porta_a_categorias"]:
        cats = "; ".join(
            f"{c['categoria']} {c['quartil']}" for c in f["porta_a_categorias"][:6]
        )
        linhas.append(f"    - Categorias de saúde (Scimago/proxy): {cats}")
    sp = "sim" if f["porta_b_scielo_sp"] else "não"
    linhas.append(
        f"    - Porta B (SciELO Saúde Pública): {sp}"
        + (" — confirmar h5 > percentil 60 no Google Scholar Metrics"
           if f["porta_b_scielo_sp"] else "")
    )
    if f["consolidacao_fonte"]:
        linhas.append(
            f"    - Fonte: {f['consolidacao_fonte']} "
            f"(verificado em {f['consolidacao_verificado_em']})"
        )

    # Qualis legado
    if f["qualis_estrato"]:
        linhas.append(
            f"- Qualis 2021-2024 (LEGADO — só artigos até 2024): "
            f"**{f['qualis_estrato']}** (fonte: {f['qualis_fonte']})"
        )
    else:
        linhas.append("- Qualis 2021-2024 (legado): não classificado")

    # APC
    if f["apc_fonte"]:
        linhas.append(
            f"- APC: {f['apc_display']}"
            + (" · waiver disponível" if f["apc_waiver"] else "")
            + f" (fonte: {f['apc_fonte']}; verificado em "
            + f"{f['apc_verificado_em'] or 'n/d'})"
        )
    else:
        linhas.append("- APC: não disponível — verificar na página da revista")
    oa = {1: "sim", 0: "não"}.get(f["is_oa"], "não disponível")
    doaj = {1: "sim", 0: "não"}.get(f["in_doaj"], "não disponível")
    linhas.append(
        f"- Open access: {oa} · DOAJ: {doaj} · Licença: {f['licenca'] or 'n/d'}"
        f" · Peer review: {f['peer_review'] or 'n/d'}"
    )
    med = {1: "sim", 0: "não"}.get(f["medline_indexado"], "não verificado")
    linhas.append(
        f"- MEDLINE: {med} (NLM Catalog, verificado em "
        f"{f['medline_verificado_em'] or 'n/d'})"
    )
    metricas = []
    if f["citedness_2yr"] is not None:
        metricas.append(f"citações/2 anos {f['citedness_2yr']:.2f}")
    if f["h_index"] is not None:
        metricas.append(f"h-index {f['h_index']}")
    if f["sjr_quartil"]:
        metricas.append(f"SJR geral {f['sjr_quartil']} ({f['sjr_ano']})")
    if metricas:
        linhas.append(
            f"- Métricas: {' · '.join(metricas)} (OpenAlex, verificado em "
            f"{f['metricas_verificado_em']}; SJR: Scimago)"
        )
    if f["tempo_1a_decisao_dias"]:
        linhas.append(
            f"- Tempo até 1ª decisão: {f['tempo_1a_decisao_dias']} dias "
            f"(fonte: {f['tempo_fonte']})"
        )
    else:
        linhas.append("- Tempo até 1ª decisão: não disponível")
    if f["taxa_aceite_pct"] is not None:
        linhas.append(
            f"- Taxa de aceite publicada: {f['taxa_aceite_pct']}% "
            f"(fonte: {f['taxa_fonte']})"
        )
    if f["tipos_artigo"]:
        linhas.append(f"- Tipos de artigo aceitos: {', '.join(f['tipos_artigo'])}")
    if f["flags"]:
        linhas.append(f"- ⚠ {'; '.join(f['flags'])}")
    if f["homepage_url"]:
        linhas.append(f"- Página: {f['homepage_url']}")
    linhas.append("")
    return linhas
