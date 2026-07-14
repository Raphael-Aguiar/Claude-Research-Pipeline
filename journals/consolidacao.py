"""Motor de consolidação — critério VIGENTE da Área 22 (ciclo 2025-2028).

A CAPES descontinuou o Qualis Periódicos. Para artigos publicados de 2025
em diante, o Documento de Área 2025-2028 da Saúde Coletiva define
"periódico consolidado" por duas portas alternativas:

  Porta A (bibliométrica): indexado em Scopus e/ou Web of Science com
    indicador de impacto ≥ percentil 50 em PELO MENOS UMA categoria de
    indexação relacionada à Saúde Coletiva.
  Porta B (SciELO): periódico da coleção SciELO Saúde Pública com índice
    h5 (Google Scholar) acima do percentil 60 da área.

Atalho: Q1 = percentil 75-100, Q2 = 50-75 → Q1/Q2 em categoria de saúde
cumpre a Porta A; Q3/Q4 não.

DOIS CAVEATS DE HONESTIDADE, sempre explicitados no relatório:
1. PROXY: o critério oficial é CiteScore (Scopus) ou JIF (JCR); usamos o
   quartil SJR (Scimago, também derivado da Scopus) como proxy. São
   correlacionados, não idênticos — verdict positivo = "provável, confirmar
   no Scopus"; e um verdict NEGATIVO pela Porta A não é definitivo (o
   periódico ainda pode cumprir via CiteScore/JIF real ou via WoS, que não
   temos).
2. CATEGORIA CURADA: HEALTH_CATEGORIES abaixo operacionaliza "categoria
   relacionada à Saúde Coletiva"; a categoria que casou é sempre mostrada.
"""

from __future__ import annotations

import json
import sqlite3

from .db import (categorias_para_issns, em_scielo_sp, hoje_iso,
                 get_periodico, upsert_periodico)

# Categorias Scopus/Scimago tratadas como "relacionadas à Saúde Coletiva"
# (interpretação curada — o Documento de Área lista como exemplos Public
# Health, Epidemiology, Health Policy, Health Informatics, Health Care
# Sciences & Services e Medicine). Inclui o guarda-chuva de medicina,
# enfermagem e saúde pública; EXCLUI, deliberadamente, categorias de TI
# pura (Information Systems, Library and Information Sciences), gestão
# (Management Information Systems) e Administração Pública — não são
# categorias de saúde, ainda que periódicos de saúde coletiva às vezes
# apareçam nelas. A categoria que casa é sempre mostrada para auditoria.
HEALTH_CATEGORIES = {
    # Saúde pública / coletiva — núcleo
    "Public Health, Environmental and Occupational Health",
    "Epidemiology",
    "Health Policy",
    "Health Informatics",
    "Health Information Management",
    "Health (social science)",
    "Health Professions (miscellaneous)",
    "Health, Toxicology and Mutagenesis",
    "Community and Home Care",
    "Chemical Health and Safety",
    # Infecto / microbiologia médica
    "Infectious Diseases",
    "Microbiology (medical)",
    # Medicina (guarda-chuva e especialidades)
    "Medicine (miscellaneous)",
    "Internal Medicine",
    "Emergency Medicine",
    "Emergency Medical Services",
    "Critical Care and Intensive Care Medicine",
    "Complementary and Alternative Medicine",
    "Family Practice",
    "Primary Care",
    "Anesthesiology and Pain Medicine",
    "Cardiology and Cardiovascular Medicine",
    "Pulmonary and Respiratory Medicine",
    "Reproductive Medicine",
    "Physiology (medical)",
    "Pathology and Forensic Medicine",
    "Molecular Medicine",
    "Radiology, Nuclear Medicine and Imaging",
    "Orthopedics and Sports Medicine",
    "Pediatrics, Perinatology and Child Health",
    "Psychiatry and Mental Health",
    "Pharmacology (medical)",
    "Biochemistry (medical)",
    "Medical Laboratory Technology",
    "Biomedical Engineering",
    "Reviews and References (medical)",
    "Geriatrics and Gerontology",
    "Obstetrics and Gynecology",
    "Dermatology",
    "Endocrinology, Diabetes and Metabolism",
    "Gastroenterology",
    "Nephrology",
    "Neurology (clinical)",
    "Oncology",
    "Ophthalmology",
    "Otorhinolaryngology",
    "Rehabilitation",
    "Surgery",
    "Urology",
    "Immunology and Allergy",
    "Hematology",
    "Rheumatology",
    "Transplantation",
    "Public Health",  # rótulo curto que aparece em algumas edições
    # Enfermagem
    "Nursing (miscellaneous)",
    "Advanced and Specialized Nursing",
    "Critical Care Nursing",
    "Emergency Nursing",
    "Medical and Surgical Nursing",
    "Oncology (nursing)",
    "Pharmacology (nursing)",
    "Nurse Assisting",
    "Gerontology",
    "Maternity and Midwifery",
    "Pediatrics",
    "Issues, Ethics and Legal Aspects",
    "LPN and LVN",
    "Fundamentals and Skills",
    "Assessment and Diagnosis",
    "Care Planning",
    "Leadership and Management",
    "Research and Theory",
    "Review and Exam Preparation",
    # Odontologia / farmácia / nutrição
    "Dentistry (miscellaneous)",
    "Dental Hygiene",
    "Pharmaceutical Science",
    "Pharmacy",
    "Nutrition and Dietetics",
    "Food Science",
    "Speech and Hearing",
}

