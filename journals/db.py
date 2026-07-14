"""Banco SQLite de periódicos — schema, conexão e helpers de proveniência.

Convenção central: todo campo volátil tem o trio
    <campo>, <campo>_fonte, <campo>_verificado_em
NULL significa "não disponível" — o sistema nunca inventa APC, ISSN ou Qualis.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DB_PATH, garantir_dirs

DDL = """
CREATE TABLE IF NOT EXISTS periodicos (
  issn_l TEXT PRIMARY KEY,          -- ISSN-L canônico (OpenAlex)
  issns TEXT NOT NULL DEFAULT '[]', -- JSON: todos os ISSNs (print + eletrônico)
  titulo TEXT NOT NULL,
  editora TEXT,
  pais TEXT,
  idiomas TEXT,                     -- JSON
  homepage_url TEXT,
  openalex_id TEXT,

  escopo_resumo TEXT, escopo_fonte TEXT, escopo_verificado_em TEXT,
  topicos TEXT,                     -- JSON (OpenAlex topics: display_name)

  -- Qualis 2021-2024 = LEGADO/retrospectivo: só classifica artigos
  -- publicados até 2024. A CAPES descontinuou o Qualis Periódicos; para
  -- o ciclo 2025-2028 vale a "consolidação" abaixo (Área 22).
  qualis_estrato TEXT, qualis_fonte TEXT, qualis_verificado_em TEXT,
  qualis_areas TEXT,                -- JSON: áreas CAPES com produção no periódico

  -- Consolidação Área 22 (ciclo 2025-2028) — critério VIGENTE para artigos
  -- publicados de 2025 em diante. Ver consolidacao.py.
  porta_a_status TEXT,              -- consolidado | nao_por_scimago | sem_categoria_saude | sem_dado
  porta_a_categoria TEXT,           -- categoria de saúde de melhor quartil (evidência)
  porta_a_quartil TEXT,             -- Q1..Q4 dessa categoria
  porta_a_categorias TEXT,          -- JSON: [{categoria, quartil}] das categorias de saúde
  porta_b_scielo_sp INTEGER,        -- 1 = na coleção SciELO Saúde Pública
  consolidado INTEGER,              -- 1 = cumpre Porta A (Scimago) OU Porta B
  consolidacao_fonte TEXT,
  consolidacao_verificado_em TEXT,

  is_oa INTEGER, in_doaj INTEGER, licenca TEXT, peer_review TEXT,
  apc_valor REAL, apc_moeda TEXT, apc_usd REAL, apc_waiver INTEGER,
  apc_fonte TEXT, apc_verificado_em TEXT,
  doaj_verificado_em TEXT,

  medline_indexado INTEGER, medline_verificado_em TEXT,
  indexacoes TEXT,                  -- JSON (best-effort: scielo, lilacs...)

  citedness_2yr REAL, h_index INTEGER, works_count INTEGER,
  metricas_verificado_em TEXT,
  sjr REAL, sjr_quartil TEXT, sjr_ano INTEGER,

  tempo_1a_decisao_dias INTEGER, tempo_fonte TEXT, tempo_verificado_em TEXT,
  taxa_aceite_pct REAL, taxa_fonte TEXT, taxa_verificado_em TEXT,
  tipos_artigo TEXT,                -- JSON: ["original","review","protocolo",...]
  tipos_fonte TEXT, tipos_verificado_em TEXT,

  atualizado_em TEXT NOT NULL
);

-- Staging da planilha oficial CAPES (1 linha por ISSN; estrato é único
-- por periódico no Qualis 2021-2024 — verificado: 0 divergências em 33k).
CREATE TABLE IF NOT EXISTS qualis_raw (
  issn TEXT PRIMARY KEY,
  titulo TEXT,
  estrato TEXT NOT NULL,
  areas TEXT,                       -- JSON: áreas de avaliação em que aparece
  fonte TEXT NOT NULL,
  ingerido_em TEXT NOT NULL
);

-- Staging do CSV Scimago (1 linha por ISSN × ano).
CREATE TABLE IF NOT EXISTS scimago_raw (
  issn TEXT NOT NULL,
  titulo TEXT,
  sjr REAL,
  quartil TEXT,
  h_index INTEGER,
  ano INTEGER NOT NULL,
  ingerido_em TEXT NOT NULL,
  PRIMARY KEY (issn, ano)
);

