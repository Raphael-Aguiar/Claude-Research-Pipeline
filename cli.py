"""CLI do Pipeline de Pesquisa Acadêmica.

Subcomandos: run, scope, search, verify, verify-refs, verify-claims, status, export,
audit, seed, extract, extract-manual, extract-facts, facts-import, facts-status,
facts-report, facts-crosscheck

Subsistema de periódicos (recomendação de onde submeter, Qualis CAPES):
`python -m tools journals --help` — CLI própria em journals/cli.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import get_pipeline_dir, get_scope_path, load_scope
from .exporters.json_export import load_refs_json
from .models import CompositeGrade, Modality


def main(argv: list[str] | None = None) -> int:
    """Entry point da CLI."""
    if argv is None:
        argv = sys.argv[1:]

    # Subsistema de periódicos (Qualis) — CLI própria em journals/cli.py
    if argv and argv[0] == "journals":
        from .journals.cli import main as journals_main

        return journals_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="python -m tools",
        description="Pipeline de Pesquisa Acadêmica — busca, verifica e audita referências",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcomandos")

    # --- run ---
    p_run = subparsers.add_parser("run", help="Executar pipeline completo")
    p_run.add_argument("project", help="Nome do projeto (pasta em ~/PKM/Escrita/)")
    p_run.add_argument(
        "--modality", "-m",
        choices=[m.value for m in Modality],
        help="Override da modalidade (default: usar scope.yaml)",
    )
    p_run.add_argument(
        "--from-stage", type=int, default=1,
        help="Retomar a partir da etapa N (default: 1)",
    )
    p_run.add_argument(
        "--to-stage", type=int, default=10,
        help="Parar após a etapa N (default: 10)",
    )

    # --- scope ---
    p_scope = subparsers.add_parser("scope", help="Criar/mostrar scope.yaml")
    p_scope.add_argument("project", help="Nome do projeto")
    p_scope.add_argument(
        "--init", action="store_true",
        help="Criar scope.yaml a partir do template",
    )
    p_scope.add_argument(
        "--modality", "-m", default="pesquisa-base",
        help="Modalidade para init (default: pesquisa-base)",
    )

    # --- search ---
    p_search = subparsers.add_parser("search", help="Executar apenas a etapa de busca")
    p_search.add_argument("project", help="Nome do projeto")

    # --- verify ---
    p_verify = subparsers.add_parser("verify", help="Verificar referências existentes")
    p_verify.add_argument("project", help="Nome do projeto")

    # --- status ---
    p_status = subparsers.add_parser("status", help="Mostrar estado do pipeline")
    p_status.add_argument("project", help="Nome do projeto")

    # --- export ---
    p_export = subparsers.add_parser("export", help="Exportar referências")
    p_export.add_argument("project", help="Nome do projeto")
    p_export.add_argument(
        "--format", "-f", choices=["bib", "json"], default="bib",
        help="Formato de exportação (default: bib)",
    )

    # --- audit ---
    p_audit = subparsers.add_parser("audit", help="Gerar relatório de auditoria")
    p_audit.add_argument("project", help="Nome do projeto")

    # --- seed ---
    p_seed = subparsers.add_parser("seed", help="Extrair seed refs de textos existentes")
    p_seed.add_argument("project", help="Nome do projeto")
    p_seed.add_argument(
        "--source", "-s",
        help="Arquivo fonte específico (default: todos os .md do projeto)",
    )

    # --- extract ---
    p_extract = subparsers.add_parser("extract", help="Extrair conteúdo das refs para MDs")
    p_extract.add_argument("project", help="Nome do projeto")

    # --- extract-manual ---
    p_extract_manual = subparsers.add_parser(
        "extract-manual",
        help="Processar PDFs manuais de pipeline/pdfs-manual/",
    )
    p_extract_manual.add_argument("project", help="Nome do projeto")

    # --- verify-refs ---
    p_verify_refs = subparsers.add_parser(
        "verify-refs",
        help="Verificar referências Vancouver de um arquivo .md",
    )
    p_verify_refs.add_argument(
        "file",
        help="Arquivo .md com referências Vancouver",
    )
    p_verify_refs.add_argument(
        "--output", "-o",
        help="Arquivo de saída para relatório (default: <file>.verification.md)",
    )

    # --- verify-claims ---
    p_verify_claims = subparsers.add_parser(
        "verify-claims",
        help="Verificar se afirmações do texto são suportadas pelas referências citadas",
    )
    p_verify_claims.add_argument(
        "file",
        help="Arquivo .md com texto e referências Vancouver",
    )
    p_verify_claims.add_argument(
        "--project", "-p",
        help="Nome do projeto (para buscar full text em pipeline/refs/)",
    )
    p_verify_claims.add_argument(
        "--output", "-o",
        help="Caminho base para outputs (default: mesmo diretório do arquivo)",
    )

    # --- extract-facts ---
    p_extract_facts = subparsers.add_parser(
        "extract-facts",
        help="Extrair candidatos a fatos das referências (Camada 1 preventiva)",
    )
    p_extract_facts.add_argument("project", help="Nome do projeto")
    p_extract_facts.add_argument(
        "--refs", "-r",
        help="Range de refs: '1,5,11-17' (default: todas)",
    )
    p_extract_facts.add_argument(
        "--section", "-s",
        help="Seção para filtrar (ex: 16.3)",
    )

    # --- facts-import ---
    p_facts_import = subparsers.add_parser(
        "facts-import",
        help="Validar e importar fatos de .facts-pending.jsonl para o registry",
    )
    p_facts_import.add_argument("project", help="Nome do projeto")

    # --- facts-status ---
    p_facts_status = subparsers.add_parser(
        "facts-status",
        help="Exibir status do Facts Registry (section brief para escrita)",
    )
    p_facts_status.add_argument("project", help="Nome do projeto")
    p_facts_status.add_argument(
        "--section", "-s",
        help="Gerar brief para seção específica (ex: 16.3)",
    )

    # --- facts-report ---
    p_facts_report = subparsers.add_parser(
        "facts-report",
        help="Gerar relatório markdown completo do Facts Registry",
    )
    p_facts_report.add_argument("project", help="Nome do projeto")
    p_facts_report.add_argument(
        "--output", "-o",
        help="Arquivo de saída (default: stdout)",
    )

    # --- facts-crosscheck ---
    p_facts_crosscheck = subparsers.add_parser(
        "facts-crosscheck",
        help="Cruzar .claims-data.json com o Facts Registry",
    )
    p_facts_crosscheck.add_argument("project", help="Nome do projeto")

    p_descriptors = subparsers.add_parser(
        "descriptors",
        help="Sugerir descritores MeSH/DeCS para os termos do scope.yaml",
    )
    p_descriptors.add_argument("project", help="Nome do projeto")

    p_screen_export = subparsers.add_parser(
        "screen-export",
        help="Exportar lote para triagem semântica LLM (dupla triagem)",
    )
    p_screen_export.add_argument("project", help="Nome do projeto")

    p_screen_import = subparsers.add_parser(
        "screen-import",
        help="Importar veredictos da triagem LLM + calcular kappa",
    )
    p_screen_import.add_argument("project", help="Nome do projeto")

    p_prisma = subparsers.add_parser(
        "prisma",
        help="Gerar fluxograma PRISMA canônico (PRISMA 2020/ScR/PRIOR)",
    )
    p_prisma.add_argument("project", help="Nome do projeto")

    p_semantic = subparsers.add_parser(
        "semantic",
        help="Re-ranqueamento + resgate semântico por embeddings (Ollama)",
    )
    p_semantic.add_argument("project", help="Nome do projeto")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "run":
            return cmd_run(args)
        elif args.command == "scope":
            return cmd_scope(args)
        elif args.command == "search":
            return cmd_search(args)
        elif args.command == "verify":
            return cmd_verify(args)
        elif args.command == "status":
            return cmd_status(args)
        elif args.command == "export":
            return cmd_export(args)
        elif args.command == "audit":
            return cmd_audit(args)
        elif args.command == "seed":
            return cmd_seed(args)
        elif args.command == "extract":
            return cmd_extract(args)
        elif args.command == "extract-manual":
            return cmd_extract_manual(args)
        elif args.command == "verify-refs":
            return cmd_verify_refs(args)
        elif args.command == "verify-claims":
            return cmd_verify_claims(args)
        elif args.command == "extract-facts":
            return cmd_extract_facts(args)
        elif args.command == "facts-import":
            return cmd_facts_import(args)
        elif args.command == "facts-status":
            return cmd_facts_status(args)
        elif args.command == "facts-report":
            return cmd_facts_report(args)
        elif args.command == "facts-crosscheck":
            return cmd_facts_crosscheck(args)
        elif args.command == "descriptors":
            return cmd_descriptors(args)
        elif args.command == "screen-export":
            return cmd_screen_export(args)
        elif args.command == "screen-import":
            return cmd_screen_import(args)
        elif args.command == "prisma":
            return cmd_prisma(args)
        elif args.command == "semantic":
            return cmd_semantic(args)
    except Exception as e:
        print(f"\nERRO: {e}", file=sys.stderr)
        return 1

    return 0


def cmd_run(args) -> int:
    """Executa pipeline completo (ou parcial com --from-stage/--to-stage)."""
    from .stages.s01_scope import parse_scope
    from .stages.s02_search import run_search
    from .stages.s03_normalize import normalize_and_dedup
    from .stages.s04_verify import verify_references
    from .stages.s05_classify import classify_references
    from .stages.s06_screen import screen_references
    from .stages.s07_integrity import check_integrity
    from .stages.s08_access import check_access
    from .stages.s09_synthesize import synthesize
    from .stages.s10_validate import validate_final

    project = args.project

    print("=" * 60)
    print(f"Pipeline de Pesquisa Acadêmica v2.0 — {project}")
    print("=" * 60)

    # Etapa 1: Scope
    if args.from_stage <= 1:
        print("\n[1/10] Carregando escopo...")
        config = parse_scope(project)
        if args.modality:
            config.modality = Modality(args.modality)
        print(f"  Modalidade: {config.modality.value}")
        print(f"  APIs: {', '.join(config.apis)}")
        print(f"  Max resultados/API: {config.max_results_per_api}")
        print(f"  Max refs finais: {config.max_final_refs}")
        if config.research_axes:
            print(f"  Eixos de pesquisa: {len(config.research_axes)}")
        if config.exclusion_keywords:
            print(f"  Exclusion keywords: {len(config.exclusion_keywords)}")
    else:
        config = load_scope(project)
        if args.modality:
            config.modality = Modality(args.modality)

    refs = []

    # Etapa 2: Busca
    if args.from_stage <= 2 <= args.to_stage:
        print("\n[2/10] Buscando referências...")
        refs = run_search(config)
    elif args.from_stage > 2:
        # Carregar refs do último checkpoint. Para re-triagem (from-stage <= 6)
        # preferir refs-verified (conjunto completo) a refs-final (já filtrado).
        refs = _load_latest_refs(project, prefer_full=args.from_stage <= 6)

    if not refs:
        print("\nNenhuma referência encontrada. Verifique o scope.yaml.")
        return 1

    # Etapa 3: Normalização
    if args.from_stage <= 3 <= args.to_stage:
        print("\n[3/10] Normalizando e deduplicando...")
        refs = normalize_and_dedup(refs, project)

    # Etapa 4: Verificação
    if args.from_stage <= 4 <= args.to_stage:
        print("\n[4/10] Verificando DOIs no CrossRef...")
        refs = verify_references(refs, project)

    # Etapa 5: Classificação
    if args.from_stage <= 5 <= args.to_stage:
        print("\n[5/10] Classificando por tier...")
        refs = classify_references(refs)

    # Etapa 6: Triagem
    if args.from_stage <= 6 <= args.to_stage:
        print("\n[6/10] Triagem de relevância (co-ocorrência multi-bloco)...")
        refs = screen_references(refs, config)

    # Etapa 7: Integridade
    if args.from_stage <= 7 <= args.to_stage:
        print("\n[7/10] Verificando integridade...")
        refs = check_integrity(refs, project)

    # Etapa 8: Acesso
    if args.from_stage <= 8 <= args.to_stage:
        print("\n[8/10] Verificando acesso...")
        refs = check_access(refs, config)

    # Etapa 9: Síntese + Outputs
    if args.from_stage <= 9 <= args.to_stage:
        print("\n[9/10] Sintetizando outputs...")
        refs = synthesize(refs, config)

        # Etapa 9b: Extração de conteúdo para MDs
        print("\n[9b] Extraindo conteúdo para MDs individuais...")
        from .stages.s08b_extract import extract_references
        extract_references(refs, config)

    # Etapa 10: Validação
    if args.from_stage <= 10 <= args.to_stage:
        print("\n[10/10] Validando resultado final...")
        validation = validate_final(refs, config)

    print("\n" + "=" * 60)
    print("Pipeline concluído!")
    pipeline_dir = get_pipeline_dir(project)
    print(f"Outputs em: {pipeline_dir}")
    print(f"\nPróximos passos:")
    print(f"  1. Revisar validation-list.md (~5-10 min)")
    print(f"  2. Revisar to-obtain.md (artigos não-OA)")
    print(f"  3. Executar /enrich-refs para resumos Claude")
    print("=" * 60)

    return 0


def cmd_scope(args) -> int:
    """Cria ou exibe scope.yaml."""
    from .stages.s01_scope import init_scope

    if args.init:
        init_scope(args.project, modality=args.modality)
    else:
        scope_path = get_scope_path(args.project)
        if scope_path.exists():
            print(scope_path.read_text(encoding="utf-8"))
        else:
            print(f"scope.yaml não encontrado. Use --init para criar.")
            return 1
    return 0


def cmd_search(args) -> int:
    """Executa apenas a etapa de busca."""
    from .stages.s01_scope import parse_scope
    from .stages.s02_search import run_search

    config = parse_scope(args.project)
    run_search(config)
    return 0


def cmd_verify(args) -> int:
    """Verifica referências existentes."""
    from .stages.s04_verify import verify_references

    refs = _load_latest_refs(args.project)
    if not refs:
        print("Nenhuma referência encontrada para verificar.")
        return 1
    verify_references(refs, args.project)
    return 0


def cmd_status(args) -> int:
    """Exibe estado do pipeline para o projeto."""
    pipeline_dir = get_pipeline_dir(args.project, create=False)

    files = {
        "scope.yaml": get_scope_path(args.project),
        "refs-raw.json": pipeline_dir / "refs-raw.json",
        "refs-dedup.json": pipeline_dir / "refs-dedup.json",
        "refs-verified.json": pipeline_dir / "refs-verified.json",
        "refs-final.json": pipeline_dir / "refs-final.json",
        "refs-seed.json": pipeline_dir / "refs-seed.json",
        "refs.bib": pipeline_dir / "refs.bib",
        "audit-report.md": pipeline_dir / "audit-report.md",
        "search-log.md": pipeline_dir / "search-log.md",
        "validation-list.md": pipeline_dir / "validation-list.md",
        "research-brief.md": pipeline_dir / "research-brief.md",
        "coverage-report.md": pipeline_dir / "coverage-report.md",
        "to-obtain.md": pipeline_dir / "to-obtain.md",
    }

    refs_dir = pipeline_dir / "refs"
    pdfs_dir = pipeline_dir / "pdfs-manual"

    print(f"\nEstado do Pipeline — {args.project}")
    print("-" * 40)

    for name, path in files.items():
        if path.exists():
            size = path.stat().st_size
            if size > 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"
            print(f"  ✓ {name} ({size_str})")

            # Detalhes de refs JSON
            if name.endswith(".json") and "refs" in name:
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    meta = data.get("metadata", {})
                    total = meta.get("total_refs", len(data.get("references", [])))
                    print(f"    → {total} referências")
                except Exception:
                    pass
        else:
            print(f"  ✗ {name}")

    # Diretório de MDs extraídos
    if refs_dir.exists():
        md_count = len(list(refs_dir.glob("*.md")))
        print(f"  ✓ refs/ ({md_count} MDs)")
    else:
        print(f"  ✗ refs/")

    # Diretório de PDFs manuais
    if pdfs_dir.exists():
        pdf_count = len(list(pdfs_dir.glob("*.pdf")))
        print(f"  ✓ pdfs-manual/ ({pdf_count} PDFs)")
    else:
        print(f"  ✗ pdfs-manual/")

    return 0


def cmd_export(args) -> int:
    """Exporta referências em formato específico."""
    from .exporters.bib_writer import write_bib
    from .exporters.json_export import save_refs_json

    refs = _load_latest_refs(args.project)
    if not refs:
        print("Nenhuma referência encontrada para exportar.")
        return 1

    pipeline_dir = get_pipeline_dir(args.project)

    if args.format == "bib":
        output = pipeline_dir / "refs.bib"
        count = write_bib(refs, output)
        print(f"Exportado {count} entradas para {output}")
    elif args.format == "json":
        output = pipeline_dir / "refs-export.json"
        save_refs_json(refs, output)
        print(f"Exportado para {output}")

    return 0


def cmd_audit(args) -> int:
    """Gera relatório de auditoria."""
    from .exporters.audit_report import generate_audit_report

    config = load_scope(args.project)
    refs = _load_latest_refs(args.project)
    if not refs:
        print("Nenhuma referência encontrada para auditar.")
        return 1

    pipeline_dir = get_pipeline_dir(args.project)
    output = pipeline_dir / "audit-report.md"
    generate_audit_report(refs, config, output)
    print(f"Relatório gerado: {output}")
    return 0


def cmd_seed(args) -> int:
    """Extrai seed refs de textos existentes."""
    from .stages.s00_seed import extract_seed_refs

    source = getattr(args, "source", None)
    refs = extract_seed_refs(args.project, source_file=source)
    if refs:
        print(f"\n{len(refs)} seed refs extraídas.")
        print("Use 'python -m tools run' para integrar ao pipeline.")
    else:
        print("Nenhuma seed ref encontrada.")
    return 0


def cmd_extract(args) -> int:
    """Extrai conteúdo das refs para MDs individuais."""
    from .stages.s08b_extract import extract_references

    config = load_scope(args.project)
    refs = _load_latest_refs(args.project)
    if not refs:
        print("Nenhuma referência encontrada para extrair.")
        return 1

    extract_references(refs, config)
    return 0


def cmd_extract_manual(args) -> int:
    """Processa PDFs manuais."""
    from .stages.s08c_manual_extract import extract_manual_pdfs

    processed = extract_manual_pdfs(args.project)
    if processed:
        print(f"\n{processed} PDFs processados com sucesso.")
    return 0


def cmd_verify_refs(args) -> int:
    """Verifica referências Vancouver de um arquivo .md."""
    from .verify_markdown_refs import verify_markdown_refs

    filepath = args.file
    output = getattr(args, "output", None)
    results = verify_markdown_refs(filepath, output=output)

    mismatches = [r for r in results if r["status"] == "mismatch"]
    return 1 if mismatches else 0


def cmd_verify_claims(args) -> int:
    """Verifica se afirmações do texto são suportadas pelas referências."""
    from .verify_claims import run_verify_claims

    filepath = args.file
    project = getattr(args, "project", None)
    output = getattr(args, "output", None)

    results = run_verify_claims(filepath, project=project, output=output)

    # Retornar código de erro se há claims CRITICAL ou HIGH
    critical_high = [
        r for r in results
        if r.fabrication_risk.value in ("CRITICAL", "HIGH")
    ]
    return 1 if critical_high else 0


def cmd_extract_facts(args) -> int:
    """Extrai candidatos a fatos das referências (Camada 1)."""
    from .extract_facts import run_extract_facts

    ref_range = _parse_ref_range(args.refs) if args.refs else None
    return run_extract_facts(args.project, ref_range=ref_range, section=args.section)


def cmd_facts_import(args) -> int:
    """Valida e importa fatos de .facts-pending.jsonl."""
    from .extract_facts import run_facts_import

    return run_facts_import(args.project)


def cmd_facts_status(args) -> int:
    """Exibe status do Facts Registry."""
    from .extract_facts import run_facts_status

    section = getattr(args, "section", None)
    return run_facts_status(args.project, section=section)


def cmd_facts_report(args) -> int:
    """Gera relatório completo do Facts Registry."""
    from .extract_facts import run_facts_report

    output = getattr(args, "output", None)
    return run_facts_report(args.project, output=output)


def cmd_facts_crosscheck(args) -> int:
    """Cruza .claims-data.json com o Facts Registry."""
    from .extract_facts import run_facts_crosscheck

    return run_facts_crosscheck(args.project)


def _parse_ref_range(spec: str) -> list[int]:
    """Parseia especificação de range de refs: '1,5,11-17' → [1, 5, 11, 12, ..., 17]."""
    result: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            result.extend(range(int(start.strip()), int(end.strip()) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


def cmd_descriptors(args) -> int:
    """Sugere descritores MeSH/DeCS para os termos livres do scope.yaml."""
    from .apis.mesh_lookup import suggest_for_config

    config = load_scope(args.project)
    print(f"Consultando a API MeSH da NLM para os termos de '{args.project}'...")
    suggestions = suggest_for_config(config)

    pipeline_dir = get_pipeline_dir(args.project)
    out = pipeline_dir / "descriptors-suggested.md"
    lines = [
        f"# Descritores sugeridos — {args.project}",
        "",
        "Fonte: NLM MeSH lookup (id.nlm.nih.gov). Curadoria é do pesquisador:",
        "copie os descritores confirmados para `mesh_terms:` (e `decs_terms:`)",
        "no scope.yaml. O DeCS aceita o rótulo MeSH em inglês no campo `mh:`;",
        "traduções PT/ES em https://decs.bvsalud.org.",
        "",
    ]
    found_any = False
    for term, matches in suggestions.items():
        lines.append(f"## {term}")
        if not matches:
            lines.append("- (nenhum descritor MeSH encontrado)")
        for m in matches:
            found_any = True
            marker = " ← **match exato**" if m["exact"] else ""
            lines.append(f"- `{m['label']}`{marker} ({m['uri']})")
        lines.append("")

    exact = [
        m["label"]
        for matches in suggestions.values()
        for m in matches
        if m["exact"]
    ]
    if exact:
        lines += [
            "## Bloco pronto para o scope.yaml (matches exatos)",
            "",
            "```yaml",
            "mesh_terms:",
        ]
        lines += [f'  - "{label}"' for label in dict.fromkeys(exact)]
        lines += ["```", ""]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Sugestões salvas em: {out}")
    if not found_any:
        print("Nenhum descritor encontrado — verifique os termos (em inglês).")
    return 0


def cmd_screen_export(args) -> int:
    """Exporta lote de triagem semântica para o revisor LLM."""
    from .screening import export_screening_batch

    config = load_scope(args.project)
    result = export_screening_batch(args.project, config)
    return 0 if result.get("exported") else 1


def cmd_screen_import(args) -> int:
    """Importa veredictos da triagem LLM e calcula concordância."""
    from .screening import import_screening_verdicts

    result = import_screening_verdicts(args.project)
    return 0 if result.get("imported") else 1


def cmd_prisma(args) -> int:
    """Gera fluxograma PRISMA canônico a partir do último checkpoint."""
    from .exporters.prisma_flow import generate_prisma_flow

    config = load_scope(args.project)
    refs = _load_latest_refs(args.project)
    if not refs:
        print("Nenhuma referência encontrada. Rode o pipeline antes.")
        return 1
    pipeline_dir = get_pipeline_dir(args.project)
    generate_prisma_flow(refs, config, pipeline_dir / "prisma-flow.md")
    return 0


def cmd_semantic(args) -> int:
    """Aplica a camada semântica ao último checkpoint completo."""
    import json as _json

    from .semantic import apply_semantic_layer

    config = load_scope(args.project)
    refs = _load_latest_refs(args.project, prefer_full=True)
    if not refs:
        print("Nenhuma referência encontrada. Rode o pipeline antes.")
        return 1

    result = apply_semantic_layer(refs, config)
    if not result["scored"]:
        return 1

    # Persistir scores no checkpoint completo
    pipeline_dir = get_pipeline_dir(args.project)
    for name in ("refs-verified.json", "refs-dedup.json"):
        path = pipeline_dir / name
        if path.exists():
            by_id = {r.id: r for r in refs}
            data = _json.loads(path.read_text(encoding="utf-8"))
            for d in data.get("references", []):
                r = by_id.get(d.get("id"))
                if r and r.semantic_score is not None:
                    d["semantic_score"] = r.semantic_score
                    d["relevance"] = r.relevance.value
                    d["relevance_method"] = r.relevance_method
            path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  Scores persistidos em {name}")
            break

    # Top e resgates
    scored = sorted(
        (r for r in refs if r.semantic_score is not None and not r.is_duplicate),
        key=lambda r: r.semantic_score, reverse=True,
    )
    print("\n  Top 5 por similaridade semântica:")
    for r in scored[:5]:
        print(f"    {r.semantic_score:.3f}  {r.title[:70]}")
    rescued = [r for r in scored if "semantic_rescue" in r.relevance_method]
    if rescued:
        print(f"\n  Resgatadas ({len(rescued)}):")
        for r in rescued[:10]:
            print(f"    {r.semantic_score:.3f}  {r.title[:70]}")
    print(f"\n  Próximo passo: python -m tools run \"{args.project}\" --from-stage 6")
    return 0


def _load_latest_refs(project: str, prefer_full: bool = False) -> list:
    """Carrega referências do último checkpoint disponível.

    prefer_full=True: prioriza o conjunto completo (refs-verified) sobre
    refs-final (já filtrado) — necessário para re-triagem.
    """
    pipeline_dir = get_pipeline_dir(project, create=False)

    if prefer_full:
        candidates = [
            "refs-verified.json",
            "refs-dedup.json",
            "refs-final.json",
            "refs-raw.json",
        ]
    else:
        candidates = [
            "refs-final.json",
            "refs-verified.json",
            "refs-dedup.json",
            "refs-raw.json",
        ]

    for name in candidates:
        path = pipeline_dir / name
        if path.exists():
            print(f"  Carregando {name}...")
            return load_refs_json(path)

    return []
