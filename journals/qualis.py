"""Ingestão do Qualis CAPES 2021-2024 (o último Qualis de periódicos).

A CAPES descontinuou o Qualis Periódicos a partir do ciclo 2025-2028
(passará a classificar artigos por consolidação do veículo), então esta
tabela é ESTÁTICA: ingere-se uma vez e nunca se re-scrapeia.

Fontes aceitas:
- XLSX oficial do Sucupira legado (aba RelatorioQualis, colunas
  ISSN | Título | Área de Avaliação | Estrato) — `baixar_qualis()` automatiza.
- CSV com as mesmas colunas (header tolerante a variações).

Fato verificado na ingestão de 2026-07-11: 33.347 ISSNs únicos, estrato
idêntico em todas as áreas (classificação única por periódico); o arquivo
repete o periódico por área de avaliação com produção — as áreas são
preservadas em qualis_raw.areas como sinal de aderência.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import warnings
from collections import defaultdict
from pathlib import Path

from .db import hoje_iso, normalizar_issn

SUCUPIRA_URL = (
    "https://sucupira-legado.capes.gov.br/sucupira/public/consultas/coleta/"
    "veiculoPublicacaoQualis/listaConsultaGeralPeriodicos.jsf"
)
EVENTO_2021_2024 = "237"  # value do <option> "QUADRIÊNIO 2021-2024"

ORDEM_ESTRATOS = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4", "B5", "C"]


def baixar_qualis(destino: Path, timeout: int = 300) -> Path:
    """Baixa o XLSX oficial de classificações do Sucupira (dança JSF).

    1. GET para obter ViewState; 2. POST consulta vazia do evento
    2021-2024; 3. POST no commandLink do "Arquivo de classificações".
    """
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    s = requests.Session()
    s.headers.update(headers)

    init = s.get(SUCUPIRA_URL, timeout=timeout)
    init.raise_for_status()
    vs = re.search(r'id="javax\.faces\.ViewState" value="([^"]+)"', init.text)
    if not vs:
        raise RuntimeError("ViewState não encontrado na página do Sucupira")

    consulta = {
        "form": "form",
        "form:evento": EVENTO_2021_2024,
        "form:area": "0",
        "form:estrato": "0",
        "form:consultar": "Consultar",
        "javax.faces.ViewState": vs.group(1),
    }
    resp = s.post(SUCUPIRA_URL, data=consulta, timeout=timeout)
    resp.raise_for_status()

    link = re.search(r"\{'(form:j_idt\d+)':'form:j_idt\d+'\}", resp.text)
    vs2 = re.findall(
        r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', resp.text
    )
    if not link or not vs2:
        raise RuntimeError(
            "Link do arquivo de classificações não encontrado — "
            "a página do Sucupira pode ter mudado; baixe manualmente em "
            f"{SUCUPIRA_URL} e use ingest-qualis --arquivo"
        )

    download = {
        "form": "form",
        "form:evento": EVENTO_2021_2024,
        "form:area": "0",
        "form:estrato": "0",
        link.group(1): link.group(1),
        "javax.faces.ViewState": vs2[-1],
    }
    dl = s.post(SUCUPIRA_URL, data=download, timeout=timeout * 2, stream=True)
    dl.raise_for_status()
    if "spreadsheet" not in dl.headers.get("Content-Type", ""):
        raise RuntimeError(
            f"Resposta não é XLSX ({dl.headers.get('Content-Type')})"
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as f:
        for chunk in dl.iter_content(1 << 16):
            f.write(chunk)
    return destino


def _linhas_xlsx(arquivo: Path):
    """Itera (issn, titulo, area, estrato) do XLSX oficial."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import openpyxl

        wb = openpyxl.load_workbook(str(arquivo), read_only=True)
    ws = wb.active
    # O XLSX do Sucupira declara dimensões erradas (1 linha) — resetar.
    ws.reset_dimensions()
    rows = ws.iter_rows(values_only=True)
    header = [str(c or "").strip().lower() for c in next(rows)]
    idx = _mapear_colunas(header, arquivo)
    for row in rows:
        yield tuple(
            str(row[i]).strip() if i is not None and i < len(row) and row[i] else ""
            for i in idx
        )


def _linhas_csv(arquivo: Path):
    """Itera (issn, titulo, area, estrato) de um CSV com header."""
    with open(arquivo, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        sep = ";" if amostra.count(";") > amostra.count(",") else ","
        reader = csv.reader(f, delimiter=sep)
        header = [c.strip().lower() for c in next(reader)]
        idx = _mapear_colunas(header, arquivo)
        for row in reader:
            yield tuple(
                row[i].strip() if i is not None and i < len(row) else ""
                for i in idx
            )


def _mapear_colunas(header: list[str], arquivo: Path):
    """Localiza colunas issn/título/área/estrato de forma tolerante."""

    def achar(*termos, obrigatoria=True):
        for i, col in enumerate(header):
            if any(t in col for t in termos):
                return i
        if obrigatoria:
            raise ValueError(
                f"Coluna {termos} não encontrada em {arquivo} (header: {header})"
            )
        return None

    return (
        achar("issn"),
        achar("títul", "titul"),
        achar("área", "area", obrigatoria=False),
        achar("estrato", "classifica"),
    )


def ingerir_qualis(
    conn: sqlite3.Connection,
    arquivo: Path,
    fonte: str = "CAPES Qualis 2021-2024 (Sucupira)",
) -> dict:
    """Carrega a planilha para qualis_raw. Retorna estatísticas.

    Consolida 1 linha por ISSN; em conflito de estrato entre áreas
    (não esperado no Qualis único) mantém o melhor e conta o conflito.
    """
    linhas = _linhas_xlsx(arquivo) if arquivo.suffix.lower() in (".xlsx", ".xls") \
        else _linhas_csv(arquivo)

    consolidado: dict[str, dict] = {}
    areas_por_issn: dict[str, set] = defaultdict(set)
    invalidos = 0
    conflitos = 0

    for issn, titulo, area, estrato in linhas:
        norm = normalizar_issn(issn)
        estrato = estrato.upper().strip()
        if not norm or estrato not in ORDEM_ESTRATOS:
            invalidos += 1
            continue
        if area:
            areas_por_issn[norm].add(area.strip())
        atual = consolidado.get(norm)
        if atual is None:
            consolidado[norm] = {"titulo": titulo, "estrato": estrato}
        elif atual["estrato"] != estrato:
            conflitos += 1
            if ORDEM_ESTRATOS.index(estrato) < ORDEM_ESTRATOS.index(atual["estrato"]):
                atual["estrato"] = estrato

    agora = hoje_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO qualis_raw "
        "(issn, titulo, estrato, areas, fonte, ingerido_em) VALUES (?,?,?,?,?,?)",
        (
            (
                issn,
                d["titulo"],
                d["estrato"],
                json.dumps(sorted(areas_por_issn[issn]), ensure_ascii=False),
                fonte,
                agora,
            )
            for issn, d in consolidado.items()
        ),
    )
    conn.commit()
    return {
        "issns": len(consolidado),
        "invalidos": invalidos,
        "conflitos_estrato": conflitos,
    }
