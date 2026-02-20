"""Testes para verificação — fixtures sem rede."""

import json
from pathlib import Path

import pytest

from tools.models import Reference
from tools.exporters.json_export import load_refs_json, save_refs_json

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestJsonExport:
    """Testa serialização/deserialização JSON."""

    def test_load_sample_refs(self):
        refs = load_refs_json(FIXTURES_DIR / "sample_refs.json")
        assert len(refs) == 5
        assert refs[0].title == "Artificial Intelligence in Hospital Management: A Systematic Review"
        assert refs[0].doi == "10.1186/s12913-023-09876-5"
        assert refs[0].grade.value == "gold"

    def test_roundtrip(self, tmp_path):
        original = [
            Reference(
                id="test_1",
                title="Test Article",
                authors=["Author A"],
                year=2023,
                doi="10.1234/test",
            ),
            Reference(
                id="test_2",
                title="Another Article",
                year=2024,
            ),
        ]

        output = tmp_path / "test.json"
        save_refs_json(original, output, metadata={"project": "test"})
        loaded = load_refs_json(output)

        assert len(loaded) == 2
        assert loaded[0].id == "test_1"
        assert loaded[0].doi == "10.1234/test"
        assert loaded[1].id == "test_2"

    def test_load_crossref_fixture(self):
        with open(FIXTURES_DIR / "sample_crossref_response.json") as f:
            data = json.load(f)
        msg = data["message"]
        assert msg["DOI"] == "10.1186/s12913-023-09876-5"
        assert msg["type"] == "journal-article"
        assert len(msg["author"]) == 3
