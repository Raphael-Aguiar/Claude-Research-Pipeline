"""Testes da importação RIS (caminho legítimo para LILACS/SciELO)."""

import json

import pytest

from tools.ris_import import import_ris_file, parse_ris, ris_record_to_reference

SAMPLE_RIS = """TY  - JOUR
TI  - Inteligência artificial na gestão hospitalar: revisão integrativa
AU  - Silva, João
AU  - Souza Filho, Maria
PY  - 2023
JO  - Revista de Saúde Pública
VL  - 57
IS  - 2
SP  - 100
EP  - 112
DO  - https://doi.org/10.11606/s1518-8787.2023057004321
UR  - https://www.scielo.br/j/rsp/a/exemplo
AB  - Objetivo: mapear aplicações de IA na gestão hospitalar.
  Métodos: revisão integrativa em LILACS e SciELO.
ER  -
TY  - THES
TI  - Gestão de leitos com aprendizado de máquina
AU  - Pereira, Ana
PY  - 2022
ER  -
TY  - JOUR
AU  - Sem Título, Autor
PY  - 2021
ER  -
"""


class TestParseRis:
    def test_parseia_registros_e_continuacao_de_linha(self):
        records = parse_ris(SAMPLE_RIS)
        assert len(records) == 3
        first = records[0]
        assert first["TI"][0].startswith("Inteligência artificial")
        assert first["AU"] == ["Silva, João", "Souza Filho, Maria"]
        # continuação de linha do abstract foi absorvida
        assert "Métodos: revisão integrativa" in first["AB"][0]

    def test_converte_para_reference_com_doi_limpo(self):
        rec = parse_ris(SAMPLE_RIS)[0]
        ref = ris_record_to_reference(rec, "bvs-portal", 1)
        assert ref.doi == "10.11606/s1518-8787.2023057004321"
        assert ref.year == 2023
        assert ref.pages == "100-112"
        assert ref.journal == "Revista de Saúde Pública"
        assert ref.source_api == "bvs-portal"
        assert ref.source == "manual-import"

    def test_registro_sem_titulo_e_descartado(self):
        rec = parse_ris(SAMPLE_RIS)[2]
        assert ris_record_to_reference(rec, "bvs-portal", 3) is None


class TestImportRisFile:
    def test_importa_para_refs_raw_e_loga_busca(self, tmp_path, monkeypatch):
        import tools.config as cfg
        monkeypatch.setattr(cfg, "ESCRITA_DIR", tmp_path)
        # o módulo importou get_pipeline_dir por referência — patch nele também
        import tools.ris_import as ri
        monkeypatch.setattr(
            ri, "get_pipeline_dir",
            lambda name, create=True: _mk(tmp_path / name / "pipeline"),
        )
        (tmp_path / "Proj").mkdir()
        ris = tmp_path / "export.ris"
        ris.write_text(SAMPLE_RIS, encoding="utf-8")

        result = import_ris_file("Proj", ris)
        assert result["imported"] == 2  # 3º registro sem título
        assert result["with_doi"] == 1

        raw = json.loads(
            (tmp_path / "Proj" / "pipeline" / "refs-raw.json").read_text()
        )
        assert len(raw["references"]) == 2
        log = (tmp_path / "Proj" / "pipeline" / "search-log.md").read_text()
        assert "importação RIS manual" in log
        assert "AÇÃO NECESSÁRIA" in log

        # Reimportar não colide ids (sufixo) e appenda
        result2 = import_ris_file("Proj", ris)
        raw2 = json.loads(
            (tmp_path / "Proj" / "pipeline" / "refs-raw.json").read_text()
        )
        assert len(raw2["references"]) == 4
        ids = [d["id"] for d in raw2["references"]]
        assert len(ids) == len(set(ids))


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p
