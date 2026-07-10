"""Testes da Fase 2 — triagem LLM, kappa, PRISMA, meta-revisão, descritores."""

import json

import pytest

from tools.models import (
    AccessStatus,
    CompositeGrade,
    Modality,
    Reference,
    Relevance,
    SearchConfig,
)


def _ref(id, relevance, llm=None, **kw):
    r = Reference(id=id, title=kw.pop("title", f"Ref {id}"),
                  doi=f"10.1000/{id}", doi_resolves=True, **kw)
    r.relevance = relevance
    r.llm_verdict = llm
    return r


class TestKappa:
    def test_concordancia_perfeita(self):
        from tools.screening import _compute_agreement
        refs = [
            _ref("a", Relevance.DIRECT, "include"),
            _ref("b", Relevance.OFF_TOPIC, "exclude"),
            _ref("c", Relevance.TANGENTIAL, "include"),
            _ref("d", Relevance.OFF_TOPIC, "exclude"),
        ]
        kappa, agreement, div = _compute_agreement(refs)
        assert agreement == 1.0
        assert kappa == 1.0
        assert div == []

    def test_divergencias_detectadas(self):
        from tools.screening import _compute_agreement
        refs = [
            _ref("a", Relevance.DIRECT, "exclude"),   # divergente
            _ref("b", Relevance.OFF_TOPIC, "include"),  # divergente
            _ref("c", Relevance.DIRECT, "include"),
            _ref("d", Relevance.OFF_TOPIC, "exclude"),
        ]
        kappa, agreement, div = _compute_agreement(refs)
        assert agreement == 0.5
        assert len(div) == 2

    def test_maybe_vai_para_revisao_mas_nao_entra_no_kappa(self):
        from tools.screening import _compute_agreement
        refs = [
            _ref("a", Relevance.DIRECT, "maybe"),
            _ref("b", Relevance.DIRECT, "include"),
            _ref("c", Relevance.OFF_TOPIC, "exclude"),
        ]
        kappa, agreement, div = _compute_agreement(refs)
        assert agreement == 1.0
        assert any(r.id == "a" for r in div)

    def test_sem_veredictos_retorna_none(self):
        from tools.screening import _compute_agreement
        refs = [_ref("a", Relevance.DIRECT)]
        kappa, _, _ = _compute_agreement(refs)
        assert kappa is None


