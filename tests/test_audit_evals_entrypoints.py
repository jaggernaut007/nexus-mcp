"""Audit tests for eval entry points that no other test runs."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evals.retrieval import run as retrieval
from evals.routing import runner


class TestStreamRunTimeout:
    def test_stream_run_kills_a_silent_child_and_reports_the_timeout(self):
        lines, timed_out, early, _tail = runner.stream_run(
            [sys.executable, "-c", "import time; time.sleep(30)"], {}, Path("."), 0.5, 3
        )
        assert timed_out is True
        assert early is False
        assert lines == []


class TestPrepareRepo:
    def test_prepare_repo_copies_the_fixture_and_indexes_once(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: calls.append(argv))
        repo = runner.prepare_repo("py", tmp_path / "src", tmp_path / "work")
        assert (repo / "shop" / "orders.py").exists()
        assert len(calls) == 1
        assert calls[0][0] == "py" and calls[0][2] == str(repo)

    def test_prepare_repo_skips_when_an_index_exists(self, tmp_path, monkeypatch):
        (tmp_path / "work" / "shop_repo" / ".nexus").mkdir(parents=True)
        (tmp_path / "work" / "fixture.sha256").write_text(
            runner.fixture_digest(runner.FIXTURE_DIR)
        )
        calls = []
        monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: calls.append(argv))
        runner.prepare_repo("py", tmp_path / "src", tmp_path / "work")
        assert calls == []

    def test_prepare_repo_rebuilds_when_the_fixture_changed(self, tmp_path, monkeypatch):
        (tmp_path / "work" / "shop_repo" / ".nexus").mkdir(parents=True)
        (tmp_path / "work" / "fixture.sha256").write_text("an older fixture")
        calls = []
        monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: calls.append(argv))
        runner.prepare_repo("py", tmp_path / "src", tmp_path / "work")
        assert len(calls) == 1

    def test_prepare_repo_rebuilds_after_a_cut_off_index(self, tmp_path, monkeypatch):
        """A `.nexus` folder without a stamp is a run that never finished."""
        (tmp_path / "work" / "shop_repo" / ".nexus").mkdir(parents=True)
        calls = []
        monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kw: calls.append(argv))
        runner.prepare_repo("py", tmp_path / "src", tmp_path / "work")
        assert len(calls) == 1

    def test_prepare_repo_raises_when_the_preindex_fails(self, tmp_path, monkeypatch):
        def boom(argv, **kw):
            raise subprocess.CalledProcessError(1, argv)

        monkeypatch.setattr(runner.subprocess, "run", boom)
        with pytest.raises(subprocess.CalledProcessError):
            runner.prepare_repo("py", tmp_path / "src", tmp_path / "work")


class TestRoutingMain:
    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        seen = {}
        monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
        monkeypatch.setattr(runner, "WORK_DIR", tmp_path / "work")
        monkeypatch.setattr(runner, "require_login", lambda d: None)
        monkeypatch.setattr(runner, "claude_version", lambda: "test")

        def fake_prepare(python, src_dir, work_dir):
            seen["work_dir"] = work_dir
            seen["prepare_src"] = src_dir
            return tmp_path / "repo"

        def fake_suite(specs, conditions, modes, reps, out_path, run_one):
            seen.update(specs=specs, conditions=conditions, modes=modes, out=out_path)
            return len(specs)

        monkeypatch.setattr(runner, "prepare_repo", fake_prepare)
        monkeypatch.setattr(runner, "run_suite", fake_suite)
        return seen

    def test_main_smoke_runs_four_prompts_mcp_only_tool_search_on(self, wired):
        assert runner.main(["--smoke"]) == 0
        assert len(wired["specs"]) == 4
        assert wired["conditions"] == ["mcp-only"] and wired["modes"] == [True]

    def test_main_src_without_a_nexus_mcp_package_exits(self, wired, tmp_path):
        with pytest.raises(SystemExit, match="nexus_mcp"):
            runner.main(["--smoke", "--src", str(tmp_path)])

    def test_main_other_src_uses_its_own_work_dir(self, wired, tmp_path):
        other = tmp_path / "old-checkout" / "src"
        (other / "nexus_mcp").mkdir(parents=True)
        runner.main(["--smoke", "--src", str(other)])
        digest = hashlib.sha256(str(other.resolve()).encode()).hexdigest()[:10]
        assert wired["work_dir"] == tmp_path / "work" / f"src-{digest}"

    def test_main_default_src_uses_the_shared_work_dir(self, wired, tmp_path):
        runner.main(["--smoke"])
        assert wired["work_dir"] == tmp_path / "work"

    def test_main_refuses_to_mix_models_in_one_label(self, wired, tmp_path):
        out = tmp_path / "results" / "routing-x.jsonl"
        out.parent.mkdir()
        out.write_text(json.dumps({"model": "opus", "prompt_id": "a"}) + "\n")
        with pytest.raises(SystemExit, match="new --label"):
            runner.main(["--smoke", "--label", "x", "--model", "sonnet"])

    def test_main_returns_3_when_the_usage_limit_stops_the_run(self, wired, monkeypatch):
        def limit(*a, **k):
            raise runner.UsageLimitReached("usage limit")

        monkeypatch.setattr(runner, "run_suite", limit)
        assert runner.main(["--smoke"]) == 3

    def test_main_only_filters_prompts(self, wired):
        runner.main(["--only", "graph-callers"])
        assert [s["id"] for s in wired["specs"]] == ["graph-callers"]


class TestRetrievalEntryPoints:
    def test_run_in_subprocess_returns_the_last_json_line(self, monkeypatch):
        out = "log line\n" + json.dumps({"candidate": "m", "suites": {}}) + "\n"
        monkeypatch.setattr(
            retrieval.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
        )
        assert retrieval.run_in_subprocess("m", ["shop_repo"])["candidate"] == "m"

    def test_run_in_subprocess_reports_the_stderr_tail_when_the_child_dies(self, monkeypatch):
        monkeypatch.setattr(
            retrieval.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 1, stdout="", stderr="a\nb\nc\nOOM killed"
            ),
        )
        result = retrieval.run_in_subprocess("m", ["shop_repo"])
        assert result["candidate"] == "m"
        assert "OOM killed" in result["error"] and result["suites"] == {}

    def test_main_single_prints_one_json_line(self, monkeypatch, capsys):
        monkeypatch.setattr(retrieval, "run_candidate", lambda name, suites: {"candidate": name})
        assert retrieval.main(["--single", "bge-small-en", "--suites", "shop_repo"]) == 0
        assert json.loads(capsys.readouterr().out) == {"candidate": "bge-small-en"}

    def test_main_writes_results_and_reports_failed_candidates(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr(retrieval, "RESULTS_DIR", tmp_path)
        monkeypatch.setattr(
            retrieval, "run_in_subprocess",
            lambda name, suites: {"candidate": name, "suites": {}, "peak_rss_mb": 0, "error": "x"},
        )
        assert retrieval.main(["--candidates", "bge-small-en", "--label", "t"]) == 0
        written = json.loads((tmp_path / "retrieval-t.json").read_text())
        assert written[0]["error"] == "x"
        assert "bge-small-en failed: x" in capsys.readouterr().err
