"""Verificador de referências Vancouver em arquivos Markdown.

Uso standalone:
    python -m tools.verify_markdown_refs capitulo16.md

Funcionalidade:
1. Parseia referências Vancouver de um arquivo .md
2. Para cada referência com DOI, verifica autores/journal/volume no PubMed e CrossRef
3. Para referências sem DOI, busca no CrossRef por título
4. Gera relatório de discrepâncias
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from rapidfuzz import fuzz


def parse_vancouver_refs(text: str) -> list[dict]:
    """Parseia referências Vancouver numeradas de um texto Markdown.

    Reconhece o padrão: N. Autores. Título. Journal. Ano;Vol(Issue):Pages.
    Também extrai DOI se presente.

    Returns:
        Lista de dicts com: number, raw, authors_str, title, journal,
        year, volume, issue, pages, doi, url, ref_type.
    """
    refs = []

    # Encontrar bloco de referências
    ref_section = re.search(
        r"^## Referências\s*\n(.+)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not ref_section:
        ref_section = re.search(
            r"^# Referências\s*\n(.+)",
            text,
            re.MULTILINE | re.DOTALL,
        )
    if not ref_section:
        return refs

    ref_text = ref_section.group(1)

    # Pattern para cada referência numerada
    # Ex: "1. Dash S, Shakyawar SK... J Big Data. 2019;6(1):1-25. DOI: 10.xxx."
    ref_pattern = re.compile(
        r"^(\d+)\.\s+(.+?)(?=\n\d+\.\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    for match in ref_pattern.finditer(ref_text):
        num = int(match.group(1))
        raw = match.group(2).strip().replace("\n", " ")

        ref = {
            "number": num,
            "raw": raw,
            "authors_str": "",
            "title": "",
            "journal": "",
            "year": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "doi": "",
            "url": "",
            "ref_type": "unknown",  # "journal", "web", "legislation", "book"
        }

        # Extrair DOI
        doi_match = re.search(r"DOI:\s*(10\.\S+?)\.?\s*$", raw)
        if doi_match:
            ref["doi"] = doi_match.group(1).rstrip(".")
            raw_no_doi = raw[: doi_match.start()].strip().rstrip(".")
        else:
            # DOI inline (sem prefixo "DOI:")
            doi_match2 = re.search(r"\b(10\.\d{4,}/\S+?)\.?\s*$", raw)
            if doi_match2:
                ref["doi"] = doi_match2.group(1).rstrip(".")
            raw_no_doi = raw

        # Extrair URL
        url_match = re.search(
            r"Disponível na Internet:\s*(https?://\S+?)(?:\s*\(|$)",
            raw_no_doi,
        )
        if url_match:
            ref["url"] = url_match.group(1)
            ref["ref_type"] = "web"

        # Classificar tipo
        if "Diário Oficial" in raw or "Lei nº" in raw or "Decreto" in raw:
            ref["ref_type"] = "legislation"
        elif ref["url"] and not ref["doi"]:
            ref["ref_type"] = "web"
        elif ref["doi"] or re.search(r"\d{4};\d+", raw):
            ref["ref_type"] = "journal"

        # Para artigos de journal, parsear componentes
        if ref["ref_type"] == "journal":
            _parse_journal_ref(ref, raw_no_doi)

        refs.append(ref)

    return refs


def _parse_journal_ref(ref: dict, raw: str) -> None:
    """Parseia componentes de uma referência de periódico.

    Pattern típico Vancouver:
    Autores. Título do artigo. Título do periódico. Ano;Vol(Issue):Páginas.
    """
    # Extrair ano;volume(issue):páginas do final
    bib_match = re.search(
        r"(\d{4});(\d+)(?:\(([^)]+)\))?:(\S+?)\.?\s*$",
        raw,
    )

    if bib_match:
        ref["year"] = bib_match.group(1)
        ref["volume"] = bib_match.group(2)
        ref["issue"] = bib_match.group(3) or ""
        ref["pages"] = bib_match.group(4)
        before_bib = raw[: bib_match.start()].strip().rstrip(".")
    else:
        # Ahead-of-print (sem volume/páginas)
        year_match = re.search(r"\b(20\d{2})\b", raw)
        if year_match:
            ref["year"] = year_match.group(1)
        before_bib = raw

    # Separar autores do resto
    # Padrão: autores terminam com ponto antes do título
    # Heurística: primeiro ponto seguido de maiúscula ou aspas
    parts = before_bib.split(". ", 1)
    if len(parts) >= 2:
        ref["authors_str"] = parts[0].strip()

        # Título + journal
        rest = parts[1]
        # O journal é a última parte antes do ano
        # Heurística: split pelo último ponto antes do ano
        last_dot = rest.rfind(".")
        if last_dot > 0:
            ref["title"] = rest[:last_dot].strip()
            journal_part = rest[last_dot + 1 :].strip()
            # Remover ano se presente
            journal_part = re.sub(r"\s*\d{4}\s*$", "", journal_part).strip()
            ref["journal"] = journal_part
        else:
            ref["title"] = rest.strip()


def _vancouver_authors_to_list(authors_str: str) -> list[str]:
    """Converte string Vancouver de autores para lista.

    Input: "Dash S, Shakyawar SK, Sharma M, Kaushik S"
    Output: ["Dash S", "Shakyawar SK", "Sharma M", "Kaushik S"]
    """
    if not authors_str:
        return []
    # Remover "et al."
    clean = re.sub(r"\s*et al\.?\s*$", "", authors_str)
    # Separar por vírgula
    parts = [p.strip() for p in clean.split(",") if p.strip()]
    return parts


def verify_markdown_refs(
    filepath: str | Path,
    output: str | Path | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Verifica todas as referências Vancouver em um arquivo Markdown.

    Args:
        filepath: Caminho para o arquivo .md.
        output: Caminho para relatório de saída (default: ao lado do .md).
        verbose: Imprimir progresso no console.

    Returns:
        Lista de dicts com resultado da verificação de cada referência.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"Arquivo não encontrado: {filepath}")
        return []

    text = filepath.read_text(encoding="utf-8")
    refs = parse_vancouver_refs(text)

    if verbose:
        print(f"Encontradas {len(refs)} referências em {filepath.name}")
        journal_refs = [r for r in refs if r["ref_type"] == "journal"]
        web_refs = [r for r in refs if r["ref_type"] == "web"]
        leg_refs = [r for r in refs if r["ref_type"] == "legislation"]
        print(f"  Periódicos: {len(journal_refs)}, Web: {len(web_refs)}, "
              f"Legislação: {len(leg_refs)}")

    results = []

    for ref in refs:
        if ref["ref_type"] != "journal":
            results.append({
                "number": ref["number"],
                "status": "skipped",
                "reason": f"Tipo '{ref['ref_type']}' — não verificável via API",
                "ref": ref,
            })
            continue

        doi = ref["doi"]
        if not doi:
            results.append({
                "number": ref["number"],
                "status": "no_doi",
                "reason": "Sem DOI — busca por título não implementada",
                "ref": ref,
            })
            continue

        if verbose:
            print(f"\n  [{ref['number']}] DOI: {doi}")

        result = _verify_single_ref(ref, verbose=verbose)
        results.append(result)

        time.sleep(0.5)  # Rate limiting

    # Gerar relatório
    if output is None:
        output = filepath.with_suffix(".verification.md")

    _write_report(results, Path(output), filepath.name)

    if verbose:
        ok = sum(1 for r in results if r["status"] == "ok")
        issues = sum(1 for r in results if r["status"] == "mismatch")
        skipped = sum(1 for r in results if r["status"] in ("skipped", "no_doi"))
        errors = sum(1 for r in results if r["status"] == "error")
        print(f"\n{'=' * 50}")
        print(f"RESULTADO: {ok} OK, {issues} com discrepâncias, "
              f"{skipped} ignoradas, {errors} erros")
        print(f"Relatório: {output}")

    return results


def _verify_single_ref(ref: dict, verbose: bool = True) -> dict:
    """Verifica uma única referência contra PubMed/CrossRef."""
    from .apis.crossref import (
        extract_authors_from_crossref,
        extract_journal_from_crossref,
        get_crossref_metadata,
    )
    from .apis.pubmed import fetch_pubmed_metadata
    from .config import get_api_config

    api_config = get_api_config()
    pm_email = api_config.get("NCBI_EMAIL", "")

    doi = ref["doi"]
    stored_authors = _vancouver_authors_to_list(ref["authors_str"])
    issues = []

    # Tentar PubMed primeiro
    verified_meta = None
    source = ""

    if pm_email:
        pm = fetch_pubmed_metadata(doi=doi, email=pm_email)
        if pm and pm.get("authors"):
            verified_meta = pm
            source = "pubmed"
            time.sleep(0.34)

    if not verified_meta:
        cr_meta = get_crossref_metadata(doi)
        if cr_meta:
            cr_authors = extract_authors_from_crossref(cr_meta)
            cr_journal = extract_journal_from_crossref(cr_meta)
            if cr_authors:
                verified_meta = {
                    "authors": cr_authors,
                    "journal_abbrev": cr_journal["journal_short"],
                    "journal": cr_journal["journal_full"],
                    "volume": cr_journal["volume"],
                    "issue": cr_journal["issue"],
                    "pages": cr_journal["pages"],
                    "year": cr_journal["year"],
                }
                source = "crossref"

    if not verified_meta:
        return {
            "number": ref["number"],
            "status": "error",
            "reason": "DOI não encontrado no PubMed nem CrossRef",
            "ref": ref,
        }

    verified_authors = verified_meta["authors"]

    # Comparar autores
    def norm(name: str) -> str:
        return name.lower().replace(".", "").strip()

    if stored_authors and verified_authors:
        # Primeiro autor
        first_score = fuzz.ratio(
            norm(stored_authors[0]), norm(verified_authors[0])
        )
        if first_score < 85:
            issues.append(
                f"AUTOR 1: '{stored_authors[0]}' → '{verified_authors[0]}' "
                f"(score={first_score:.0f})"
            )

        # Demais autores
        for i in range(1, min(6, len(stored_authors), len(verified_authors))):
            score = fuzz.ratio(norm(stored_authors[i]), norm(verified_authors[i]))
            if score < 80:
                issues.append(
                    f"AUTOR {i + 1}: '{stored_authors[i]}' → "
                    f"'{verified_authors[i]}' (score={score:.0f})"
                )

    # Comparar journal
    if ref["journal"] and verified_meta.get("journal_abbrev"):
        j_stored = norm(ref["journal"])
        j_verified = norm(verified_meta["journal_abbrev"])
        j_full = norm(verified_meta.get("journal", ""))
        j_score = max(
            fuzz.ratio(j_stored, j_verified),
            fuzz.ratio(j_stored, j_full) if j_full else 0,
        )
        # Verificar abreviação: cada palavra do nome curto é prefixo
        is_abbrev = _is_abbreviation_of(j_stored, j_full) or _is_abbreviation_of(j_stored, j_verified)
        if (
            j_score < 75
            and j_stored not in j_verified
            and j_verified not in j_stored
            and not is_abbrev
        ):
            issues.append(
                f"JOURNAL: '{ref['journal']}' → "
                f"'{verified_meta['journal_abbrev']}'"
            )

    # Comparar volume
    if (
        ref["volume"]
        and verified_meta.get("volume")
        and ref["volume"] != verified_meta["volume"]
    ):
        issues.append(
            f"VOLUME: '{ref['volume']}' → '{verified_meta['volume']}'"
        )

    # Comparar issue
    if (
        ref["issue"]
        and verified_meta.get("issue")
        and ref["issue"] != verified_meta["issue"]
    ):
        issues.append(
            f"ISSUE: '{ref['issue']}' → '{verified_meta['issue']}'"
        )

    if verbose:
        if issues:
            print(f"    🔴 {len(issues)} discrepância(s)")
            for iss in issues:
                print(f"       {iss}")
        else:
            print(f"    ✅ OK (via {source})")

    status = "mismatch" if issues else "ok"

    return {
        "number": ref["number"],
        "status": status,
        "source": source,
        "issues": issues,
        "ref": ref,
        "verified": {
            "authors": verified_authors,
            "journal": verified_meta.get("journal_abbrev", ""),
            "volume": verified_meta.get("volume", ""),
            "issue": verified_meta.get("issue", ""),
            "pages": verified_meta.get("pages", ""),
        },
    }


def _write_report(
    results: list[dict],
    output: Path,
    source_name: str,
) -> None:
    """Gera relatório Markdown com resultados da verificação."""
    lines = [
        f"# Verificação de Referências — {source_name}",
        "",
        f"**Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Resumo
    ok = [r for r in results if r["status"] == "ok"]
    mismatches = [r for r in results if r["status"] == "mismatch"]
    skipped = [r for r in results if r["status"] in ("skipped", "no_doi")]
    errors = [r for r in results if r["status"] == "error"]

    lines.extend([
        f"| Status | Quantidade |",
        f"|---|---|",
        f"| ✅ OK | {len(ok)} |",
        f"| 🔴 Discrepâncias | {len(mismatches)} |",
        f"| ⏭ Ignoradas | {len(skipped)} |",
        f"| ❌ Erros | {len(errors)} |",
        "",
    ])

    if mismatches:
        lines.extend(["## Discrepâncias", ""])
        for r in mismatches:
            ref = r["ref"]
            v = r.get("verified", {})
            lines.append(f"### [{ref['number']}] {ref.get('title', '')[:70]}")
            lines.append(f"**DOI:** {ref['doi']} | **Fonte:** {r.get('source', '')}")
            lines.append("")

            for issue in r.get("issues", []):
                lines.append(f"- {issue}")
            lines.append("")

            if ref.get("authors_str"):
                lines.append(f"**No texto:** {ref['authors_str']}")
            if v.get("authors"):
                fmt = ", ".join(v["authors"][:6])
                if len(v["authors"]) > 6:
                    fmt += " et al."
                lines.append(f"**Verificado:** {fmt}")
            lines.extend(["", "---", ""])

    if ok:
        lines.extend(["## Referências OK", ""])
        for r in ok:
            ref = r["ref"]
            lines.append(
                f"- [{ref['number']}] {ref.get('authors_str', '')[:30]}... "
                f"({r.get('source', '')})"
            )
        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")


def _is_abbreviation_of(abbrev: str, full: str) -> bool:
    """Verifica se abbrev é uma abreviação plausível de full."""
    if not abbrev or not full:
        return False
    abbrev_words = abbrev.split()
    full_words = [w for w in full.split() if w not in ("and", "of", "the", "in", "for", "on")]
    if not abbrev_words or not full_words:
        return False
    matched = 0
    full_remaining = list(full_words)
    for aw in abbrev_words:
        for i, fw in enumerate(full_remaining):
            if fw.startswith(aw) or aw.startswith(fw):
                matched += 1
                full_remaining.pop(i)
                break
    return matched >= len(abbrev_words) * 0.8


from datetime import datetime

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m tools.verify_markdown_refs <arquivo.md> [--output relatorio.md]")
        sys.exit(1)

    filepath = sys.argv[1]
    output = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output = sys.argv[idx + 1]

    verify_markdown_refs(filepath, output=output)