-- Categorias Scopus por quartil (Scimago) — base da Porta A da Área 22.
-- 1 linha por (issn, categoria, ano). SJR é PROXY do CiteScore/Scopus.
CREATE TABLE IF NOT EXISTS scimago_categorias (
  issn TEXT NOT NULL,
  categoria TEXT NOT NULL,
  quartil TEXT NOT NULL,            -- Q1..Q4
  ano INTEGER NOT NULL,
  ingerido_em TEXT NOT NULL,
  PRIMARY KEY (issn, categoria, ano)
);

-- Coleção SciELO Saúde Pública (Porta B da Área 22). Lista estável
-- (articlemeta, coleção 'spa'). h5 > percentil 60 é verificação manual.
CREATE TABLE IF NOT EXISTS scielo_sp (
  issn TEXT PRIMARY KEY,
  titulo TEXT,
  fonte TEXT NOT NULL,
  ingerido_em TEXT NOT NULL
);

-- Lista curada (Nota do vault + revistas_Copy) usada na geração de candidatos.
CREATE TABLE IF NOT EXISTS curados (
  origem TEXT NOT NULL,             -- ex: 'nota-ia-saude'
  titulo TEXT NOT NULL,
  issn_l TEXT,                      -- resolvido por título (NULL = pendente)
  issns TEXT,                       -- JSON
  estrato_origem TEXT,              -- estrato declarado na lista (não é fonte Qualis)
  apc_texto TEXT,                   -- APC como escrito na lista (não verificado)
  url TEXT,
  escopo_texto TEXT,
  similaridade REAL,                -- fuzzy título ↔ fonte de resolução
  resolvido_via TEXT,               -- crossref | openalex
  resolvido_em TEXT,
  PRIMARY KEY (origem, titulo)
);

-- Snapshots de páginas de editora (base auditável da extração LLM).
CREATE TABLE IF NOT EXISTS snapshots (
  issn_l TEXT NOT NULL,
  url TEXT NOT NULL,
  capturado_em TEXT NOT NULL,
  texto_path TEXT NOT NULL,
  PRIMARY KEY (issn_l, url)
);

