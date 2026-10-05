"""Tests for the embedding-model dimension of the benchmark harness.

`nexus@m1` means: the nexus condition, with a server that uses m1 and
its own index folder. No live claude call and no real indexing happens here.
"""

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks import conditions as cond
from benchmarks import nexus_server, preindex_models, runner
from nexus_mcp.indexing import embedding_service as es

FIXTURES = Path(__file__).parent / "fixtures" / "bench"


class TestConditionLabels:
    def test_split_condition_separates_the_model(self):
        assert cond.split_condition("nexus@granite-97m-r2-int8") == ("nexus", "granite-97m-r2-int8")
        assert cond.split_condition("nexus") == ("nexus", None)
        assert cond.split_condition("nexus@") == ("nexus", None)

    def test_expand_conditions_adds_each_model_to_the_nexus_conditions(self):
        out = cond.expand_conditions(["baseline", "nexus"], ["a", "b"])
        assert out == ["baseline", "nexus@a", "nexus@b"]

    def test_expand_conditions_without_models_is_unchanged(self):
        assert cond.expand_conditions(["baseline", "nexus"], []) == ["baseline", "nexus"]

    def test_expand_conditions_keeps_the_plugin_and_labelled_names(self):
        out = cond.expand_conditions(["nexus-plugin", "mcp-only@x", "mcp-only"], ["a"])
        assert out == ["nexus-plugin", "mcp-only@x", "mcp-only@a"]

    def test_build_argv_accepts_a_model_label_on_a_server_condition(self):
        argv = cond.build_argv("nexus@granite-97m-r2-int8", "q", "sonnet", 1.0)
        assert "--mcp-config" in argv
        assert argv[argv.index("--model") + 1] == "sonnet"

    @pytest.mark.parametrize("label", ["baseline@x", "nexus-plugin@x", "bogus@x"])
    def test_build_argv_rejects_a_model_where_it_has_no_meaning(self, label):
        with pytest.raises(ValueError):
            cond.build_argv(label, "q", "sonnet", 1.0)

    def test_build_run_uses_the_given_mcp_config(self, tmp_path):
        config = tmp_path / "m.json"
        built = cond.build_run(
            "nexus@x", "q", "sonnet", 1.0, tmp_path, env={}, mcp_config_path=config
        )
        argv = built["argv"]
        assert argv[argv.index("--mcp-config") + 1] == str(config)


class TestModelMcpConfig:
    def test_config_sets_the_model_the_index_folder_and_the_server_entry_point(self, tmp_path):
        config = cond.model_mcp_config("granite-97m-r2-int8", tmp_path / "idx", python="py")
        server = config["mcpServers"]["nexus-mcp"]
        assert server["command"] == "py"
        assert server["args"] == ["-m", "benchmarks.nexus_server"]
        assert server["env"]["NEXUS_EMBEDDING_MODEL"] == "granite-97m-r2-int8"
        assert server["env"]["NEXUS_STORAGE_DIR"] == str(tmp_path / "idx")

    def test_pythonpath_holds_the_source_tree_and_the_repository_root(self, tmp_path):
        env = cond.model_mcp_config("m", tmp_path)["mcpServers"]["nexus-mcp"]["env"]
        parts = env["PYTHONPATH"].split(":")
        assert str(cond.REPO_ROOT / "src") in parts
        assert str(cond.REPO_ROOT) in parts

    def test_write_model_mcp_config_creates_parents_and_valid_json(self, tmp_path):
        path = cond.write_model_mcp_config(tmp_path / "a" / "b.json", "m", tmp_path)
        assert json.loads(path.read_text())["mcpServers"]["nexus-mcp"]["env"][
            "NEXUS_EMBEDDING_MODEL"
        ] == "m"


