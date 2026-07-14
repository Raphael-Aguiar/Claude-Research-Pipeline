"""Testes F2 — ranqueamento por consolidação Área 22 (determinístico, sem rede)."""

import json
from collections import Counter

import pytest

from tools.journals import db as jdb
from tools.journals.consolidacao import aplicar
from tools.journals.rank import ranquear, _tokens


def _seed_categoria(conn, issn, categoria, quartil, ano=2025):
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
    # A1 caro, consolidado Porta A Q1 (Public Health)
    jdb.upsert_periodico(
        c, "1111-1111", titulo="Consolidado A1 Caro", apc_usd=4000.0,
        apc_valor=4000.0, apc_moeda="USD", is_oa=1, medline_indexado=1,
        citedness_2yr=5.0,
        topicos=json.dumps(["Public Health", "Epidemiology"]),
    )
    _seed_categoria(c, "1111-1111", "Public Health, Environmental and Occupational Health", "Q1")
    # gratuito, SciELO-SP (Porta B) + Public Health Q2
    jdb.upsert_periodico(
        c, "2222-2222", titulo="SciELO SP Gratuito", apc_valor=0.0,
        apc_usd=0.0, is_oa=1, medline_indexado=0, citedness_2yr=1.0,
        topicos=json.dumps(["Public Health"]),
    )
    _seed_categoria(c, "2222-2222", "Public Health, Environmental and Occupational Health", "Q2")
    c.execute("INSERT INTO scielo_sp (issn, titulo, fonte, ingerido_em) VALUES (?,?,?,?)",
              ("2222-2222", "SciELO SP Gratuito", "spa", "2026-07-14"))
    # armadilha: Educação pura, sem categoria de saúde
    jdb.upsert_periodico(c, "3333-3333", titulo="EdTech Pura", is_oa=1)
    _seed_categoria(c, "3333-3333", "Education", "Q1")
    _seed_categoria(c, "3333-3333", "Computer Science Applications", "Q1")
    # health só em Q3 (nao_por_scimago)
    jdb.upsert_periodico(c, "4444-4444", titulo="Saúde Q3", is_oa=1, citedness_2yr=2.0)
    _seed_categoria(c, "4444-4444", "Health Informatics", "Q3")
    for issn in ("1111-1111", "2222-2222", "3333-3333", "4444-4444"):
        row = jdb.get_periodico(c, issn)
        aplicar(c, issn, json.loads(row["issns"] or "[]") or [issn])
    yield c
    c.close()


CONSULTA = {"tema": "public health surveillance", "keywords_en": []}


def test_consolidado_pontua_mais_que_armadilha(conn):
    r = ranquear(conn, ["1111-1111", "3333-3333"], Counter(), set(), CONSULTA)
    por = {f["issn_l"]: f for f in r["finalistas"]}
    assert por["1111-1111"]["componentes"]["consolidacao"] == 1.0
    assert por["3333-3333"]["componentes"]["consolidacao"] == 0.10
    assert por["1111-1111"]["pre_score"] > por["3333-3333"]["pre_score"]


def test_armadilha_educacao_sinalizada_nao_excluida(conn):
    r = ranquear(conn, ["3333-3333"], Counter(), set(), CONSULTA)
    assert len(r["finalistas"]) == 1  # NÃO é excluída
    f = r["finalistas"][0]
    assert f["porta_a_status"] == "sem_categoria_saude"
    assert f["consolidado"] == 0
    assert any("SEM categoria de saúde" in fl for fl in f["flags"])


def test_porta_b_scielo_consolida(conn):
    r = ranquear(conn, ["2222-2222"], Counter(), set(), CONSULTA)
    f = r["finalistas"][0]
    assert f["consolidado"] == 1
    assert f["porta_b_scielo_sp"] == 1
    assert f["componentes"]["consolidacao"] >= 0.85


def test_nao_por_scimago_borderline(conn):
    r = ranquear(conn, ["4444-4444"], Counter(), set(), CONSULTA)
    f = r["finalistas"][0]
    assert f["porta_a_status"] == "nao_por_scimago"
    assert f["consolidado"] == 0
    assert any("CiteScore/JIF" in fl for fl in f["flags"])


def test_apc_zero_maximiza_componente(conn):
    r = ranquear(conn, ["1111-1111", "2222-2222"], Counter(), set(), CONSULTA)
    por = {f["issn_l"]: f for f in r["finalistas"]}
    assert por["2222-2222"]["componentes"]["apc"] == 1.0
    assert por["1111-1111"]["componentes"]["apc"] < 0.3


def test_ordenacao_e_top(conn):
    r = ranquear(
        conn, ["1111-1111", "2222-2222", "3333-3333", "4444-4444"],
        Counter({"1111-1111": 5}), {"1111-1111"}, CONSULTA, top=2,
    )
    assert len(r["finalistas"]) == 2
    scores = [f["pre_score"] for f in r["finalistas"]]
    assert scores == sorted(scores, reverse=True)
    # consolidados (Porta A Q1 / Porta B) devem liderar sobre a armadilha
    assert r["finalistas"][0]["issn_l"] in ("1111-1111", "2222-2222")


def test_tokens_remove_stopwords():
    assert "the" not in _tokens("the public health of the world")
    assert "public" in _tokens("the public health")
