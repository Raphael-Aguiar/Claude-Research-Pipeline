"""Testes da fundação do subsistema de periódicos (F0) — sem rede."""

from pathlib import Path

import pytest

from tools.journals import db as jdb
from tools.journals.qualis import ingerir_qualis
from tools.journals.scimago import ingerir_scimago
from tools.journals.seed_vault import parse_lista_markdown

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = jdb.connect(":memory:")
    jdb.init_db(c)
    yield c
    c.close()


class TestNormalizarIssn:
    def test_formato_canonico(self):
        assert jdb.normalizar_issn("1413-8123") == "1413-8123"

    def test_sem_hifen(self):
        assert jdb.normalizar_issn("14138123") == "1413-8123"

    def test_digito_x_minusculo(self):
        assert jdb.normalizar_issn("9999-999x") == "9999-999X"

    def test_invalido_vira_none(self):
        assert jdb.normalizar_issn("issn-invalido") is None
        assert jdb.normalizar_issn("") is None
        assert jdb.normalizar_issn(None) is None


class TestUpsertPeriodico:
    def test_insere_e_atualiza_sem_apagar(self, conn):
        jdb.upsert_periodico(conn, "1413-8123", titulo="C&SC", apc_usd=0.0)
        jdb.upsert_periodico(conn, "1413-8123", h_index=80, apc_usd=None)
        row = jdb.get_periodico(conn, "1413-8123")
        assert row["titulo"] == "C&SC"
        assert row["h_index"] == 80
        # None não sobrescreve valor existente
        assert row["apc_usd"] == 0.0

    def test_busca_por_issn_alternativo(self, conn):
        jdb.upsert_periodico(
            conn, "1386-5056", titulo="IJMI",
            issns='["1386-5056", "1872-8243"]',
        )
        row = jdb.get_periodico(conn, "1872-8243")
        assert row is not None and row["issn_l"] == "1386-5056"

    def test_issn_invalido_falha(self, conn):
        with pytest.raises(ValueError):
            jdb.upsert_periodico(conn, "abc", titulo="X")


class TestIngestQualis:
    def test_ingere_consolidando_por_issn(self, conn):
        stats = ingerir_qualis(conn, FIXTURES / "sample_qualis.csv")
        # 4 ISSNs válidos com estrato válido (1413-8123 repetido consolida)
        assert stats["issns"] == 4
        assert stats["invalidos"] == 2  # issn quebrado + estrato Z9
        assert stats["conflitos_estrato"] == 0

    def test_areas_preservadas(self, conn):
        ingerir_qualis(conn, FIXTURES / "sample_qualis.csv")
        row = jdb.qualis_para_issns(conn, ["1413-8123"])
        assert row["estrato"] == "A1"
        assert "SAÚDE COLETIVA" in row["areas"]
        assert "MEDICINA II" in row["areas"]

    def test_issn_desconhecido_retorna_none(self, conn):
        ingerir_qualis(conn, FIXTURES / "sample_qualis.csv")
        assert jdb.qualis_para_issns(conn, ["0000-0001"]) is None


class TestIngestScimago:
    def test_ingere_multiplos_issns_por_linha(self, conn):
        stats = ingerir_scimago(conn, FIXTURES / "sample_scimago.csv", ano=2025)
        # IJMI tem 2 ISSNs → 2 registros; C&SC 1; sem-ISSN pulado
        assert stats["gravados"] == 3
        row = jdb.sjr_para_issns(conn, ["1872-8243"])
        assert row is not None
        assert row["quartil"] == "Q1"
        assert abs(row["sjr"] - 1.477) < 1e-9  # decimal com vírgula parseado

    def test_mais_recente_vence(self, conn):
        ingerir_scimago(conn, FIXTURES / "sample_scimago.csv", ano=2024)
        ingerir_scimago(conn, FIXTURES / "sample_scimago.csv", ano=2025)
        row = jdb.sjr_para_issns(conn, ["1413-8123"])
        assert row["ano"] == 2025


class TestParseNota:
    def test_parse_estrutura_completa(self):
        entradas = parse_lista_markdown(FIXTURES / "sample_nota_revistas.md")
        assert len(entradas) == 2
        ijmi, rpsp = entradas
        assert ijmi["titulo"] == "International Journal of Medical Informatics"
        assert ijmi["estrato_origem"] == "A1"
        assert ijmi["apc_texto"] == "US$ 3.150"
        assert ijmi["url"].startswith("https://www.sciencedirect.com/")
        assert "informática médica" in ijmi["escopo_texto"]
        assert rpsp["estrato_origem"] == "A4"
        assert rpsp["apc_texto"] == "zero"

    def test_secao_notas_ignorada(self):
        entradas = parse_lista_markdown(FIXTURES / "sample_nota_revistas.md")
        titulos = [e["titulo"] for e in entradas]
        assert all("seção" not in t.lower() for t in titulos)


class TestCampoVencido:
    def test_nunca_verificado_esta_vencido(self, conn):
        jdb.upsert_periodico(conn, "1413-8123", titulo="X")
        row = jdb.get_periodico(conn, "1413-8123")
        assert jdb.campo_vencido(row, "doaj_verificado_em", 180) is True

    def test_recente_nao_vencido(self, conn):
        jdb.upsert_periodico(
            conn, "1413-8123", titulo="X", doaj_verificado_em=jdb.hoje_iso()
        )
        row = jdb.get_periodico(conn, "1413-8123")
        assert jdb.campo_vencido(row, "doaj_verificado_em", 180) is False
