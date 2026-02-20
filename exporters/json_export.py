"""Exportação JSON — serializa referências para arquivos intermediários."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import Reference


def save_refs_json(
    refs: list[Reference],
    output_path: Path,
    metadata: dict | None = None,
) -> None:
    """Salva lista de referências em JSON.

    Args:
        refs: Lista de referências.
        output_path: Caminho do arquivo de saída.
        metadata: Metadados adicionais para incluir no JSON.
    """
    data = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_refs": len(refs),
            **(metadata or {}),
        },
        "references": [ref.to_dict() for ref in refs],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_refs_json(input_path: Path) -> list[Reference]:
    """Carrega lista de referências de JSON.

    Args:
        input_path: Caminho do arquivo JSON.

    Returns:
        Lista de Reference.
    """
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    refs_data = data.get("references", [])
    return [Reference.from_dict(r) for r in refs_data]
