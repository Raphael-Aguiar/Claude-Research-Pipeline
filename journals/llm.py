"""Backend LLM opcional para extração local em massa (Ollama).

O caminho padrão de extração é o fan-out de subagentes baratos
(Haiku/Sonnet) orquestrado pela skill periodicos-alvo via Claude Code —
zero infraestrutura. Este módulo existe só para re-varreduras em massa
a custo zero na RTX 4080: JOURNALS_LLM_BACKEND=ollama.

A saída passa pela MESMA validação de import_extraction.py — o backend
não muda a barreira anti-alucinação.
"""

from __future__ import annotations

import json
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELO_DEFAULT = os.environ.get("JOURNALS_OLLAMA_MODEL", "qwen2.5:14b")

PROMPT_EXTRACAO = """Você extrai dados editoriais de páginas de periódicos científicos.
Leia o texto abaixo (snapshot da página do periódico {titulo}) e responda
APENAS com JSON válido no formato:

{{"tempo_1a_decisao_dias": <int ou null>,
  "tempo_trecho": "<citação literal do texto que sustenta o valor, ou null>",
  "taxa_aceite_pct": <float ou null>,
  "taxa_trecho": "<citação literal ou null>",
  "tipos_artigo": [<strings>] ou null,
  "tipos_trecho": "<citação literal ou null>"}}

REGRAS INVIOLÁVEIS:
- Se o dado NÃO estiver explícito no texto, use null. NUNCA estime.
- Cada valor não-nulo exige trecho LITERAL copiado do texto (será
  validado por substring — paráfrase é rejeitada).
- Converta unidades para as pedidas (semanas→dias ×7; "median 8 days"→8).

TEXTO:
{texto}
"""


def extrair_com_ollama(
    texto: str, titulo: str, modelo: str = MODELO_DEFAULT
) -> dict | None:
    """Extrai campos editoriais de um snapshot via Ollama local."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": modelo,
            "prompt": PROMPT_EXTRACAO.format(titulo=titulo, texto=texto[:24000]),
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=300,
    )
    resp.raise_for_status()
    try:
        return json.loads(resp.json()["response"])
    except (KeyError, json.JSONDecodeError):
        return None
