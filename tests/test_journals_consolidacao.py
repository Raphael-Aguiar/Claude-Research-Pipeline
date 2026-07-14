"""Testes do motor de consolidação Área 22 (Porta A/B), sem rede."""

import pytest

from tools.journals import db as jdb
from tools.journals.consolidacao import avaliar, HEALTH_CATEGORIES


def _cat(conn, issn, categoria, quartil, ano=2025):
    conn.execute(
        "INSERT OR REPLACE INTO scimago_categorias "
        "(issn, categoria, quartil, ano, ingerido_em) VALUES (?,?,?,?,?)",
        (issn, categoria, quartil, ano, "2026-07-14"),
    )
    conn.commit()


@pytest.fixture
def conn():
    c = jdb.connect(":memory:")
    jdb.init_db(c)
    yield c
    c.close()


def test_porta_a_q1_consolida(conn):
    _cat(conn, "1111-1111", "Epidemiology", "Q1")
    v = avaliar(conn, ["1111-1111"])
    assert v["porta_a_status"] == "consolidado"
    assert v["porta_a_categoria"] == "Epidemiology"
    assert v["porta_a_quartil"] == "Q1"
    assert v["consolidado"] is True


def test_melhor_quartil_de_saude_vence(conn):
    # tem Q3 em Public Health mas Q1 em Health Policy → consolidado por Q1
    _cat(conn, "2222-2222", "Public Health, Environmental and Occupational Health", "Q3")
    _cat(conn, "2222-2222", "Health Policy", "Q1")
    v = avaliar(conn, ["2222-2222"])
    assert v["porta_a_quartil"] == "Q1"
    assert v["porta_a_categoria"] == "Health Policy"


def test_educacao_pura_sem_categoria_saude(conn):
    _cat(conn, "3333-3333", "Education", "Q1")
    _cat(conn, "3333-3333", "Computer Science Applications", "Q1")
    v = avaliar(conn, ["3333-3333"])
    assert v["porta_a_status"] == "sem_categoria_saude"
    assert v["consolidado"] is False


def test_saude_so_q3_nao_por_scimago(conn):
    _cat(conn, "4444-4444", "Health Informatics", "Q3")
    v = avaliar(conn, ["4444-4444"])
    assert v["porta_a_status"] == "nao_por_scimago"
    assert v["porta_a_quartil"] == "Q3"
    assert v["consolidado"] is False


def test_sem_dado_quando_ausente_do_scimago(conn):
    v = avaliar(conn, ["9999-9999"])
    assert v["porta_a_status"] == "sem_dado"
    assert v["consolidado"] is False


def test_porta_b_scielo_consolida_mesmo_sem_porta_a(conn):
    # sem categoria de saúde, mas na coleção SciELO SP
    _cat(conn, "5555-5555", "Education", "Q1")
    conn.execute(
        "INSERT INTO scielo_sp (issn, titulo, fonte, ingerido_em) VALUES (?,?,?,?)",
        ("5555-5555", "Rev SP", "spa", "2026-07-14"),
    )
    conn.commit()
    v = avaliar(conn, ["5555-5555"])
    assert v["porta_b_scielo_sp"] is True
    assert v["consolidado"] is True  # Porta B resgata


def test_health_categories_contem_exemplos_do_documento(conn):
    for c in ["Public Health, Environmental and Occupational Health",
              "Epidemiology", "Health Policy", "Health Informatics"]:
        assert c in HEALTH_CATEGORIES
    # a armadilha não é categoria de saúde
    assert "Education" not in HEALTH_CATEGORIES
    assert "Computer Science Applications" not in HEALTH_CATEGORIES