CREATE INDEX IF NOT EXISTS idx_periodicos_estrato ON periodicos (qualis_estrato);
CREATE INDEX IF NOT EXISTS idx_scimago_cat_issn ON scimago_categorias (issn);
"""

# Colunas adicionadas depois da 1ª versão do schema — migração leve para
# bancos já existentes (CREATE TABLE IF NOT EXISTS não altera tabela viva).
_COLUNAS_NOVAS = {
    "porta_a_status": "TEXT",
    "porta_a_categoria": "TEXT",
    "porta_a_quartil": "TEXT",
    "porta_a_categorias": "TEXT",
    "porta_b_scielo_sp": "INTEGER",
    "consolidado": "INTEGER",
    "consolidacao_fonte": "TEXT",
    "consolidacao_verificado_em": "TEXT",
}

_ISSN_RE = re.compile(r"^[0-9]{4}-?[0-9]{3}[0-9Xx]$")


def normalizar_issn(valor: str | None) -> str | None:
    """Normaliza ISSN para o formato XXXX-XXXX (dígito X maiúsculo).

    Retorna None para valores vazios ou fora do padrão — nunca inventa.
    """
    if not valor:
        return None
    limpo = valor.strip().upper().replace(" ", "")
    if not _ISSN_RE.match(limpo):
        return None
    limpo = limpo.replace("-", "")
    return f"{limpo[:4]}-{limpo[4:]}"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Abre conexão com row_factory de dicionário."""
    if str(db_path) == str(DB_PATH):
        garantir_dirs()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Cria as tabelas (idempotente) e aplica migração de colunas novas."""
    conn.executescript(DDL)
    existentes = {
        r["name"] for r in conn.execute("PRAGMA table_info(periodicos)")
    }
    for col, tipo in _COLUNAS_NOVAS.items():
        if col not in existentes:
            conn.execute(f"ALTER TABLE periodicos ADD COLUMN {col} {tipo}")
    # Índice sobre coluna migrada — só depois do ALTER TABLE.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_periodicos_consolidado "
        "ON periodicos (consolidado)"
    )
    conn.commit()


def hoje_iso() -> str:
    return date.today().isoformat()


def upsert_periodico(conn: sqlite3.Connection, issn_l: str, **campos) -> None:
    """Insere/atualiza um periódico. Só grava campos não-None.

    Nunca sobrescreve valor existente com None — ausência de dado numa
    fonte não apaga dado obtido de outra.
    """
    issn_l = normalizar_issn(issn_l)
    if not issn_l:
        raise ValueError(f"ISSN-L inválido: {issn_l!r}")
    campos = {k: v for k, v in campos.items() if v is not None}
    campos["atualizado_em"] = datetime.now().isoformat(timespec="seconds")

    existe = conn.execute(
        "SELECT 1 FROM periodicos WHERE issn_l = ?", (issn_l,)
    ).fetchone()
    if existe:
        sets = ", ".join(f"{k} = ?" for k in campos)
        conn.execute(
            f"UPDATE periodicos SET {sets} WHERE issn_l = ?",
            (*campos.values(), issn_l),
        )
    else:
        campos.setdefault("titulo", "")
        campos.setdefault("issns", "[]")
        cols = ", ".join(["issn_l", *campos])
        marks = ", ".join("?" for _ in range(len(campos) + 1))
        conn.execute(
            f"INSERT INTO periodicos ({cols}) VALUES ({marks})",
            (issn_l, *campos.values()),
        )
    conn.commit()


def get_periodico(conn: sqlite3.Connection, issn: str) -> sqlite3.Row | None:
    """Busca por ISSN-L exato ou por qualquer ISSN do array issns."""
    issn = normalizar_issn(issn) or issn
    row = conn.execute(
        "SELECT * FROM periodicos WHERE issn_l = ?", (issn,)
    ).fetchone()
    if row:
        return row
    return conn.execute(
        "SELECT * FROM periodicos WHERE issns LIKE ?", (f'%"{issn}"%',)
    ).fetchone()


def campo_vencido(row: sqlite3.Row, campo_data: str, ttl_dias: int) -> bool:
    """True se o campo nunca foi verificado ou o TTL expirou."""
    valor = row[campo_data] if campo_data in row.keys() else None
    if not valor:
        return True
    verificado = date.fromisoformat(valor[:10])
    return date.today() - verificado > timedelta(days=ttl_dias)


def qualis_para_issns(
    conn: sqlite3.Connection, issns: list[str]
) -> sqlite3.Row | None:
    """Retorna a linha de qualis_raw que casa com qualquer dos ISSNs."""
    for issn in issns:
        norm = normalizar_issn(issn)
        if not norm:
            continue
        row = conn.execute(
            "SELECT * FROM qualis_raw WHERE issn = ?", (norm,)
        ).fetchone()
        if row:
            return row
    return None


def sjr_para_issns(
    conn: sqlite3.Connection, issns: list[str]
) -> sqlite3.Row | None:
    """Retorna a linha mais recente de scimago_raw para qualquer dos ISSNs."""
    for issn in issns:
        norm = normalizar_issn(issn)
        if not norm:
            continue
        row = conn.execute(
            "SELECT * FROM scimago_raw WHERE issn = ? ORDER BY ano DESC LIMIT 1",
            (norm,),
        ).fetchone()
        if row:
            return row
    return None


def categorias_para_issns(
    conn: sqlite3.Connection, issns: list[str]
) -> list[sqlite3.Row]:
    """Retorna as categorias Scopus (com quartil) do ano mais recente."""
    for issn in issns:
        norm = normalizar_issn(issn)
        if not norm:
            continue
        ano = conn.execute(
            "SELECT MAX(ano) FROM scimago_categorias WHERE issn = ?", (norm,)
        ).fetchone()[0]
        if ano is None:
            continue
        return conn.execute(
            "SELECT categoria, quartil, ano FROM scimago_categorias "
            "WHERE issn = ? AND ano = ? ORDER BY quartil, categoria",
            (norm, ano),
        ).fetchall()
    return []


def em_scielo_sp(conn: sqlite3.Connection, issns: list[str]) -> sqlite3.Row | None:
    """Retorna a linha de scielo_sp se algum ISSN estiver na coleção."""
    for issn in issns:
        norm = normalizar_issn(issn)
        if not norm:
            continue
        row = conn.execute(
            "SELECT * FROM scielo_sp WHERE issn = ?", (norm,)
        ).fetchone()
        if row:
            return row
    return None
