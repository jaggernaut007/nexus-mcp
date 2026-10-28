"""Tests for evals.retrieval: metrics, query validity, candidates and rendering."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.retrieval import candidates as cand
from evals.retrieval import metrics, run


class TestMetrics:
    def test_dedupe_keeps_first_occurrence(self):
        assert metrics.dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_first_hit_rank_counts_distinct_files(self):
        assert metrics.first_hit_rank(["a", "a", "b"], {"b"}) == 2

    def test_first_hit_rank_none_when_absent(self):
        assert metrics.first_hit_rank(["a"], {"z"}) is None

    def test_hit_at_k_respects_the_cutoff(self):
        ranked = ["a", "b", "c"]
        assert metrics.hit_at_k(ranked, {"c"}, 2) == 0.0
        assert metrics.hit_at_k(ranked, {"c"}, 3) == 1.0

    def test_recall_at_k_is_the_fraction_of_relevant_files(self):
        assert metrics.recall_at_k(["a", "x", "b"], {"a", "b", "c", "d"}, 3) == 0.5

    def test_recall_at_k_empty_relevant_is_zero(self):
        assert metrics.recall_at_k(["a"], set(), 5) == 0.0

    def test_reciprocal_rank_values(self):
        assert metrics.reciprocal_rank(["x", "a"], {"a"}) == 0.5
        assert metrics.reciprocal_rank(["x"], {"a"}) == 0.0

    def test_reciprocal_rank_outside_k_is_zero(self):
        ranked = [f"f{i}" for i in range(12)] + ["a"]
        assert metrics.reciprocal_rank(ranked, {"a"}, k=10) == 0.0

    def test_score_query_perfect_ranking(self):
        got = metrics.score_query(["a", "b"], {"a"})
        assert got["hit@1"] == 1.0 and got["mrr@10"] == 1.0 and got["recall@5"] == 1.0

    def test_mean_metrics_averages_queries(self):
        mean = metrics.mean_metrics([{"hit@1": 1.0}, {"hit@1": 0.0}])
        assert mean == {"hit@1": 0.5, "queries": 2}

    def test_mean_metrics_empty(self):
        assert metrics.mean_metrics([]) == {}


class TestQuerySuites:
    def test_every_relevant_file_exists_on_disk(self):
        for name, suite in run.load_suites().items():
            root = run.suite_root(suite)
            if not root.is_dir():
                continue  # the flask clone or a private project is not on this machine
            for q in suite["queries"]:
                for rel in q["relevant"]:
                    assert (root / rel).is_file(), f"{name}/{q['id']}: {rel} not found"

    def test_query_ids_are_unique_inside_a_suite(self):
        for suite in run.load_suites().values():
            ids = [q["id"] for q in suite["queries"]]
            assert len(ids) == len(set(ids))

    def test_queries_do_not_contain_the_target_file_name(self):
        # A query that spells the file name tests spelling, not meaning.
        for suite in run.load_suites().values():
            for q in suite["queries"]:
                if q.get("kind") == "identifier":
                    continue  # these name symbols on purpose
                for rel in q["relevant"]:
                    stem = Path(rel).stem
                    text = q["query"].lower()
                    words = set(re.findall(r"[a-z]+", text))
                    assert stem not in words, q["id"]
                    if "_" in stem:
                        assert stem.replace("_", " ") not in text, q["id"]


class TestCandidates:
    def test_register_adds_an_eval_only_candidate_without_approx_size(self):
        registry = {"bge-small-en": {"dimensions": 384}}
        config = cand.register("granite-97m-r2-int8", registry)
        assert "approx_mb" not in config
        assert registry["granite-97m-r2-int8"]["onnx_file"].endswith(".onnx")

    def test_register_shipped_model_leaves_registry_alone(self):
        registry = {"bge-small-en": {"dimensions": 384}}
        assert cand.register("bge-small-en", registry) == {"dimensions": 384}

    def test_register_unknown_name_raises(self):
        with pytest.raises(KeyError):
            cand.register("nope", {})

    def test_every_candidate_has_the_required_registry_keys(self):
        required = {"hf_name", "dimensions", "max_seq_length", "trust_remote_code",
                    "prompt_prefix", "query_prefix", "backend"}
        for name, config in cand.CANDIDATES.items():
            if config is not None:
                assert required <= set(config), name
                assert config["trust_remote_code"] is False, name

    def test_candidates_never_touch_the_shipped_registry(self):
        from nexus_mcp.indexing.embedding_service import EMBEDDING_MODELS

        assert "granite-97m-r2-int8" not in EMBEDDING_MODELS


class TestRanking:
    def test_to_relative_files_strips_the_suite_root(self, tmp_path):
        results = [{"absolute_path": str(tmp_path / "pkg" / "a.py")}, {"filepath": "elsewhere.py"}]
        assert run.to_relative_files(results, tmp_path) == ["pkg/a.py", "elsewhere.py"]

    def test_to_relative_files_skips_results_without_a_path(self, tmp_path):
        assert run.to_relative_files([{"text": "x"}], tmp_path) == []

    def test_rank_files_vector_mode_uses_only_the_vector_engine(self, tmp_path):
        pipeline = SimpleNamespace(
            vector_engine=SimpleNamespace(
                search=lambda q, limit: [{"absolute_path": str(tmp_path / "v.py")}]
            ),
            bm25_engine=SimpleNamespace(search=lambda q, limit: pytest.fail("bm25 called")),
            graph_engine=None,
        )
        files = run.rank_files(pipeline, SimpleNamespace(), tmp_path, "q", "vector")
        assert files == ["v.py"]

    def test_rank_files_bm25_mode_uses_only_bm25(self, tmp_path):
        pipeline = SimpleNamespace(
            vector_engine=SimpleNamespace(search=lambda q, limit: pytest.fail("vector called")),
            bm25_engine=SimpleNamespace(
                search=lambda q, limit: [{"absolute_path": str(tmp_path / "b.py")}]
            ),
            graph_engine=None,
        )
        assert run.rank_files(pipeline, SimpleNamespace(), tmp_path, "q", "bm25") == ["b.py"]


class TestRender:
    def test_render_markdown_has_a_row_per_mode_and_marks_failures(self):
        mode = {"hit@1": 0.5, "hit@5": 0.75, "recall@10": 0.8, "mrr@10": 0.6,
                "median_query_ms": 12.0, "queries": 4}
        runs = [
            {"candidate": "m1", "peak_rss_mb": 300.0, "suites": {
                "s": {"index_seconds": 3.2, "modes": {m: mode for m in run.MODES}}}},
            {"candidate": "m2", "peak_rss_mb": 0, "suites": {}, "error": "boom"},
        ]
        text = run.render_markdown(runs, ["s"])
        assert text.count("| m1 |") == len(run.MODES)
        assert "| m2 | failed |" in text

    def test_peak_rss_mb_is_positive(self):
        assert run.peak_rss_mb() > 0

    def test_render_markdown_splits_hit_at_1_by_query_kind(self):
        mode = {"hit@1": 0.5, "hit@5": 0.75, "recall@10": 0.8, "mrr@10": 0.6,
                "median_query_ms": 12.0, "queries": 4,
                "by_kind": {"concept": {"hit@1": 0.25, "queries": 3},
                            "identifier": {"hit@1": 1.0, "queries": 1}}}
        runs = [{"candidate": "m1", "peak_rss_mb": 300.0, "suites": {
            "s": {"index_seconds": 3.2, "modes": {m: mode for m in run.MODES}}}}]
        text = run.render_markdown(runs, ["s"])
        assert "| Model | Mode | concept | identifier |" in text
        assert "| m1 | vector | 0.25 (3) | 1.00 (1) |" in text

    def test_render_markdown_skips_kind_table_when_only_one_kind(self):
        mode = {"hit@1": 0.5, "hit@5": 0.75, "recall@10": 0.8, "mrr@10": 0.6,
                "median_query_ms": 12.0, "queries": 4,
                "by_kind": {"concept": {"hit@1": 0.5, "queries": 4}}}
        runs = [{"candidate": "m1", "peak_rss_mb": 300.0, "suites": {
            "s": {"index_seconds": 3.2, "modes": {m: mode for m in run.MODES}}}}]
        assert "by query kind" not in run.render_markdown(runs, ["s"])

    def test_render_markdown_marks_a_suite_whose_root_is_missing(self):
        runs = [{"candidate": "m1", "peak_rss_mb": 1.0,
                 "suites": {"s": {"error": "root not found: /nope"}}}]
        assert "| m1 | failed |" in run.render_markdown(runs, ["s"])


class TestSuiteLoading:
    def test_suite_root_expands_home_and_resolves_relative_to_the_repo(self):
        assert run.suite_root({"root": "~/x"}) == (Path.home() / "x").resolve()
        assert run.suite_root({"root": "evals/fixtures/shop_repo"}) == (
            run.REPO_ROOT / "evals/fixtures/shop_repo"
        ).resolve()

    def test_kind_of_defaults_to_concept(self):
        assert run.kind_of({"id": "a"}) == "concept"
        assert run.kind_of({"id": "a", "kind": "identifier"}) == "identifier"

    def test_load_suites_includes_the_public_flask_suite(self):
        assert "flask" in run.load_suites()

    def test_run_candidate_reports_a_missing_root_instead_of_crashing(self, tmp_path, monkeypatch):
        suite = {"root": str(tmp_path / "absent"), "queries": []}
        monkeypatch.setattr(run, "load_suites", lambda *a, **k: {"gone": suite})
        out = run.run_candidate("bge-small-en", ["gone"], work_dir=tmp_path / "w")
        assert "root not found" in out["suites"]["gone"]["error"]
