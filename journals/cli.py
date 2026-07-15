"""CLI do subsistema de periódicos — python -m tools journals <subcomando>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DATA_DIR, DB_PATH, NOTA_REVISTAS, RAW_DIR, garantir_dirs
from . import db as jdb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools journals",
        description="Radar Qualis — recomenda periódicos para submissão (Área 22)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init-db", help="Criar o banco data/journals/periodicos.db")

    p_dq = sub.add_parser(
        "download-qualis",
        help="Baixar o XLSX oficial do Qualis 2021-2024 do Sucupira",
    )
    p_dq.add_argument(
        "--destino", default=str(RAW_DIR / "qualis_2021_2024.xlsx")
    )

    p_iq = sub.add_parser(
        "ingest-qualis", help="Ingerir planilha Qualis (XLSX/CSV) em qualis_raw"
    )
    p_iq.add_argument("--arquivo", required=True)

    p_is = sub.add_parser(
        "ingest-scimago", help="Ingerir CSV do Scimago (SJR) em scimago_raw"
    )
    p_is.add_argument("--csv", required=True)
    p_is.add_argument("--ano", type=int, help="Ano, se o CSV não tiver coluna year")

    p_ic = sub.add_parser(
        "ingest-scimago-categorias",
        help="Ingerir categorias Scopus por quartil (base da Porta A, Área 22)",
    )
    p_ic.add_argument("--csv", required=True,
                      help="CSV: issn,categoria,quartil,ano")

    p_isp = sub.add_parser(
        "ingest-scielo-sp",
        help="Ingerir a coleção SciELO Saúde Pública (Porta B, Área 22)",
    )
    p_isp.add_argument(
        "--arquivo",
        default=str(RAW_DIR / "scielo_saude_publica.json"),
        help="JSON da coleção (articlemeta)",
    )

    p_seed = sub.add_parser(
        "seed-from-vault", help="Semear base com a lista curada do vault"
    )
    p_seed.add_argument("--arquivo", default=str(NOTA_REVISTAS))
    p_seed.add_argument("--origem", default="nota-ia-saude")
    p_seed.add_argument(
        "--sem-resolver", action="store_true",
        help="Só parsear, sem resolver ISSN online",
    )

    p_enrich = sub.add_parser(
        "enrich", help="Enriquecer periódicos via APIs abertas (cache-first)"
    )
    p_enrich.add_argument("--issn", action="append", default=[],
                          help="ISSN específico (repetível)")
    p_enrich.add_argument("--all", action="store_true",
                          help="Todos os periódicos da base")
    p_enrich.add_argument("--stale-only", action="store_true",
                          help="Só campos com TTL vencido (default: idem)")
    p_enrich.add_argument("--refresh", action="store_true",
                          help="Forçar re-consulta mesmo com cache fresco")

    p_rec = sub.add_parser(
        "recommend", help="Gerar finalistas para um tema ou manuscrito"
    )
    p_rec.add_argument("--tema", help="Tema/abstract em texto livre")
    p_rec.add_argument("--perfil", help="JSON de perfil estruturado (do LLM)")
    p_rec.add_argument("--manuscrito", help="Arquivo .md do manuscrito")
    p_rec.add_argument("--top", type=int, default=12)
    p_rec.add_argument("--output", "-o", help="JSON de saída (finalistas)")
    p_rec.add_argument("--sem-descoberta", action="store_true",
                       help="Não buscar candidatos novos no OpenAlex (só base local)")

    p_rep = sub.add_parser(
        "report", help="Gerar relatório Markdown a partir de finalistas JSON"
    )
    p_rep.add_argument("--finalistas", required=True)
    p_rep.add_argument("--julgamento", help="JSON com julgamento do modelo forte")
    p_rep.add_argument("--output", "-o", required=True)

    p_snap = sub.add_parser(
        "snapshot", help="Capturar páginas de editora (texto) para extração LLM"
    )
    p_snap.add_argument("--issn", action="append", default=[])
    p_snap.add_argument("--finalistas", help="JSON de finalistas (captura em lote)")

    p_imp = sub.add_parser(
        "import-extraction",
        help="Validar e gravar extrações LLM (anti-alucinação por substring)",
    )
    p_imp.add_argument("--arquivo", required=True,
                       help="JSON com extrações dos subagentes")

    sub.add_parser("status", help="Estado da base (contagens, registros vencidos)")

    p_exp = sub.add_parser(
        "export-nota", help="Gerar atualização da Nota de Revistas (preview)"
    )
    p_exp.add_argument("--output", "-o",
                       help="Arquivo de saída (default: stdout/preview)")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    try:
        return _dispatch(args)
    except Exception as e:
        print(f"\nERRO: {e}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.command == "init-db":
        garantir_dirs()
        conn = jdb.connect()
        jdb.init_db(conn)
        print(f"Banco inicializado: {DB_PATH}")
        return 0

    if args.command == "download-qualis":
        from .qualis import baixar_qualis

        destino = Path(args.destino)
        print("Baixando XLSX oficial do Sucupira (pode levar minutos)...")
        baixar_qualis(destino)
        print(f"Salvo em: {destino} ({destino.stat().st_size:,} bytes)")
        print("Próximo passo: ingest-qualis --arquivo", destino)
        return 0

    if args.command == "ingest-qualis":
        from .qualis import ingerir_qualis

        conn = jdb.connect()
        jdb.init_db(conn)
        stats = ingerir_qualis(conn, Path(args.arquivo))
        print(
            f"Qualis ingerido: {stats['issns']:,} ISSNs "
            f"({stats['invalidos']} linhas inválidas, "
            f"{stats['conflitos_estrato']} conflitos de estrato)"
        )
        return 0

    if args.command == "ingest-scimago":
        from .scimago import ingerir_scimago

        conn = jdb.connect()
        jdb.init_db(conn)
        stats = ingerir_scimago(conn, Path(args.csv), ano=args.ano)
        print(
            f"Scimago ingerido: {stats['gravados']:,} registros "
            f"({stats['issns_invalidos']} ISSNs inválidos)"
        )
        return 0

    if args.command == "ingest-scimago-categorias":
        from .scimago import ingerir_categorias

        conn = jdb.connect()
        jdb.init_db(conn)
        stats = ingerir_categorias(conn, Path(args.csv))
        print(
            f"Categorias Scimago: {stats['linhas']:,} linhas, "
            f"{stats['issns']:,} ISSNs ({stats['invalidos']} inválidas)"
        )
        return 0

    if args.command == "ingest-scielo-sp":
        from .scielo import ingerir_scielo_sp

        conn = jdb.connect()
        jdb.init_db(conn)
        stats = ingerir_scielo_sp(conn, Path(args.arquivo))
        print(
            f"SciELO Saúde Pública: {stats['periodicos']} periódicos "
            f"({stats['issns']} ISSNs)"
        )
        return 0

    if args.command == "seed-from-vault":
        from .seed_vault import semear

        conn = jdb.connect()
        jdb.init_db(conn)
        stats = semear(
            conn, Path(args.arquivo), args.origem,
            resolver=not args.sem_resolver,
        )
        print(
            f"\nSemeadura: {stats['resolvidos']}/{stats['entradas']} resolvidos."
        )
        if stats["pendentes"]:
            print("Pendentes (resolver manualmente):")
            for t in stats["pendentes"]:
                print(f"  - {t}")
        return 0

    if args.command == "enrich":
        from .enrich import enriquecer

        conn = jdb.connect()
        jdb.init_db(conn)
        return enriquecer(
            conn, issns=args.issn, todos=args.all, refresh=args.refresh
        )

    if args.command == "recommend":
        from .candidates import recomendar

        conn = jdb.connect()
        jdb.init_db(conn)
        return recomendar(
            conn,
            tema=args.tema,
            perfil_path=args.perfil,
            manuscrito=args.manuscrito,
            top=args.top,
            output=args.output,
            descoberta=not args.sem_descoberta,
        )

    if args.command == "report":
        from .report import gerar_relatorio

        return gerar_relatorio(
            finalistas_path=Path(args.finalistas),
            julgamento_path=Path(args.julgamento) if args.julgamento else None,
            output=Path(args.output),
        )

    if args.command == "snapshot":
        from .snapshot import capturar

        conn = jdb.connect()
        jdb.init_db(conn)
        return capturar(conn, issns=args.issn, finalistas_path=args.finalistas)

    if args.command == "import-extraction":
        from .import_extraction import importar

        conn = jdb.connect()
        jdb.init_db(conn)
        return importar(conn, Path(args.arquivo))

    if args.command == "status":
        return _cmd_status()

    if args.command == "export-nota":
        from .export_nota import exportar_nota

        conn = jdb.connect()
        return exportar_nota(conn, output=Path(args.output) if args.output else None)

    return 1


def _cmd_status() -> int:
    from .config import (TTL_DOAJ, TTL_MEDLINE, TTL_METRICAS)

    if not DB_PATH.exists():
        print(f"Banco inexistente ({DB_PATH}). Rode: init-db")
        return 1
    conn = jdb.connect()
    jdb.init_db(conn)

    def contar(sql):
        return conn.execute(sql).fetchone()[0]

    print(f"Base: {DB_PATH}")
    print(f"  periodicos:            {contar('SELECT COUNT(*) FROM periodicos'):,}")
    print(f"    consolidados Área 22: "
          f"{contar('SELECT COUNT(*) FROM periodicos WHERE consolidado = 1')}")
    print(f"  qualis_raw (legado):   {contar('SELECT COUNT(*) FROM qualis_raw'):,}")
    print(f"  scimago_categorias:    {contar('SELECT COUNT(*) FROM scimago_categorias'):,} "
          f"linhas ({contar('SELECT COUNT(DISTINCT issn) FROM scimago_categorias'):,} ISSNs)")
    print(f"  scielo_sp (Porta B):   {contar('SELECT COUNT(*) FROM scielo_sp')} ISSNs")
    print(f"  scimago_raw (SJR):     {contar('SELECT COUNT(*) FROM scimago_raw'):,}")
    print(f"  curados:               {contar('SELECT COUNT(*) FROM curados')} "
          f"({contar('SELECT COUNT(*) FROM curados WHERE issn_l IS NULL')} pendentes)")
    print(f"  snapshots:             {contar('SELECT COUNT(*) FROM snapshots')}")

    vencidos = {
        f"DOAJ/APC (>{TTL_DOAJ}d)": (
            "SELECT COUNT(*) FROM periodicos WHERE doaj_verificado_em IS NULL "
            f"OR date(doaj_verificado_em) < date('now', '-{TTL_DOAJ} days')"
        ),
        f"métricas (>{TTL_METRICAS}d)": (
            "SELECT COUNT(*) FROM periodicos WHERE metricas_verificado_em IS NULL "
            f"OR date(metricas_verificado_em) < date('now', '-{TTL_METRICAS} days')"
        ),
        f"MEDLINE (>{TTL_MEDLINE}d)": (
            "SELECT COUNT(*) FROM periodicos WHERE medline_verificado_em IS NULL "
            f"OR date(medline_verificado_em) < date('now', '-{TTL_MEDLINE} days')"
        ),
    }
    print("  Registros com verificação vencida:")
    for nome, sql in vencidos.items():
        print(f"    {nome}: {contar(sql)}")
    return 0
