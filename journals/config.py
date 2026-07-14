"""Paths e TTLs do subsistema de periódicos."""

from __future__ import annotations

from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent  # tools/
REPO_DIR = TOOLS_DIR.parent                          # ~/bin/escrita-tooling

DATA_DIR = REPO_DIR / "data" / "journals"
DB_PATH = DATA_DIR / "periodicos.db"
RAW_DIR = DATA_DIR / "raw"            # planilhas baixadas (Qualis, SJR)
SNAPSHOTS_DIR = DATA_DIR / "snapshots"  # texto de páginas de editora
EXPORT_CSV = DATA_DIR / "periodicos_export.csv"

# Nota curada no vault (dados-semente e destino do export)
NOTA_REVISTAS = (
    Path.home() / "PKM" / "Notas" / "Revistas para publicação — IA em saúde.md"
)

# TTL em dias por família de campo. Qualis não tem TTL: o 2021-2024 é o
# último Qualis de periódicos (CAPES descontinuou a classificação).
TTL_METRICAS = 365   # OpenAlex summary_stats
TTL_DOAJ = 180       # APC, licença, peer review
TTL_MEDLINE = 365    # NLM Catalog
TTL_SNAPSHOT = 180   # páginas de editora (tempo de decisão, taxa, tipos)


def garantir_dirs() -> None:
    """Cria a árvore data/journals/ (gitignored) se não existir."""
    for d in (DATA_DIR, RAW_DIR, SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
