"""Etapa 1 — Definição de Escopo.

Parser de scope.yaml → SearchConfig.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import (
    TEMPLATES_DIR,
    get_project_dir,
    get_scope_path,
    load_scope,
)
from ..models import SearchConfig


def init_scope(project_name: str, modality: str = "pesquisa-base") -> Path:
    """Cria scope.yaml a partir do template, se não existir.

    Returns:
        Path do scope.yaml criado/existente.
    """
    scope_path = get_scope_path(project_name)
    project_dir = get_project_dir(project_name)

    if scope_path.exists():
        print(f"scope.yaml já existe em {scope_path}")
        return scope_path

    # Garantir que o diretório do projeto existe
    project_dir.mkdir(parents=True, exist_ok=True)

    # Copiar template
    template = TEMPLATES_DIR / "scope-template.yaml"
    if template.exists():
        shutil.copy(template, scope_path)
        # Substituir placeholders
        content = scope_path.read_text(encoding="utf-8")
        content = content.replace("{{PROJECT_NAME}}", project_name)
        content = content.replace("{{MODALITY}}", modality)
        scope_path.write_text(content, encoding="utf-8")
    else:
        # Template mínimo inline
        content = f"""# scope.yaml — {project_name}
# Gerado automaticamente. Editar conforme necessidade.

project_name: "{project_name}"
modality: {modality}

research_question: ""

keyword_blocks:
  - concept: "conceito principal"
    terms:
      - "termo1"
      - "termo2"
  - concept: "domínio"
    terms:
      - "termo3"
      - "termo4"

mesh_terms: []

inclusion_criteria:
  - "artigos em inglês ou português"
  - "publicados entre 2020 e 2026"

exclusion_criteria:
  - "artigos fora do escopo"

year_range: [2020, 2026]
languages: ["en", "pt"]

apis: ["pubmed", "openalex"]
max_results_per_api: 100

relevance_keywords:
  direct: []
  tangential: []
  off_topic: []
"""
        scope_path.write_text(content, encoding="utf-8")

    print(f"scope.yaml criado em {scope_path}")
    print("Edite o arquivo antes de executar o pipeline.")
    return scope_path


def parse_scope(project_name: str) -> SearchConfig:
    """Carrega e valida scope.yaml.

    Returns:
        SearchConfig pronto para uso nas etapas seguintes.
    """
    config = load_scope(project_name)

    # Validações
    if not config.keyword_blocks:
        print("AVISO: Nenhum keyword_block definido no scope.yaml.")
    if not config.research_question:
        print("AVISO: research_question vazia no scope.yaml.")

    return config
