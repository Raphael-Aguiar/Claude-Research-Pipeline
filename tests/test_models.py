"""Testes para models.py — compute_grade e serialização."""

import pytest

from tools.models import (
    AccessStatus,
    CompositeGrade,
    Reference,
    Relevance,
    Tier,
)


def _verified(**kwargs) -> Reference:
    """Reference com identidade verificada (sem pendências zero-trust v3)."""
    defaults = dict(doi="10.1000/teste", doi_resolves=True)
    defaults.update(kwargs)
    return Reference(**defaults)


class TestComputeGrade:
    """Testa todos os cenários de compute_grade() (v2 + travas v3).

    Regras v2: acesso NÃO reduz grade.
    - GOLD = T1-T2 + DIRECT (independente de acesso)
    - SILVER = T3 + DIRECT, ou T1-T2 + TANGENTIAL
    - BRONZE = T3 + TANGENTIAL

    v3 (zero-trust): pendência de verificação limita a BRONZE.
    Os testes de grade usam _verified() para isolar as regras v2.
    """

    def test_retracted_is_discard(self):
        ref = Reference(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.ACCESSIBLE,
            retracted=True,
        )
        assert ref.compute_grade() == CompositeGrade.DISCARD

    def test_off_topic_is_discard(self):
        ref = Reference(
            tier=Tier.T1,
            relevance=Relevance.OFF_TOPIC,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.DISCARD

    def test_t4_is_discard(self):
        ref = Reference(
            tier=Tier.T4,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.DISCARD

    def test_broken_is_discard(self):
        ref = Reference(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.BROKEN,
        )
        assert ref.compute_grade() == CompositeGrade.DISCARD

    def test_t1_direct_accessible_is_gold(self):
        ref = _verified(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.GOLD

    def test_t2_direct_oa_is_gold(self):
        ref = _verified(
            tier=Tier.T2,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.OPEN_ACCESS,
        )
        assert ref.compute_grade() == CompositeGrade.GOLD

    def test_t1_direct_restricted_is_gold(self):
        """v2: acesso NÃO reduz grade — T1+DIRECT+restricted = GOLD."""
        ref = _verified(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.RESTRICTED,
        )
        assert ref.compute_grade() == CompositeGrade.GOLD

    def test_t1_direct_no_url_is_gold(self):
        """v2: acesso NÃO reduz grade — T1+DIRECT+no_url = GOLD."""
        ref = _verified(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.NO_URL,
        )
        assert ref.compute_grade() == CompositeGrade.GOLD

    def test_t1_tangential_is_silver(self):
        ref = _verified(
            tier=Tier.T1,
            relevance=Relevance.TANGENTIAL,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.SILVER

    def test_t3_direct_is_silver(self):
        ref = _verified(
            tier=Tier.T3,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.SILVER

    def test_t3_tangential_is_bronze(self):
        ref = _verified(
            tier=Tier.T3,
            relevance=Relevance.TANGENTIAL,
            access_status=AccessStatus.ACCESSIBLE,
        )
        assert ref.compute_grade() == CompositeGrade.BRONZE


class TestZeroTrustCaps:
    """Travas v3: pendência de verificação limita a grade a BRONZE."""

    def test_sem_identificador_e_sem_verificacao_vira_bronze(self):
        ref = Reference(tier=Tier.T1, relevance=Relevance.DIRECT)
        assert ref.compute_grade() == CompositeGrade.BRONZE
        assert ref.verification_pendencies()

    def test_autores_divergentes_vira_bronze(self):
        ref = _verified(
            tier=Tier.T1, relevance=Relevance.DIRECT, authors_verified=False
        )
        assert ref.compute_grade() == CompositeGrade.BRONZE

    def test_titulo_divergente_vira_bronze(self):
        ref = _verified(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            crossref_match=False,
            crossref_title_similarity=40.0,
        )
        assert ref.compute_grade() == CompositeGrade.BRONZE

    def test_doi_que_nao_resolve_vira_bronze(self):
        ref = Reference(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            doi="10.9999/quebrado",
            doi_resolves=False,
        )
        assert ref.compute_grade() == CompositeGrade.BRONZE

    def test_verificada_por_titulo_nao_tem_pendencia(self):
        ref = Reference(
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            verified_via="title-pubmed",
            pmid="12345678",
        )
        assert ref.compute_grade() == CompositeGrade.GOLD

    def test_retratada_continua_discard_mesmo_com_pendencia(self):
        ref = Reference(tier=Tier.T1, relevance=Relevance.DIRECT, retracted=True)
        assert ref.compute_grade() == CompositeGrade.DISCARD


class TestNewFields:
    """Testa novos campos v2."""

    def test_new_fields_default(self):
        ref = Reference()
        assert ref.relevance_score == 0.0
        assert ref.mapped_axes == []
        assert ref.source == ""
        assert ref.citation_count is None
        assert ref.canonical is False

    def test_new_fields_roundtrip(self):
        ref = Reference(
            id="test_1",
            relevance_score=15.5,
            mapped_axes=["Conceitos", "Aplicações"],
            source="api",
            citation_count=142,
            canonical=True,
        )
        data = ref.to_dict()
        restored = Reference.from_dict(data)

        assert restored.relevance_score == 15.5
        assert restored.mapped_axes == ["Conceitos", "Aplicações"]
        assert restored.source == "api"
        assert restored.citation_count == 142
        assert restored.canonical is True


class TestSerialization:
    """Testa to_dict() e from_dict()."""

    def test_roundtrip(self):
        ref = Reference(
            id="test_1",
            title="Test Article",
            authors=["Smith J", "Doe A"],
            year=2023,
            doi="10.1234/test",
            tier=Tier.T1,
            relevance=Relevance.DIRECT,
            access_status=AccessStatus.ACCESSIBLE,
            grade=CompositeGrade.GOLD,
        )
        data = ref.to_dict()
        restored = Reference.from_dict(data)

        assert restored.id == ref.id
        assert restored.title == ref.title
        assert restored.authors == ref.authors
        assert restored.year == ref.year
        assert restored.doi == ref.doi
        assert restored.tier == ref.tier
        assert restored.relevance == ref.relevance
        assert restored.access_status == ref.access_status
        assert restored.grade == ref.grade

    def test_from_dict_handles_missing_fields(self):
        data = {"id": "minimal", "title": "Minimal"}
        ref = Reference.from_dict(data)
        assert ref.id == "minimal"
        assert ref.tier == Tier.UNKNOWN
        assert ref.grade == CompositeGrade.UNGRADED
