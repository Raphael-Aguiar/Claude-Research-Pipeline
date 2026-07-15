"""Importação de arquivos RIS — caminho legítimo para LILACS/SciELO via BVS.

Contexto (verificado em 2026-07-15 na documentação oficial da BIREME,
docs.api.bvsalud.org): o acesso via API aos registros bibliográficos da
BVS NÃO está disponível para uso público — nem por solicitação, parceria
ou pagamento. A própria BIREME indica o caminho: consultar pelo Portal
Regional (https://pesquisa.bvsalud.org, funciona no navegador) e exportar
os resultados (RIS/CSV).

Fluxo para revisões que exigem LILACS/SciELO:
  1. Executar a estratégia de busca no portal BVS pelo navegador
     (documentar a string e a data no search-log — exigência PRISMA).
  2. Exportar os resultados em RIS.
  3. `python -m tools import-ris "Projeto" arquivo.ris`
  4. `python -m tools run "Projeto" --from-stage 3` — dedup, verificação
     zero-trust e triagem tratam os registros importados como quaisquer
     outros (DOI/título verificados; sem identificador → pendência).

O parser cobre o RIS padrão (também aceita exports do Zotero, Rayyan,
Web of Science, Scopus e SciELO, que usam o mesmo formato).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import get_pipeline_dir
from .models import Reference

_TAG_RE = re.compile(r"^([A-Z][A-Z0-9])  ?- ?(.*)$")


def parse_ris(text: str) -> list[dict]:
    """Parseia um arquivo RIS em lista de dicts tag→valores.

    Cada registro termina em `ER  -`. Tags repetidas (AU, KW) acumulam.
    """
    records: list[dict] = []
    current: dict[str, list[str]] = {}
    last_tag: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("﻿").rstrip()
        if not line.strip():
            continue
        m = _TAG_RE.match(line)
        if m:
            tag, value = m.group(1), m.group(2).strip()
            if tag == "ER":
                if current:
                    records.append(current)
                current = {}
                last_tag = None
                continue
            current.setdefault(tag, []).append(value)
            last_tag = tag
        elif last_tag and current:
            # Linha de continuação (valor multilinha, comum em abstracts)
            current[last_tag][-1] += " " + line.strip()

    if current:  # registro final sem ER explícito
        records.append(current)
    return records


def _first(rec: dict, *tags: str) -> str:
    for t in tags:
        if rec.get(t):
            return rec[t][0]
    return ""


def ris_record_to_reference(rec: dict, source_label: str, idx: int) -> Reference | None:
    """Converte um registro RIS em Reference."""
    title = _first(rec, "TI", "T1")
    if not title:
        return None

    year = None
    year_raw = _first(rec, "PY", "Y1", "DA")
    m = re.search(r"(\d{4})", year_raw)
    if m:
        year = int(m.group(1))

    doi = _first(rec, "DO", "DI") or None
    if doi:
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()

    pages = _first(rec, "SP")
    ep = _first(rec, "EP")
    if pages and ep:
        pages = f"{pages}-{ep}"

    return Reference(
        id=f"{source_label}_{idx:04d}",
        title=title,
        authors=[a.strip() for a in rec.get("AU", rec.get("A1", []))],
        year=year,
        doi=doi,
        journal=_first(rec, "JO", "JF", "T2", "JA") or None,
        volume=_first(rec, "VL") or None,
        issue=_first(rec, "IS") or None,
        pages=pages or None,
        url=_first(rec, "UR") or None,
        abstract=_first(rec, "AB", "N2") or None,
        pub_type=_first(rec, "TY") or None,
        source_api=source_label,
        source="manual-import",
    )


def import_ris_file(
    project_name: str,
    ris_path: str | Path,
    source_label: str = "bvs-portal",
) -> dict:
    """Importa um RIS para o refs-raw.json do projeto (append + log).

    Os registros entram no pipeline como qualquer resultado de busca:
    dedup (etapa 3) e verificação zero-trust (etapa 4) se aplicam.
    """
    ris_path = Path(ris_path).expanduser()
    if not ris_path.exists():
        print(f"  Arquivo não encontrado: {ris_path}")
        return {"imported": 0}

    records = parse_ris(ris_path.read_text(encoding="utf-8", errors="replace"))
    refs = []
    skipped = 0
    for i, rec in enumerate(records, 1):
        ref = ris_record_to_reference(rec, source_label, i)
        if ref:
            refs.append(ref)
        else:
            skipped += 1

    if not refs:
        print(f"  Nenhum registro válido em {ris_path.name} "
              f"({len(records)} blocos lidos, {skipped} sem título)")
        return {"imported": 0, "skipped": skipped}

    pipeline_dir = get_pipeline_dir(project_name)
    raw_path = pipeline_dir / "refs-raw.json"
    if raw_path.exists():
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        data = {"metadata": {"project": project_name}, "references": []}

    existing_ids = {d.get("id") for d in data["references"]}
    added = 0
    for ref in refs:
        while ref.id in existing_ids:
            ref.id += "b"
        data["references"].append(ref.to_dict())
        existing_ids.add(ref.id)
        added += 1

    data["metadata"][f"import_{source_label}"] = {
        "file": ris_path.name,
        "imported": added,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    raw_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Registrar no search-log (reprodutibilidade PRISMA)
    log_path = pipeline_dir / "search-log.md"
    entry = (
        f"\n### {source_label} (importação RIS manual)\n\n"
        f"- **Arquivo:** {ris_path.name}\n"
        f"- **Registros importados:** {added} ({skipped} sem título ignorados)\n"
        f"- **Data da importação:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"- **AÇÃO NECESSÁRIA:** documentar aqui a string de busca usada no "
        f"portal e a data da busca (exigência PRISMA).\n"
    )
    if log_path.exists():
        log_path.write_text(
            log_path.read_text(encoding="utf-8") + entry, encoding="utf-8"
        )
    else:
        log_path.write_text(f"# Search Log — {project_name}\n{entry}", encoding="utf-8")

    with_doi = sum(1 for r in refs if r.doi)
    print(f"  {added} registros importados de {ris_path.name} "
          f"({with_doi} com DOI, {added - with_doi} sem DOI — irão para "
          f"resgate por título na etapa 4)")
    print(f"  Próximo passo: python -m tools run \"{project_name}\" --from-stage 3")
    return {"imported": added, "skipped": skipped, "with_doi": with_doi}