_ORDEM_Q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}


def avaliar(conn: sqlite3.Connection, issns: list[str]) -> dict:
    """Calcula o verdict de consolidação Área 22 para um conjunto de ISSNs.

    Retorna dict com porta_a_status, evidências e consolidado (bool).
    Não grava — quem persiste é `aplicar()`.
    """
    categorias = categorias_para_issns(conn, issns)
    health = [
        {"categoria": r["categoria"], "quartil": r["quartil"]}
        for r in categorias
        if r["categoria"] in HEALTH_CATEGORIES
    ]
    health.sort(key=lambda c: _ORDEM_Q.get(c["quartil"], 9))

    if not categorias:
        status = "sem_dado"            # nem no Scimago (sem proxy disponível)
        melhor = None
    elif not health:
        status = "sem_categoria_saude"  # a armadilha (só Educação/CS/etc.)
        melhor = None
    else:
        melhor = health[0]
        if melhor["quartil"] in ("Q1", "Q2"):
            status = "consolidado"       # cumpre Porta A (proxy SJR)
        else:
            status = "nao_por_scimago"   # saúde só em Q3/Q4 pelo SJR

    sp = em_scielo_sp(conn, issns)
    porta_b = sp is not None

    return {
        "porta_a_status": status,
        "porta_a_categoria": melhor["categoria"] if melhor else None,
        "porta_a_quartil": melhor["quartil"] if melhor else None,
        "porta_a_categorias": health,
        "porta_b_scielo_sp": porta_b,
        "porta_b_titulo": sp["titulo"] if sp else None,
        "consolidado": (status == "consolidado") or porta_b,
    }


def aplicar(conn: sqlite3.Connection, issn_l: str, issns: list[str]) -> dict:
    """Avalia e grava o verdict de consolidação no periódico."""
    v = avaliar(conn, issns)
    fonte = (
        "Área 22 (Doc. Área 2025-2028): Porta A = Scimago SJR 2025 "
        "(proxy de CiteScore/Scopus) por categoria; Porta B = coleção "
        "SciELO Saúde Pública (articlemeta)"
    )
    upsert_periodico(
        conn, issn_l,
        porta_a_status=v["porta_a_status"],
        porta_a_categoria=v["porta_a_categoria"],
        porta_a_quartil=v["porta_a_quartil"],
        porta_a_categorias=json.dumps(v["porta_a_categorias"], ensure_ascii=False),
        porta_b_scielo_sp=1 if v["porta_b_scielo_sp"] else 0,
        consolidado=1 if v["consolidado"] else 0,
        consolidacao_fonte=fonte,
        consolidacao_verificado_em=hoje_iso(),
    )
    return v


def rotulo_status(status: str) -> str:
    """Frase curta e honesta para cada status de Porta A."""
    return {
        "consolidado": "provável consolidado (Porta A) — confirmar CiteScore no Scopus",
        "nao_por_scimago": "categorias de saúde só em Q3/Q4 pelo SJR — "
                           "pode cumprir via CiteScore/JIF real; verificar",
        "sem_categoria_saude": "não cumpre Porta A: sem categoria de saúde no "
                               "Scopus (armadilha Educação/Computação pura)",
        "sem_dado": "sem dado de categoria no Scimago — verificar manualmente",
    }.get(status, status)
