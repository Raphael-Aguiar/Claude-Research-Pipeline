"""Etapa 4 — Verificação de Existência e Metadados.

Verifica DOIs no CrossRef, valida autores e journal contra
PubMed (prioridade) e CrossRef (fallback).
Salva refs-verified.json + verification-report.md.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..apis.crossref import (
    extract_authors_from_crossref,
    extract_journal_from_crossref,
    verify_doi,
)
from ..config import get_api_config, get_pipeline_dir
from ..models import Reference


def _compare_authors(
    stored: list[str],
    verified: list[str],
) -> tuple[bool, float, list[str]]:
    """Compara listas de autores e retorna (match, score, issues).

    Comparação:
    1. Primeiro autor: fuzzy match >= 85
    2. Demais autores (até 6): fuzzy match >= 80 na média
    3. Quantidade: divergência > 50% = flag
    """
    issues = []
    if not verified:
        return True, 100.0, []  # Nada para comparar

    if not stored:
        issues.append("Sem autores armazenados para comparar")
        return False, 0.0, issues

    # Normalizar nomes (lowercase, remover pontos)
    def norm(name: str) -> str:
        return name.lower().replace(".", "").strip()

    # Primeiro autor
    first_stored = norm(stored[0])
    first_verified = norm(verified[0])
    first_score = fuzz.ratio(first_stored, first_verified)

    if first_score < 85:
        issues.append(
            f"Primeiro autor diverge: '{stored[0]}' vs '{verified[0]}' "
            f"(score={first_score:.0f})"
        )

    # Demais autores (comparar até 6)
    max_compare = min(6, len(stored), len(verified))
    scores = [first_score]
    for i in range(1, max_compare):
        s = norm(stored[i]) if i < len(stored) else ""
        v = norm(verified[i]) if i < len(verified) else ""
        if s and v:
            score = fuzz.ratio(s, v)
            scores.append(score)
            if score < 80:
                issues.append(
                    f"Autor {i + 1} diverge: '{stored[i]}' vs '{verified[i]}' "
                    f"(score={score:.0f})"
                )

    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Quantidade de autores
    if len(stored) > 0 and len(verified) > 0:
        ratio = min(len(stored), len(verified)) / max(len(stored), len(verified))
        if ratio < 0.5:
            issues.append(
                f"Qtd autores diverge: {len(stored)} armazenados "
                f"vs {len(verified)} verificados"
            )

    match = len(issues) == 0
    return match, avg_score, issues


def _compare_journal(
    stored_journal: str | None,
    verified_journal: str,
    verified_journal_full: str = "",
) -> tuple[bool, list[str]]:
    """Compara nome do journal.

    Aceita match parcial, abreviações NLM e nomes completos.
    """
    if not stored_journal or not verified_journal:
        return True, []

    s = stored_journal.lower().replace(".", "").strip()
    v = verified_journal.lower().replace(".", "").strip()
    v_full = verified_journal_full.lower().replace(".", "").strip() if verified_journal_full else ""

    # Match direto
    if s == v or s == v_full:
        return True, []

    # Match fuzzy
    score_short = fuzz.ratio(s, v)
    score_full = fuzz.ratio(s, v_full) if v_full else 0

    if max(score_short, score_full) >= 75:
        return True, []

    # Verificar se um contém o outro (ex: "Bioengineering" vs "Bioengineering (Basel)")
    if s in v or s in v_full or v in s:
        return True, []

    # Verificar abreviação: cada palavra do nome curto é prefixo de
    # uma palavra do nome longo (ex: "Appl Sci" → "Applied Sciences")
    if _is_abbreviation_of(s, v_full) or _is_abbreviation_of(s, v):
        return True, []

    return False, [
        f"Journal diverge: '{stored_journal}' vs '{verified_journal}'"
        + (f" ({verified_journal_full})" if verified_journal_full else "")
    ]


def _is_abbreviation_of(abbrev: str, full: str) -> bool:
    """Verifica se abbrev é uma abreviação plausível de full.

    Ex: 'appl sci' é abreviação de 'applied sciences'
        'technol forecast soc change' é abreviação de
        'technological forecasting and social change'
    """
    if not abbrev or not full:
        return False

    abbrev_words = abbrev.split()
    full_words = [w for w in full.split() if w not in ("and", "of", "the", "in", "for", "on")]

    if not abbrev_words or not full_words:
        return False

    # Cada palavra abreviada deve ser prefixo de alguma palavra no nome completo
    matched = 0
    full_remaining = list(full_words)
    for aw in abbrev_words:
        for i, fw in enumerate(full_remaining):
            if fw.startswith(aw) or aw.startswith(fw):
                matched += 1
                full_remaining.pop(i)
                break

    return matched >= len(abbrev_words) * 0.8


def verify_references(
    refs: list[Reference],
    project_name: str,
) -> list[Reference]:
    """Verifica existência e metadados das referências.

    Para cada referência com DOI:
    1. Verifica DOI no CrossRef (existência + título)
    2. Busca metadados autoritativos no PubMed (se indexado) ou CrossRef
    3. Compara autores, journal, volume, issue
    4. Marca discrepâncias em verification_issues

    Args:
        refs: Lista de referências (pós-dedup).
        project_name: Nome do projeto.

    Returns:
        Lista atualizada com campos de verificação preenchidos.
    """
    api_config = get_api_config()
    cr_email = api_config.get("CROSSREF_EMAIL", "")
    pm_email = api_config.get("NCBI_EMAIL", "")

    to_verify = [r for r in refs if not r.is_duplicate and r.doi]
    print(f"\n  Verificando {len(to_verify)} referências com DOI...")

    stats = {
        "verified": 0, "resolved": 0, "titles_matched": 0,
        "authors_matched": 0, "authors_mismatched": 0,
        "journal_mismatched": 0, "pubmed_found": 0,
    }

    issues_report: list[dict] = []

    for ref in to_verify:
        try:
            # Passo 1: Verificar DOI no CrossRef
            result = verify_doi(ref.doi, expected_title=ref.title, email=cr_email)

            ref.doi_resolves = result["resolves"]
            ref.crossref_match = result["title_match"]
            ref.crossref_title_similarity = result["title_similarity"]

            if result["resolves"]:
                stats["resolved"] += 1
                if result["pub_type"] and not ref.pub_type:
                    ref.pub_type = result["pub_type"]
                if result["is_retracted"]:
                    ref.retracted = True
                if result["has_update"]:
                    ref.has_correction = True
            if result["title_match"]:
                stats["titles_matched"] += 1

            # Passo 2: Buscar metadados autoritativos
            # Prioridade: PubMed (mais confiável para biomédicos) > CrossRef
            authoritative = None

            if pm_email and result["resolves"]:
                from ..apis.pubmed import fetch_pubmed_metadata

                pm_meta = fetch_pubmed_metadata(
                    doi=ref.doi, pmid=ref.pmid, email=pm_email,
                )
                if pm_meta and pm_meta.get("authors"):
                    authoritative = pm_meta
                    authoritative["source"] = "pubmed"
                    stats["pubmed_found"] += 1
                time.sleep(0.34)

            if not authoritative and result["resolves"] and result.get("metadata"):
                # Fallback: CrossRef
                cr_meta = result["metadata"]
                cr_authors = extract_authors_from_crossref(cr_meta)
                cr_journal = extract_journal_from_crossref(cr_meta)
                if cr_authors:
                    authoritative = {
                        "authors": cr_authors,
                        "journal_abbrev": cr_journal["journal_short"],
                        "journal": cr_journal["journal_full"],
                        "volume": cr_journal["volume"],
                        "issue": cr_journal["issue"],
                        "pages": cr_journal["pages"],
                        "year": cr_journal["year"],
                        "source": "crossref",
                    }

            # Passo 3: Comparar metadados
            if authoritative:
                verified_authors = authoritative["authors"]
                ref.verified_authors = verified_authors
                ref.verified_journal = (
                    authoritative.get("journal_abbrev")
                    or authoritative.get("journal", "")
                )
                ref.verified_volume = authoritative.get("volume", "")
                ref.verified_issue = authoritative.get("issue", "")
                ref.verified_pages = authoritative.get("pages", "")

                all_issues = []

                # Comparar autores
                match, score, author_issues = _compare_authors(
                    ref.authors, verified_authors,
                )
                ref.authors_verified = match
                if match:
                    stats["authors_matched"] += 1
                else:
                    stats["authors_mismatched"] += 1
                    all_issues.extend(author_issues)

                # Comparar journal
                j_match, j_issues = _compare_journal(
                    ref.journal,
                    authoritative.get("journal_abbrev", ""),
                    authoritative.get("journal", ""),
                )
                if not j_match:
                    stats["journal_mismatched"] += 1
                    all_issues.extend(j_issues)

                # Comparar volume/issue
                if (
                    ref.volume
                    and authoritative.get("volume")
                    and ref.volume != authoritative["volume"]
                ):
                    all_issues.append(
                        f"Volume diverge: '{ref.volume}' vs "
                        f"'{authoritative['volume']}'"
                    )

                if (
                    ref.issue
                    and authoritative.get("issue")
                    and ref.issue != authoritative["issue"]
                ):
                    all_issues.append(
                        f"Issue diverge: '{ref.issue}' vs "
                        f"'{authoritative['issue']}'"
                    )

                ref.verification_issues = all_issues

                if all_issues:
                    issues_report.append({
                        "ref_id": ref.id,
                        "title": ref.title[:80],
                        "doi": ref.doi,
                        "source": authoritative.get("source", ""),
                        "issues": all_issues,
                        "stored_authors": ref.authors[:6],
                        "verified_authors": verified_authors[:6],
                    })
            else:
                ref.authors_verified = None

            stats["verified"] += 1
            if stats["verified"] % 10 == 0:
                print(f"  → {stats['verified']}/{len(to_verify)} verificadas...")

            time.sleep(0.1)

        except Exception as e:
            print(f"  → ERRO ao verificar {ref.doi}: {e}")
            ref.doi_resolves = None
            ref.crossref_match = None

    # Estatísticas
    print(f"  → {stats['resolved']}/{len(to_verify)} DOIs resolvem")
    print(f"  → {stats['titles_matched']}/{len(to_verify)} títulos conferem")
    print(f"  → {stats['authors_matched']} autores OK, "
          f"{stats['authors_mismatched']} com discrepâncias")
    if stats["journal_mismatched"]:
        print(f"  → {stats['journal_mismatched']} journals com discrepâncias")
    print(f"  → {stats['pubmed_found']} verificados via PubMed")

    no_doi = [r for r in refs if not r.is_duplicate and not r.doi]
    if no_doi:
        print(f"  → {len(no_doi)} referências sem DOI (não verificadas)")

    # Salvar refs-verified.json
    pipeline_dir = get_pipeline_dir(project_name)
    verified_path = pipeline_dir / "refs-verified.json"
    data = {
        "metadata": {
            "project": project_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_refs": len(refs),
            "verified": stats["verified"],
            "dois_resolved": stats["resolved"],
            "titles_matched": stats["titles_matched"],
            "authors_matched": stats["authors_matched"],
            "authors_mismatched": stats["authors_mismatched"],
            "journal_mismatched": stats["journal_mismatched"],
            "no_doi": len(no_doi),
        },
        "references": [ref.to_dict() for ref in refs],
    }
    with open(verified_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  refs-verified.json salvo: {verified_path}")

    # Gerar relatório de discrepâncias
    if issues_report:
        _write_verification_report(issues_report, pipeline_dir, stats)

    return refs


def _write_verification_report(
    issues: list[dict],
    pipeline_dir,
    stats: dict,
) -> None:
    """Gera verification-report.md com discrepâncias encontradas."""
    report_path = pipeline_dir / "verification-report.md"
    lines = [
        "# Relatório de Verificação de Metadados",
        "",
        f"**Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Referências verificadas:** {stats['verified']}",
        f"**Autores OK:** {stats['authors_matched']}",
        f"**Autores com discrepâncias:** {stats['authors_mismatched']}",
        f"**Journals com discrepâncias:** {stats['journal_mismatched']}",
        "",
        "---",
        "",
    ]

    for item in issues:
        lines.append(f"## [{item['ref_id']}] {item['title']}")
        lines.append(f"**DOI:** {item['doi']} | **Fonte:** {item['source']}")
        lines.append("")
        for issue in item["issues"]:
            lines.append(f"- {issue}")
        lines.append("")
        if item["stored_authors"] != item["verified_authors"]:
            stored = ", ".join(item["stored_authors"])
            verified = ", ".join(item["verified_authors"])
            lines.append(f"**Armazenado:** {stored}")
            lines.append(f"**Verificado:** {verified}")
            lines.append("")
        lines.append("---")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  verification-report.md salvo: {report_path}")
