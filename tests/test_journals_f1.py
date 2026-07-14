"""Testes F1 — enriquecimento cache-first, sem rede (clientes monkeypatched)."""

import json

import pytest

from tools.journals import db as jdb
from tools.journals import enrich
from tools.journals.apis import doaj as doaj_mod


@pytest.fixture
def conn():
    c = jdb.connect(":memory:")
    jdb.init_db(c)
    yield c
    c.close()


SRC_OPENALEX = {
    "openalex_id": "https://openalex.org/S123",
    "issn_l": "1471-2458",
    "issns": ["1471-2458"],
    "titulo": "BMC Public Health",
    "editora": "BioMed Central",
    "pais": "GB",
    "homepage_url": "https://bmcpublichealth.biomedcentral.com",
    "is_oa": True,
    "in_doaj": True,
    "apc_usd": 3450,
    "citedness_2yr": 4.2,
    "h_index": 200,
    "works_count": 30000,
    "topicos": ["Public Health", "Epidemiology"],
}

DJ_DOAJ = {
    "titulo": "BMC Public Health",
    "editora": "BMC",
    "tem_apc": True,
    "apc_valor": 3450,
    "apc_moeda": "USD",
    "apc_url": "https://example.com/fees",
    "apc_waiver": True,
    "licenca": "CC BY",
    "peer_review": "Open peer review",
}


def _patch_clientes(monkeypatch, contagem):
    def openalex(issn, email=""):
        contagem["openalex"] += 1
        return dict(SRC_OPENALEX)

    def doaj_busca(issn):
        contagem["doaj"] += 1
        return dict(DJ_DOAJ)

    def medline(issn, email="", api_key=""):
        contagem["nlm"] += 1
        return True

    monkeypatch.setattr(
        enrich.openalex_sources, "buscar_source_por_issn", openalex
    )
    monkeypatch.setattr(enrich.doaj, "buscar_journal_por_issn", doaj_busca)
    monkeypatch.setattr(enrich.nlm_catalog, "medline_indexado", medline)
    monkeypatch.setattr(enrich, "PAUSA_ENTRE_CHAMADAS", 0)


def test_enriquece_e_grava_proveniencia(conn, monkeypatch):
    contagem = {"openalex": 0, "doaj": 0, "nlm": 0}
    _patch_clientes(monkeypatch, contagem)
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador())
    row = jdb.get_periodico(conn, "1471-2458")
    assert row["titulo"] == "BMC Public Health"
    assert row["apc_valor"] == 3450 and row["apc_moeda"] == "USD"
    assert "DOAJ API v4" in row["apc_fonte"]
    assert "https://example.com/fees" in row["apc_fonte"]
    assert row["apc_verificado_em"] == jdb.hoje_iso()
    assert row["medline_indexado"] == 1
    assert row["licenca"] == "CC BY"
    assert json.loads(row["topicos"]) == ["Public Health", "Epidemiology"]


def test_segunda_execucao_zero_chamadas(conn, monkeypatch):
    contagem = {"openalex": 0, "doaj": 0, "nlm": 0}
    _patch_clientes(monkeypatch, contagem)
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador())
    antes = dict(contagem)
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador())
    assert contagem == antes  # cache fresco → nenhum cliente chamado


def test_refresh_forca_reconsulta(conn, monkeypatch):
    contagem = {"openalex": 0, "doaj": 0, "nlm": 0}
    _patch_clientes(monkeypatch, contagem)
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador())
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador(), refresh=True)
    assert contagem["openalex"] == 2
    assert contagem["doaj"] == 2


def test_sem_apc_vira_zero_com_fonte(conn, monkeypatch):
    contagem = {"openalex": 0, "doaj": 0, "nlm": 0}
    _patch_clientes(monkeypatch, contagem)
    dj_gratuito = dict(DJ_DOAJ, tem_apc=False, apc_valor=None, apc_moeda=None)
    monkeypatch.setattr(
        enrich.doaj, "buscar_journal_por_issn", lambda issn: dj_gratuito
    )
    enrich.enriquecer_um(conn, "1471-2458", {}, enrich.Contador())
    row = jdb.get_periodico(conn, "1471-2458")
    assert row["apc_valor"] == 0.0 and row["apc_usd"] == 0.0
    assert "sem APC" in row["apc_fonte"]


def test_doaj_normalizacao_prefere_usd():
    resposta = {
        "results": [{
            "bibjson": {
                "title": "X",
                "publisher": {"name": "P"},
                "apc": {
                    "has_apc": True,
                    "max": [
                        {"price": 3150, "currency": "EUR"},
                        {"price": 3450, "currency": "USD"},
                    ],
                    "url": "https://fees",
                },
                "waiver": {"has_waiver": True},
                "license": [{"type": "CC BY"}],
                "editorial": {"review_process": ["Blind peer review"]},
            }
        }]
    }

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return resposta

    import tools.journals.apis.doaj as d

    original = d.requests.get
    d.requests.get = lambda *a, **k: FakeResp()
    try:
        r = d.buscar_journal_por_issn("1111-1111")
    finally:
        d.requests.get = original
    assert r["apc_valor"] == 3450 and r["apc_moeda"] == "USD"
    assert r["apc_url"] == "https://fees"
    assert r["peer_review"] == "Blind peer review"
