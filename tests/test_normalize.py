"""Testes para s03_normalize.py — dedup e normalização (v2)."""

import pytest

from tools.models import Reference
from tools.stages.s03_normalize import (
    FUZZY_THRESHOLD,
    SUSPECT_THRESHOLD,
    _fuzzy_similarity,
    _normalize_fields,
)


class TestNormalizeFields:
    """Testa normalização de campos."""

    def test_doi_lowercase(self):
        ref = Reference(doi="10.1186/S12913-023-09876-5")
        _normalize_fields(ref)
        assert ref.doi == "10.1186/s12913-023-09876-5"

    def test_doi_strip_url_prefix(self):
        ref = Reference(doi="https://doi.org/10.1186/s12913-023-09876-5")
        _normalize_fields(ref)
        assert ref.doi == "10.1186/s12913-023-09876-5"

    def test_doi_strip_http_prefix(self):
        ref = Reference(doi="http://doi.org/10.1186/test")
        _normalize_fields(ref)
        assert ref.doi == "10.1186/test"

    def test_doi_strip_doi_prefix(self):
        ref = Reference(doi="doi:10.1186/test")
        _normalize_fields(ref)
        assert ref.doi == "10.1186/test"

    def test_title_strip(self):
        ref = Reference(title="  Test Title  ")
        _normalize_fields(ref)
        assert ref.title == "Test Title"


class TestFuzzySimilarity:
    """Testa cálculo de similaridade fuzzy (v2 — threshold 90%)."""

    def test_identical_titles_high_score(self):
        a = Reference(title="AI in Hospital Management", year=2023, authors=["Silva J"])
        b = Reference(title="AI in Hospital Management", year=2023, authors=["Silva J"])
        score = _fuzzy_similarity(a, b)
        assert score >= FUZZY_THRESHOLD

    def test_similar_titles_with_typo(self):
        a = Reference(title="Artificial Intelligence in Hospital Management", year=2023)
        b = Reference(title="Artificial Intelligence in Hospital Managment", year=2023)
        score = _fuzzy_similarity(a, b)
        assert score >= SUSPECT_THRESHOLD  # Pelo menos suspeita

    def test_different_titles_zero(self):
        a = Reference(title="AI in Hospital Management", year=2023)
        b = Reference(title="Tuberculosis Treatment Outcomes", year=2023)
        score = _fuzzy_similarity(a, b)
        assert score == 0.0

    def test_different_years_by_more_than_one(self):
        a = Reference(title="AI in Hospital Management", year=2023)
        b = Reference(title="AI in Hospital Management", year=2020)
        score = _fuzzy_similarity(a, b)
        assert score == 0.0  # Anos muito diferentes

    def test_adjacent_years_preprint(self):
        """Preprint vs publicado: ano ±1 deve ter score alto."""
        a = Reference(title="AI in Hospital Management", year=2023, authors=["Silva J"])
        b = Reference(title="AI in Hospital Management", year=2024, authors=["Silva J"])
        score = _fuzzy_similarity(a, b)
        assert score >= SUSPECT_THRESHOLD

    def test_different_authors_zero(self):
        a = Reference(title="AI in Hospital Management", year=2023, authors=["Silva J"])
        b = Reference(title="AI in Hospital Management", year=2023, authors=["Yamamoto K"])
        score = _fuzzy_similarity(a, b)
        assert score == 0.0

    def test_no_title_zero(self):
        a = Reference(title="", year=2023)
        b = Reference(title="Test", year=2023)
        score = _fuzzy_similarity(a, b)
        assert score == 0.0

    def test_no_year_still_matches(self):
        a = Reference(title="AI in Hospital Management", year=None, authors=["Silva J"])
        b = Reference(title="AI in Hospital Management", year=None, authors=["Silva J"])
        score = _fuzzy_similarity(a, b)
        assert score >= FUZZY_THRESHOLD
