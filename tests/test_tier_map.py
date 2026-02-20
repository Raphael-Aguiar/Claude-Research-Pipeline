"""Testes para tier_map.py — classify_domain()."""

import pytest

from tools.models import Tier
from tools.tier_map import classify_domain


class TestClassifyDomain:
    """Testa classificação de domínios por tier."""

    def test_t1_pubmed(self):
        _, tier = classify_domain("https://pubmed.ncbi.nlm.nih.gov/12345/")
        assert tier == Tier.T1

    def test_t1_scielo(self):
        _, tier = classify_domain("https://www.scielo.br/j/rsp/a/123/")
        assert tier == Tier.T1

    def test_t1_springer(self):
        _, tier = classify_domain("https://link.springer.com/article/10.1007/test")
        assert tier == Tier.T1

    def test_t1_sciencedirect(self):
        _, tier = classify_domain("https://www.sciencedirect.com/science/article/pii/123")
        assert tier == Tier.T1

    def test_t2_who(self):
        _, tier = classify_domain("https://www.who.int/publications/i/item/123")
        assert tier == Tier.T2

    def test_t2_govbr(self):
        _, tier = classify_domain("https://www.gov.br/saude/pt-br/")
        assert tier == Tier.T2

    def test_t2_datasus(self):
        _, tier = classify_domain("https://datasus.saude.gov.br/")
        assert tier == Tier.T2

    def test_t3_mckinsey(self):
        _, tier = classify_domain("https://www.mckinsey.com/industries/healthcare/")
        assert tier == Tier.T3

    def test_t3_statista(self):
        _, tier = classify_domain("https://www.statista.com/statistics/123/")
        assert tier == Tier.T3

    def test_t4_carefy(self):
        _, tier = classify_domain("https://blog.carefy.com.br/gestao-leitos")
        assert tier == Tier.T4

    def test_t4_youtube(self):
        _, tier = classify_domain("https://www.youtube.com/watch?v=123")
        assert tier == Tier.T4

    def test_unknown_domain(self):
        _, tier = classify_domain("https://some-unknown-site.xyz/page")
        assert tier == Tier.UNKNOWN

    def test_empty_url(self):
        _, tier = classify_domain("")
        assert tier == Tier.UNKNOWN

    def test_none_url(self):
        _, tier = classify_domain(None)
        assert tier == Tier.UNKNOWN

    def test_suffix_matching(self):
        """Subdomínio deve herdar classificação do domínio pai."""
        _, tier = classify_domain("https://sub.scielo.br/article/123")
        assert tier == Tier.T1

    def test_www_stripped(self):
        _, tier = classify_domain("https://www.bmj.com/content/123")
        assert tier == Tier.T1

    def test_returns_hostname(self):
        host, _ = classify_domain("https://www.nature.com/articles/123")
        assert host == "nature.com"