class TestScreeningRoundtrip:
    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        import tools.config as cfg
        monkeypatch.setattr(cfg, "ESCRITA_DIR", tmp_path)
        proj = tmp_path / "Projeto Teste"
        (proj / "pipeline").mkdir(parents=True)
        refs = [
            _ref("r1", Relevance.DIRECT, title="Big data in hospitals",
                 abstract="Machine learning for bed management"),
            _ref("r2", Relevance.OFF_TOPIC, title="Antibiotics in cattle"),
        ]
        data = {"metadata": {}, "references": [r.to_dict() for r in refs]}
        (proj / "pipeline" / "refs-verified.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        return "Projeto Teste", proj

    def test_export_e_import(self, project):
        from tools.screening import (
            export_screening_batch,
            import_screening_verdicts,
        )
        name, proj = project
        config = SearchConfig(
            project_name=name,
            modality=Modality.REVISAO_ESCOPO,
            research_question="Big data na gestão hospitalar?",
            inclusion_criteria=["Estudos sobre gestão hospitalar"],
            exclusion_criteria=["Estudos veterinários"],
        )
        result = export_screening_batch(name, config)
        assert result["exported"] == 2
        batch = (proj / "pipeline" / "screening-batch.jsonl").read_text()
        assert "Big data in hospitals" in batch
        instructions = (proj / "pipeline" / "screening-instructions.md").read_text()
        assert "I1" in instructions and "E1" in instructions

        # Veredictos do "revisor LLM"
        verdicts = [
            {"id": "r1", "verdict": "include", "reason": "gestão hospitalar", "criteria": ["I1"]},
            {"id": "r2", "verdict": "exclude", "reason": "veterinário", "criteria": ["E1"]},
        ]
        (proj / "pipeline" / ".screening-verdicts.jsonl").write_text(
            "\n".join(json.dumps(v) for v in verdicts), encoding="utf-8"
        )
        result = import_screening_verdicts(name)
        assert result["imported"] == 2
        assert result["missing"] == 0

        merged = json.loads(
            (proj / "pipeline" / "refs-verified.json").read_text()
        )
        by_id = {d["id"]: d for d in merged["references"]}
        assert by_id["r1"]["llm_verdict"] == "include"
        assert by_id["r2"]["llm_verdict"] == "exclude"
        assert (proj / "pipeline" / "screening-report.md").exists()

    def test_import_rejeita_verdict_invalido(self, project):
        from tools.screening import import_screening_verdicts
        name, proj = project
        (proj / "pipeline" / ".screening-verdicts.jsonl").write_text(
            '{"id": "r1", "verdict": "talvez"}', encoding="utf-8"
        )
        result = import_screening_verdicts(name)
        assert result["imported"] == 0


class TestS06LLMCombination:
    def _screen(self, refs, modality=Modality.REVISAO_ESCOPO):
        from tools.stages.s06_screen import screen_references
        config = SearchConfig(
            project_name="t", modality=modality,
            keyword_blocks=[
                {"concept": "tec", "terms": ["big data"]},
                {"concept": "dom", "terms": ["hospital"]},
            ],
        )
        return screen_references(refs, config)

    def test_llm_include_promove_tangential(self, tmp_path, monkeypatch):
        import tools.config as cfg
        monkeypatch.setattr(cfg, "ESCRITA_DIR", tmp_path)
        (tmp_path / "t").mkdir()
        r = Reference(id="x", title="Big data study", abstract="big data only")
        r.llm_verdict = "include"
        out = self._screen([r])
        assert out[0].relevance == Relevance.DIRECT
        assert "llm_include" in out[0].relevance_method

    def test_llm_exclude_divergente_nao_descarta(self, tmp_path, monkeypatch):
        import tools.config as cfg
        monkeypatch.setattr(cfg, "ESCRITA_DIR", tmp_path)
        (tmp_path / "t").mkdir()
        r = Reference(id="x", title="Big data in hospital management",
                      abstract="big data hospital")
        r.llm_verdict = "exclude"
        out = self._screen([r])
        assert out[0].relevance != Relevance.OFF_TOPIC  # não descarta silencioso
        assert "llm_divergent_exclude" in out[0].relevance_method


class TestMetaRevisao:
    def test_is_review_like(self):
        from tools.stages.s06_screen import _is_review_like
        assert _is_review_like(Reference(title="AI in health: a systematic review"))
        assert _is_review_like(Reference(title="Revisão sistemática sobre IA"))
        assert _is_review_like(Reference(title="Umbrella review of ML"))
        assert _is_review_like(Reference(title="X", pub_type="Meta-Analysis"))
        assert not _is_review_like(Reference(title="A randomized trial of AI triage"))

    def test_meta_revisao_exclui_nao_revisao(self, tmp_path, monkeypatch):
        import tools.config as cfg
        monkeypatch.setattr(cfg, "ESCRITA_DIR", tmp_path)
        (tmp_path / "t").mkdir()
        from tools.stages.s06_screen import screen_references
        config = SearchConfig(
            project_name="t", modality=Modality.META_REVISAO,
            keyword_blocks=[{"concept": "tec", "terms": ["big data"]}],
        )
        primary = Reference(id="p", title="A trial of big data triage",
                            abstract="big data")
        review = Reference(id="r", title="Big data: a systematic review",
                           abstract="big data")
        out = screen_references([primary, review], config)
        by_id = {r.id: r for r in out}
        assert by_id["p"].relevance == Relevance.OFF_TOPIC
        assert by_id["p"].relevance_method == "nao_e_revisao"
        assert by_id["r"].relevance != Relevance.OFF_TOPIC

    def test_pubmed_query_meta_revisao_filtra_revisoes(self):
        from tools.apis.pubmed import _build_query
        config = SearchConfig(
            modality=Modality.META_REVISAO,
            keyword_blocks=[{"concept": "t", "terms": ["big data"]}],
        )
        q = _build_query(config)
        assert "systematic[sb]" in q
        config.modality = Modality.PESQUISA_BASE
        q2 = _build_query(config)
        assert "systematic[sb]" not in q2
        assert '"Journal Article"[PT]' in q2


class TestPrismaFlow:
    def test_contagens_e_arquivo(self, tmp_path):
        from tools.exporters.prisma_flow import generate_prisma_flow
        config = SearchConfig(
            project_name="Teste", modality=Modality.REVISAO_ESCOPO,
        )
        refs = [
            _ref("a", Relevance.DIRECT, source_api="pubmed",
                 grade=CompositeGrade.GOLD),
            _ref("b", Relevance.OFF_TOPIC, source_api="pubmed",
                 relevance_method="exclusion_keyword"),
            _ref("c", Relevance.TANGENTIAL, source_api="openalex",
                 grade=CompositeGrade.SILVER),
            Reference(id="dup", is_duplicate=True, source_api="openalex"),
        ]
        refs[1].relevance_method = "exclusion_keyword"
        out = tmp_path / "prisma-flow.md"
        counts = generate_prisma_flow(refs, config, out)
        assert counts["identified"] == 4
        assert counts["duplicates"] == 1
        assert counts["screened"] == 3
        assert counts["excluded_screening"] == 1
        assert counts["included"] == 2
        content = out.read_text()
        assert "PRISMA-ScR" in content
        assert "```mermaid" in content
        assert "Termo de exclusão do escopo" in content


class TestMeshLookupParse:
    def test_ordena_exato_primeiro(self, monkeypatch):
        from tools.apis import mesh_lookup

        class FakeResp:
            status_code = 200
            def json(self):
                return [
                    {"resource": "u2", "label": "Machine Learning Algorithms"},
                    {"resource": "u1", "label": "Machine Learning"},
                ]

        monkeypatch.setattr(
            mesh_lookup.requests, "get", lambda *a, **k: FakeResp()
        )
        monkeypatch.setattr(mesh_lookup.time, "sleep", lambda s: None)
        out = mesh_lookup.suggest_mesh_descriptors("machine learning")
        assert out[0]["label"] == "Machine Learning"
        assert out[0]["exact"] is True