class TestNexusServer:
    @pytest.fixture(autouse=True)
    def _restore_registry(self):
        before = dict(es.EMBEDDING_MODELS)
        yield
        es.EMBEDDING_MODELS.clear()
        es.EMBEDDING_MODELS.update(before)

    def test_register_candidate_adds_an_eval_only_model(self):
        assert nexus_server.register_candidate("granite-97m-r2-int8") == "granite-97m-r2-int8"
        assert es.EMBEDDING_MODELS["granite-97m-r2-int8"]["dimensions"] == 384

    def test_register_candidate_ignores_the_shipped_model(self):
        assert nexus_server.register_candidate("bge-small-en") is None

    def test_register_candidate_ignores_an_unknown_name(self):
        assert nexus_server.register_candidate("not-a-model") is None
        assert "not-a-model" not in es.EMBEDDING_MODELS

    def test_register_candidate_reads_the_environment_by_default(self, monkeypatch):
        monkeypatch.setenv("NEXUS_EMBEDDING_MODEL", "granite-97m-r2-int8")
        assert nexus_server.register_candidate() == "granite-97m-r2-int8"
        monkeypatch.delenv("NEXUS_EMBEDDING_MODEL")
        assert nexus_server.register_candidate() is None

    def test_main_registers_the_model_before_the_server_starts(self, monkeypatch):
        calls = []
        monkeypatch.setattr(nexus_server, "register_candidate", lambda *a: calls.append("reg"))
        import nexus_mcp.server as server

        monkeypatch.setattr(server, "main", lambda: calls.append("serve"))
        nexus_server.main()
        assert calls == ["reg", "serve"]


class TestPreindexModels:
    def test_storage_dir_is_per_model_inside_the_repo(self, tmp_path):
        found = preindex_models.storage_dir_for(tmp_path, "granite-97m-r2-int8")
        assert found == tmp_path / ".nexus-granite-97m-r2-int8"

    def test_index_env_sets_model_storage_and_pythonpath(self, tmp_path):
        env = preindex_models.index_env(tmp_path / "s", "m", base_env={"X": "1"})
        assert env["X"] == "1"
        assert env["NEXUS_EMBEDDING_MODEL"] == "m"
        assert env["NEXUS_STORAGE_DIR"] == str(tmp_path / "s")
        assert str(preindex_models.REPO_ROOT) in env["PYTHONPATH"].split(":")

    def test_preindex_skips_a_model_that_already_has_an_index(self, tmp_path, monkeypatch):
        (tmp_path / ".nexus-m").mkdir()
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
        assert preindex_models.preindex(tmp_path, "r", "m") is True

    def test_preindex_runs_the_child_process_with_the_model_environment(
        self, tmp_path, monkeypatch
    ):
        seen = {}

        def fake_run(cmd, env=None, cwd=None):
            seen.update(cmd=cmd, env=env)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert preindex_models.preindex(tmp_path, "repo", "m") is True
        assert seen["cmd"][-3:-1] == [str(tmp_path), "repo@m"]
        assert seen["env"]["NEXUS_STORAGE_DIR"] == str(tmp_path / ".nexus-m")

    def test_preindex_reports_a_failed_child(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1)
        )
        assert preindex_models.preindex(tmp_path, "repo", "m") is False

    def test_main_returns_1_when_a_model_fails(self, tmp_path, monkeypatch):
        (tmp_path / "repo").mkdir()
        monkeypatch.setattr(preindex_models, "REPOS_DIR", tmp_path)
        monkeypatch.setattr(preindex_models, "preindex", lambda *a: False)
        assert preindex_models.main(["--repo", "repo", "--models", "a,b"]) == 1

    def test_main_exits_when_the_repo_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preindex_models, "REPOS_DIR", tmp_path)
        with pytest.raises(SystemExit):
            preindex_models.main(["--repo", "nope", "--models", "a"])


class _FakePopen:
    def __init__(self, stdout_text):
        self.pid = 999999
        self._stdout = stdout_text

    def communicate(self, timeout=None):
        return (self._stdout, "")


def _task():
    return {
        "id": "t1",
        "prompt": "Where is it?",
        "ground_truth": {"relevant_files": ["a.py"], "must_mention_files": [], "facts": []},
        "max_budget_usd": 1.0,
        "timeout_s": 5,
    }


