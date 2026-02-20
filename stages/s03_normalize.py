"""Etapa 3 — Normalização + Deduplicação.

Dedup por PMID (prioridade) + DOI exato + fuzzy matching.
Threshold=90%. Log de suspeitas (80-89%).
Salva refs-dedup.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..config import get_pipeline_dir
from ..models import Reference

# Thresholds
FUZZY_THRESHOLD = 90
SUSPECT_THRESHOLD = 80  # Log de possíveis duplicatas para revisão


def normalize_and_dedup(
    refs: list[Reference],
    project_name: str,
) -> list[Reference]:
    """Normaliza e deduplica referências.

    Estratégia em três passadas:
    1. Dedup exato por PMID (chave primária quando disponível)
    2. Dedup exato por DOI
    3. Dedup fuzzy por título + ano + primeiro autor (threshold 90%)

    Log de "possíveis duplicatas" (similarity 80-89%) para revisão.
    Detecção de preprint vs. publicado (mesmos autores + ano ±1).
    """
    print(f"\n  Normalizando {len(refs)} referências...")

    # Normalizar campos
    for ref in refs:
        _normalize_fields(ref)

    # Passada 1: PMID exato (prioridade sobre DOI)
    pmid_groups: dict[str, list[int]] = {}
    for i, ref in enumerate(refs):
        if ref.pmid:
            pmid_key = ref.pmid.strip()
            pmid_groups.setdefault(pmid_key, []).append(i)

    duplicates_pmid = 0
    for pmid_key, indices in pmid_groups.items():
        if len(indices) > 1:
            canonical_idx = indices[0]
            canonical_ref = refs[canonical_idx]
            for idx in indices[1:]:
                refs[idx].is_duplicate = True
                refs[idx].canonical_id = canonical_ref.id
                duplicates_pmid += 1
                # Merge: se canônico não tem DOI mas duplicata tem, copiar
                if not canonical_ref.doi and refs[idx].doi:
                    canonical_ref.doi = refs[idx].doi
                # Merge: citation_count (pegar o maior)
                if refs[idx].citation_count and (
                    not canonical_ref.citation_count
                    or refs[idx].citation_count > canonical_ref.citation_count
                ):
                    canonical_ref.citation_count = refs[idx].citation_count

    print(f"  → {duplicates_pmid} duplicatas por PMID")

    # Passada 2: DOI exato
    doi_groups: dict[str, list[int]] = {}
    for i, ref in enumerate(refs):
        if ref.doi and not ref.is_duplicate:
            doi_key = ref.doi.lower().strip()
            doi_groups.setdefault(doi_key, []).append(i)

    duplicates_doi = 0
    for doi_key, indices in doi_groups.items():
        if len(indices) > 1:
            canonical_idx = indices[0]
            canonical_ref = refs[canonical_idx]
            for idx in indices[1:]:
                refs[idx].is_duplicate = True
                refs[idx].canonical_id = canonical_ref.id
                duplicates_doi += 1
                # Merge citation_count
                if refs[idx].citation_count and (
                    not canonical_ref.citation_count
                    or refs[idx].citation_count > canonical_ref.citation_count
                ):
                    canonical_ref.citation_count = refs[idx].citation_count

    print(f"  → {duplicates_doi} duplicatas por DOI exato")

    # Passada 3: fuzzy matching para refs não duplicadas
    non_dup_indices = [i for i, r in enumerate(refs) if not r.is_duplicate]
    duplicates_fuzzy = 0
    suspects: list[dict] = []

    for i_pos, i in enumerate(non_dup_indices):
        if refs[i].is_duplicate:
            continue
        for j in non_dup_indices[i_pos + 1:]:
            if refs[j].is_duplicate:
                continue
            similarity = _fuzzy_similarity(refs[i], refs[j])
            if similarity >= FUZZY_THRESHOLD:
                refs[j].is_duplicate = True
                refs[j].canonical_id = refs[i].id
                duplicates_fuzzy += 1
                # Merge citation_count
                if refs[j].citation_count and (
                    not refs[i].citation_count
                    or refs[j].citation_count > refs[i].citation_count
                ):
                    refs[i].citation_count = refs[j].citation_count
            elif similarity >= SUSPECT_THRESHOLD:
                # Log de suspeitas para revisão manual
                suspects.append({
                    "ref_a": refs[i].id,
                    "title_a": refs[i].title[:80],
                    "ref_b": refs[j].id,
                    "title_b": refs[j].title[:80],
                    "similarity": similarity,
                    "reason": _suspect_reason(refs[i], refs[j]),
                })

    print(f"  → {duplicates_fuzzy} duplicatas por fuzzy matching (threshold {FUZZY_THRESHOLD}%)")
    if suspects:
        print(f"  → {len(suspects)} possíveis duplicatas para revisão (similarity {SUSPECT_THRESHOLD}-{FUZZY_THRESHOLD - 1}%)")

    unique_count = sum(1 for r in refs if not r.is_duplicate)
    print(f"  → {unique_count} referências únicas")

    # Salvar refs-dedup.json
    pipeline_dir = get_pipeline_dir(project_name)
    dedup_path = pipeline_dir / "refs-dedup.json"
    data = {
        "metadata": {
            "project": project_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_refs": len(refs),
            "unique_refs": unique_count,
            "duplicates_pmid": duplicates_pmid,
            "duplicates_doi": duplicates_doi,
            "duplicates_fuzzy": duplicates_fuzzy,
            "suspects": suspects,
        },
        "references": [ref.to_dict() for ref in refs],
    }
    with open(dedup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  refs-dedup.json salvo: {dedup_path}")

    return refs


def _normalize_fields(ref: Reference) -> None:
    """Normaliza campos da referência para comparação."""
    if ref.doi:
        ref.doi = ref.doi.strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if ref.doi.startswith(prefix):
                ref.doi = ref.doi[len(prefix):]

    if ref.title:
        ref.title = ref.title.strip()

    if ref.pmid:
        ref.pmid = ref.pmid.strip()


def _fuzzy_similarity(a: Reference, b: Reference) -> float:
    """Calcula score de similaridade entre duas referências.

    Retorna score 0-100 baseado em título + ano + primeiro autor.
    """
    if not a.title or not b.title:
        return 0.0

    title_sim = fuzz.ratio(a.title.lower(), b.title.lower())
    if title_sim < SUSPECT_THRESHOLD:
        return 0.0

    # Penalizar se anos divergem (mas aceitar ±1 para preprints)
    year_penalty = 0.0
    if a.year and b.year:
        diff = abs(a.year - b.year)
        if diff == 0:
            year_penalty = 0.0
        elif diff == 1:
            year_penalty = 2.0  # Possível preprint vs publicado
        else:
            return 0.0  # Anos muito diferentes → não é duplicata

    # Verificar primeiro autor
    author_bonus = 0.0
    if a.authors and b.authors:
        author_sim = fuzz.ratio(
            a.authors[0].lower(),
            b.authors[0].lower(),
        )
        if author_sim < 60:
            return 0.0  # Autores muito diferentes
        if author_sim >= 80:
            author_bonus = 5.0  # Autores similares → mais provável duplicata

    return title_sim - year_penalty + author_bonus


def _suspect_reason(a: Reference, b: Reference) -> str:
    """Determina a razão da suspeita de duplicata."""
    if a.year and b.year and abs(a.year - b.year) == 1:
        return "possível preprint vs. publicado (anos adjacentes)"
    if a.journal and b.journal and a.journal != b.journal:
        return "mesmo título em journals diferentes (tradução?)"
    return "títulos similares"
