"""Envio e recebimento da pasta de trabalho de um projeto de escrita ao Drive.

Decisão de Raphael (19/11/2026): os arquivos de trabalho ficam numa pasta local do agente
(config.ESCRITA_DIR/<Projeto>) e ao fim de cada rodada vão para a subpasta
"Material de trabalho" do Drive do projeto. O ID da pasta vem de projetos_drive.json.
Usa o rclone (remoto `drive:`), sem apagar nada no destino: só copia o que é novo ou mudou.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import ESCRITA_DIR

MAPA = Path(__file__).resolve().parent / "projetos_drive.json"


def pasta_drive(projeto: str) -> str:
    mapa = json.loads(MAPA.read_text(encoding="utf-8"))
    if projeto not in mapa:
        raise SystemExit(
            f"Projeto '{projeto}' sem pasta 'Material de trabalho' em {MAPA}. "
            "Ache ou crie a pasta no Drive do projeto e acrescente o ID lá."
        )
    return mapa[projeto]


def _rclone(origem: str, destino: str, pasta_id: str) -> int:
    cmd = ["rclone", "copy", "--tpslimit", "2", "--drive-root-folder-id", pasta_id,
           "--exclude", "__pycache__/**", origem, destino]
    return subprocess.run(cmd).returncode


def enviar(projeto: str) -> int:
    """Copia a pasta de trabalho local para o Drive (nada é apagado lá)."""
    local = ESCRITA_DIR / projeto
    if not local.exists():
        raise SystemExit(f"Pasta de trabalho inexistente: {local}")
    rc = _rclone(str(local), "drive:", pasta_drive(projeto))
    print(("enviado: " if rc == 0 else "FALHOU: ") + f"{local} -> Drive (Material de trabalho de {projeto})")
    return rc


def receber(projeto: str) -> int:
    """Traz do Drive o material de trabalho do projeto para a pasta local."""
    local = ESCRITA_DIR / projeto
    local.mkdir(parents=True, exist_ok=True)
    pid = pasta_drive(projeto)
    cmd = ["rclone", "copy", "--tpslimit", "2", "--drive-root-folder-id", pid, "drive:", str(local)]
    rc = subprocess.run(cmd).returncode
    print(("recebido: " if rc == 0 else "FALHOU: ") + f"Drive (Material de trabalho de {projeto}) -> {local}")
    return rc
