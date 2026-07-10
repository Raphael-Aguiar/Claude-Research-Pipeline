"""Configuração do pipeline — carrega scope.yaml, API keys e paths."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import Modality, ResearchAxis, SearchConfig

# Diretórios base
# Textos/projetos vivem no vault (~/PKM/Escrita); o tooling vive onde este
# pacote está instalado (~/bin/escrita-tooling) — modelo híbrido de 2026-05-28.
ESCRITA_DIR = Path.home() / "PKM" / "Escrita"
TOOLS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = TOOLS_DIR / "templates"

# HTTP defaults
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
DEFAULT_TIMEOUT = 15


def get_project_dir(project_name: str) -> Path:
    """Retorna o diretório do projeto."""
    return ESCRITA_DIR / project_name


def get_pipeline_dir(project_name: str, create: bool = True) -> Path:
    """Retorna o diretório de outputs do pipeline.

    Só cria o diretório se o projeto já existe — comandos read-only
    (status) não devem materializar pastas para nomes errados.
    """
    project_dir = get_project_dir(project_name)
    pipeline_dir = project_dir / "pipeline"
    if create and project_dir.exists():
        pipeline_dir.mkdir(exist_ok=True)
    return pipeline_dir


def get_scope_path(project_name: str) -> Path:
    """Retorna o caminho do scope.yaml do projeto."""
    return get_project_dir(project_name) / "scope.yaml"


def load_env() -> dict[str, str]:
    """Carrega variáveis de ambiente do .env se existir."""
    env_path = TOOLS_DIR / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


def get_api_config() -> dict[str, str]:
    """Retorna configuração de API keys (env vars > .env file)."""
    file_env = load_env()
    keys = [
        "NCBI_EMAIL", "NCBI_API_KEY",
        "OPENALEX_EMAIL",
        "CROSSREF_EMAIL",
        "UNPAYWALL_EMAIL",
        "SEMANTIC_SCHOLAR_API_KEY",
        "BVS_API_KEY",
    ]
    config = {}
    for key in keys:
        config[key] = os.environ.get(key) or file_env.get(key, "")
    return config


def load_scope(project_name: str) -> SearchConfig:
    """Carrega scope.yaml do projeto e retorna SearchConfig."""
    scope_path = get_scope_path(project_name)
    if not scope_path.exists():
        raise FileNotFoundError(
            f"scope.yaml não encontrado em {scope_path}. "
            f"Use 'python -m tools scope \"{project_name}\"' para criar."
        )

    with open(scope_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Carregar defaults da modalidade
    modality_str = data.get("modality", "pesquisa-base")
    defaults = _load_modality_defaults(modality_str)

    config = SearchConfig()
    config.project_name = project_name
    config.modality = Modality(modality_str)
    config.research_question = data.get("research_question", "")

    # Keyword blocks
    config.keyword_blocks = data.get("keyword_blocks", [])
    config.mesh_terms = data.get("mesh_terms", [])

    # Critérios
    config.inclusion_criteria = data.get("inclusion_criteria", [])
    config.exclusion_criteria = data.get("exclusion_criteria", [])

    # Período
    year_range = data.get("year_range", defaults.get("year_range", [2020, 2026]))
    config.year_range = (year_range[0], year_range[1])

    # Idiomas
    config.languages = data.get("languages", defaults.get("languages", ["en", "pt"]))

    # APIs
    config.apis = data.get("apis", defaults.get("apis", ["pubmed", "openalex"]))

    # Max results
    config.max_results_per_api = data.get(
        "max_results_per_api",
        defaults.get("max_results_per_api", 100),
    )

    # Max final refs
    config.max_final_refs = data.get(
        "max_final_refs",
        defaults.get("max_final_refs", 40),
    )

    # DeCS terms
    config.decs_terms = data.get("decs_terms", [])

    # Exclusion keywords
    config.exclusion_keywords = data.get("exclusion_keywords", [])

    # Keywords de relevância
    relevance = data.get("relevance_keywords", {})
    config.relevance_keywords_direct = relevance.get("direct", [])
    config.relevance_keywords_tangential = relevance.get("tangential", [])
    config.relevance_keywords_off_topic = relevance.get("off_topic", [])

    # Research axes
    raw_axes = data.get("research_axes", [])
    for axis_data in raw_axes:
        axis = ResearchAxis(
            name=axis_data.get("name", ""),
            keywords=axis_data.get("keywords", []),
            synonyms=axis_data.get("synonyms", []),
        )
        config.research_axes.append(axis)

    # Quality framework (scope > default da modalidade)
    config.quality_framework = data.get(
        "quality_framework", defaults.get("quality_framework")
    )

    # Áreas do Semantic Scholar (lista vazia = sem filtro)
    if "fields_of_study" in data:
        config.fields_of_study = data["fields_of_study"] or []

    return config


def _load_modality_defaults(modality: str) -> dict:
    """Carrega defaults para a modalidade do modality-defaults.yaml."""
    defaults_path = TEMPLATES_DIR / "modality-defaults.yaml"
    if not defaults_path.exists():
        return {}
    with open(defaults_path, encoding="utf-8") as f:
        all_defaults = yaml.safe_load(f) or {}
    return all_defaults.get(modality, {})
