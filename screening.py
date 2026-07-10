"""Triagem semântica por LLM — dupla triagem com critérios de I/E em prosa.

Fluxo (dupla triagem, Cochrane MECIR C39 adaptado ao Atlas):
  1. `screen-export`  → gera screening-batch.jsonl + screening-instructions.md
  2. Claude Code (revisor 1) lê as instruções, julga cada ref contra os
     critérios de inclusão/exclusão em prosa do scope.yaml e escreve
     .screening-verdicts.jsonl
  3. `screen-import`  → valida, mescla veredictos nos checkpoints, calcula
     concordância (kappa de Cohen) entre a triagem por keywords (revisor
     automático) e a triagem LLM, e gera screening-report.md com as
     DIVERGÊNCIAS destacadas para o revisor humano (Raphael, revisor 2)
  4. `run --from-stage 5` → a etapa 6 combina os dois sinais (concordância
     aplica; divergência nunca descarta silenciosamente)

Formato de cada linha de .screening-verdicts.jsonl:
  {"id": "<ref.id>", "verdict": "include|exclude|maybe",
   "reason": "<1 frase>", "criteria": ["I1", "E2"]}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import get_pipeline_dir
from .models import Reference, Relevance, SearchConfig

VALID_VERDICTS = {"include", "exclude", "maybe"}

BATCH_FILE = "screening-batch.jsonl"
INSTRUCTIONS_FILE = "screening-instructions.md"
VERDICTS_FILE = ".screening-verdicts.jsonl"
REPORT_FILE = "screening-report.md"


def _load_checkpoint(pipeline_dir: Path) -> tuple[Path | None, list[Reference]]:
    """Carrega o checkpoint mais completo disponível."""
    for name in ("refs-verified.json", "refs-dedup.json", "refs-raw.json"):
        path = pipeline_dir / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            refs = [Reference.from_dict(d) for d in data.get("references", [])]
            return path, refs
    return None, []


def export_screening_batch(project_name: str, config: SearchConfig) -> dict:
    """Gera o lote de triagem para o revisor LLM."""
    pipeline_dir = get_pipeline_dir(project_name)
    checkpoint, refs = _load_checkpoint(pipeline_dir)
    if not refs:
        print("  Nenhum checkpoint de referências encontrado. Rode a busca antes.")
        return {"exported": 0}

    active = [r for r in refs if not r.is_duplicate]

    batch_path = pipeline_dir / BATCH_FILE
    with open(batch_path, "w", encoding="utf-8") as f:
        for r in active:
            f.write(json.dumps({
                "id": r.id,
                "title": r.title,
                "abstract": (r.abstract or "")[:1800],
                "year": r.year,
                "journal": r.journal,
                "pub_type": r.pub_type,
            }, ensure_ascii=False) + "\n")

    # Instruções para o revisor 1 (LLM)
    lines = [
        f"# Triagem semântica — {config.project_name}",
        "",
        f"**Modalidade:** {config.modality.value}",
        f"**Pergunta de pesquisa:** {config.research_question}",
        "",
        "## Critérios de INCLUSÃO",
        "",
    ]
    if config.inclusion_criteria:
        for i, c in enumerate(config.inclusion_criteria, 1):
            lines.append(f"- **I{i}**: {c}")
    else:
        lines.append("- (não definidos no scope.yaml — julgar pela pergunta de pesquisa)")
    lines += ["", "## Critérios de EXCLUSÃO", ""]
    if config.exclusion_criteria:
        for i, c in enumerate(config.exclusion_criteria, 1):
            lines.append(f"- **E{i}**: {c}")
    else:
        lines.append("- (não definidos no scope.yaml)")
    lines += [
        "",
        "## Protocolo do revisor LLM",
        "",
        f"1. Ler cada linha de `{BATCH_FILE}` (título + abstract).",
        "2. Julgar contra os critérios acima. Postura na dúvida: "
        + ("**incluir** (modalidade de revisão — Munn et al. 2018)."
           if config.modality.value != "pesquisa-base"
           else "**maybe** (pesquisa-base)."),
        "3. Para CADA ref, escrever uma linha JSON em "
        f"`{VERDICTS_FILE}`:",
        "",
        '   `{"id": "...", "verdict": "include|exclude|maybe", '
        '"reason": "<1 frase>", "criteria": ["I1"]}`',
        "",
        "4. NUNCA pular refs — cobertura deve ser 100% do lote.",
        "5. `reason` cita o critério decisivo; sem julgamento novo fora dos critérios.",
        "6. Ao terminar: `python -m tools screen-import \"" + config.project_name + "\"`.",
        "",
        f"Total de referências no lote: **{len(active)}**",
    ]
    (pipeline_dir / INSTRUCTIONS_FILE).write_text(
        "\n".join(lines), encoding="utf-8"
    )

    print(f"  Lote exportado: {batch_path} ({len(active)} refs)")
    print(f"  Instruções: {pipeline_dir / INSTRUCTIONS_FILE}")
    return {"exported": len(active), "batch": str(batch_path)}


def import_screening_verdicts(project_name: str) -> dict:
    """Valida e mescla os veredictos do LLM; calcula kappa; gera relatório."""
    pipeline_dir = get_pipeline_dir(project_name)
    verdicts_path = pipeline_dir / VERDICTS_FILE
    if not verdicts_path.exists():
        print(f"  {VERDICTS_FILE} não encontrado. Execute a triagem LLM antes.")
        return {"imported": 0}

    # Parse + validação dos veredictos
    verdicts: dict[str, dict] = {}
    errors: list[str] = []
    for n, line in enumerate(verdicts_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"linha {n}: JSON inválido ({e})")
            continue
        if v.get("verdict") not in VALID_VERDICTS:
            errors.append(f"linha {n}: verdict inválido '{v.get('verdict')}'")
            continue
        if not v.get("id"):
            errors.append(f"linha {n}: sem id")
            continue
        verdicts[v["id"]] = v

    if errors:
        print(f"  ⚠ {len(errors)} linhas inválidas:")
        for e in errors[:10]:
            print(f"    - {e}")

    # Mesclar nos checkpoints existentes (verified e, se houver, final)
    touched, missing = 0, []
    for name in ("refs-verified.json", "refs-final.json", "refs-dedup.json"):
        path = pipeline_dir / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for d in data.get("references", []):
            v = verdicts.get(d.get("id"))
            if v:
                d["llm_verdict"] = v["verdict"]
                d["llm_reason"] = v.get("reason", "")
                d["llm_criteria"] = v.get("criteria", [])
                if name == "refs-verified.json":
                    touched += 1
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Cobertura e concordância no checkpoint completo
    checkpoint, refs = _load_checkpoint(pipeline_dir)
    active = [r for r in refs if not r.is_duplicate]
    without_verdict = [r for r in active if r.id not in verdicts]

    kappa, agreement, divergences = _compute_agreement(active)

    _write_screening_report(
        pipeline_dir, active, verdicts, without_verdict,
        kappa, agreement, divergences, errors,
    )

    print(f"  {touched} refs receberam veredicto LLM (de {len(active)} ativas)")
    if without_verdict:
        print(f"  ⚠ {len(without_verdict)} refs SEM veredicto — cobertura incompleta")
    if kappa is not None:
        print(f"  Concordância keyword × LLM: {agreement:.0%} (kappa de Cohen = {kappa:.2f})")
    print(f"  {len(divergences)} divergências para revisão humana → {REPORT_FILE}")
    print(f"  Próximo passo: python -m tools run \"{project_name}\" --from-stage 5")
    return {
        "imported": touched,
        "missing": len(without_verdict),
        "kappa": kappa,
        "divergences": len(divergences),
    }


def _keyword_binary(relevance: Relevance) -> str:
    """Reduz a triagem por keywords a include/exclude (TANGENTIAL = include)."""
    return "exclude" if relevance == Relevance.OFF_TOPIC else "include"


def _llm_binary(verdict: str) -> str | None:
    """Reduz o veredicto LLM a binário; maybe fica de fora do kappa."""
    if verdict in ("include", "exclude"):
        return verdict
    return None


def _compute_agreement(
    refs: list[Reference],
) -> tuple[float | None, float, list[Reference]]:
    """Kappa de Cohen entre triagem por keywords e triagem LLM.

    Returns:
        (kappa, concordância observada, lista de refs divergentes).
    """
    pairs = []
    divergences = []
    for r in refs:
        if not r.llm_verdict:
            continue
        llm = _llm_binary(r.llm_verdict)
        kw = _keyword_binary(r.relevance)
        if llm is None:
            continue  # maybe não entra no kappa, mas vai para revisão humana
        pairs.append((kw, llm))
        if kw != llm:
            divergences.append(r)

    # 'maybe' sempre é divergência a revisar
    divergences.extend(
        r for r in refs if r.llm_verdict == "maybe" and r not in divergences
    )

    if len(pairs) < 2:
        return None, 0.0, divergences

    n = len(pairs)
    observed = sum(1 for kw, llm in pairs if kw == llm) / n
    p_kw_inc = sum(1 for kw, _ in pairs if kw == "include") / n
    p_llm_inc = sum(1 for _, llm in pairs if llm == "include") / n
    expected = p_kw_inc * p_llm_inc + (1 - p_kw_inc) * (1 - p_llm_inc)
    if expected == 1.0:
        return 1.0, observed, divergences
    kappa = (observed - expected) / (1 - expected)
    return kappa, observed, divergences


def _write_screening_report(
    pipeline_dir: Path,
    active: list[Reference],
    verdicts: dict,
    without_verdict: list[Reference],
    kappa: float | None,
    agreement: float,
    divergences: list[Reference],
    errors: list[str],
) -> None:
    """Gera screening-report.md — divergências para o revisor humano."""
    lines = [
        "# Relatório de Triagem Semântica (dupla triagem)",
        "",
        f"**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Refs ativas:** {len(active)} | **Com veredicto LLM:** "
        f"{len(active) - len(without_verdict)} | **Sem veredicto:** {len(without_verdict)}",
    ]
    if kappa is not None:
        interp = (
            "quase perfeita" if kappa > 0.8 else
            "substancial" if kappa > 0.6 else
            "moderada" if kappa > 0.4 else
            "razoável" if kappa > 0.2 else "fraca"
        )
        lines.append(
            f"**Concordância keyword × LLM:** {agreement:.0%} — "
            f"kappa de Cohen = {kappa:.2f} ({interp}, escala Landis & Koch)"
        )
    lines += [
        "",
        "> Divergências NÃO são descartadas automaticamente (zero-trust). "
        "O revisor humano decide cada uma; a etapa 6 do pipeline mantém a "
        "ref viva e marcada até lá.",
        "",
    ]

    if divergences:
        lines += [
            "## ⚠ Divergências para decisão humana",
            "",
            "| Ref | Título | Keywords | LLM | Razão do LLM |",
            "|---|---|---|---|---|",
        ]
        for r in divergences:
            lines.append(
                f"| {r.id} | {r.title[:70]} | {_keyword_binary(r.relevance)} "
                f"({r.relevance.name}) | {r.llm_verdict} | "
                f"{(r.llm_reason or '')[:100]} |"
            )
        lines.append("")

    if without_verdict:
        lines += [
            "## ⚠ Sem veredicto LLM (cobertura incompleta)",
            "",
        ]
        for r in without_verdict[:30]:
            lines.append(f"- {r.id}: {r.title[:80]}")
        lines.append("")

    if errors:
        lines += ["## Linhas inválidas no verdicts.jsonl", ""]
        lines += [f"- {e}" for e in errors]
        lines.append("")

    (pipeline_dir / REPORT_FILE).write_text("\n".join(lines), encoding="utf-8")