class TestRunnerWithModels:
    def test_run_once_records_the_model_and_starts_that_models_server(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
        captured = {}

        def fake_popen(argv, **kw):
            captured["argv"] = argv
            return _FakePopen((FIXTURES / "nexus_run.jsonl").read_text())

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        repo_dir = tmp_path / "jobscout"
        record = runner.run_once(
            _task(), "nexus@m1", {"name": "jobscout", "pin": "p"}, repo_dir, "sonnet",
            tmp_path,
        )
        assert record["condition"] == "nexus@m1"
        assert record["embedding_model"] == "m1"
        argv = captured["argv"]
        config_path = Path(argv[argv.index("--mcp-config") + 1])
        server = json.loads(config_path.read_text())["mcpServers"]["nexus-mcp"]
        assert server["env"]["NEXUS_STORAGE_DIR"] == str(repo_dir / ".nexus-m1")

    def test_run_once_scores_a_model_label_like_nexus_not_like_baseline(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
        monkeypatch.setattr(
            runner.subprocess, "Popen",
            lambda *a, **k: _FakePopen((FIXTURES / "nexus_run.jsonl").read_text()),
        )
        task = _task()
        task["ground_truth"]["relevant_files"] = ["django/dispatch/dispatcher.py"]
        record = runner.run_once(
            task, "nexus@m", {"name": "django", "pin": "p"}, tmp_path, "sonnet", tmp_path
        )
        assert "django/dispatch/dispatcher.py" in record["files_touched"]

    def test_run_once_baseline_records_no_embedding_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess, "Popen",
            lambda *a, **k: _FakePopen((FIXTURES / "baseline_run.jsonl").read_text()),
        )
        record = runner.run_once(
            _task(), "baseline", {"name": "r", "pin": "p"}, tmp_path, "sonnet", tmp_path
        )
        assert record["embedding_model"] is None

    def test_run_suite_stops_early_when_a_models_index_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "repo_dir_for", lambda suite: tmp_path)
        suite = {"repo": {"name": "r", "pin": "p"}, "tasks": [_task()]}
        with pytest.raises(SystemExit) as exc:
            runner.run_suite(suite, ["nexus@m1"], 1, "sonnet", tmp_path, tmp_path / "o")
        assert "preindex_models" in str(exc.value)

    def test_run_suite_runs_when_every_model_has_an_index(self, tmp_path, monkeypatch):
        (tmp_path / ".nexus-m").mkdir()
        monkeypatch.setattr(runner, "repo_dir_for", lambda suite: tmp_path)
        monkeypatch.setattr(
            runner, "run_once",
            lambda task, condition, *a, **k: {"task_id": "t1", "condition": condition},
        )
        suite = {"repo": {"name": "r", "pin": "p"}, "tasks": [_task()]}
        records = runner.run_suite(
            suite, ["baseline", "nexus@m"], 1, "sonnet", tmp_path, tmp_path / "o.jsonl"
        )
        assert [r["condition"] for r in records] == ["baseline", "nexus@m"]

    def test_main_expands_nexus_conditions_per_embedding_model(self, tmp_path, monkeypatch):
        tasks = tmp_path / "t.yaml"
        tasks.write_text(
            "repo: {name: r, pin: p}\ntasks:\n  - {id: t1, prompt: q, ground_truth: {}}\n"
        )
        seen = {}

        def fake_run_suite(suite, condition_names, *a, **k):
            seen["conditions"] = condition_names
            return []

        monkeypatch.setattr(runner, "require_login", lambda *a, **k: None)
        monkeypatch.setattr(runner, "run_suite", fake_run_suite)
        runner.main(
            ["--tasks", str(tasks), "--conditions", "baseline,nexus",
             "--embedding-models", "a, b", "--out", str(tmp_path / "o.jsonl")]
        )
        assert seen["conditions"] == ["baseline", "nexus@a", "nexus@b"]
