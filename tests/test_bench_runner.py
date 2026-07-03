"""Tests for benchmarks.runner orchestration (no live claude subprocess).

Batch bookkeeping (incremental writes, per-run error capture) is exercised by
monkeypatching run_once. run_once itself is exercised directly by
monkeypatching subprocess.Popen with a fake that never spawns a real process.
"""

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks import runner

FIXTURES = Path(__file__).parent / "fixtures" / "bench"


def _suite():
    return {
        "repo": {"name": "django", "pin": "abc123"},
        "defaults": {"max_budget_usd": 1.0, "timeout_s": 600},
        "tasks": [
            {"id": "t1", "category": "conceptual", "prompt": "q1", "ground_truth": {}},
            {"id": "t2", "category": "impact", "prompt": "q2", "ground_truth": {}},
        ],
    }


class _FakePopen:
    """Stand-in for subprocess.Popen that never spawns a real process.

    `raise_timeout_on_first_call` simulates a run that hangs past its
    timeout: the first communicate() raises TimeoutExpired (as real Popen
    does when the child is still running), matching run_once's expectation
    that it must kill the process group and call communicate() again to
    drain whatever partial output exists.
    """

    def __init__(self, stdout_text="", raise_timeout_on_first_call=False):
        self.pid = 999999
        self._stdout = stdout_text
        self._raise_timeout_on_first_call = raise_timeout_on_first_call
        self._called = False

    def communicate(self, timeout=None):
        if self._raise_timeout_on_first_call and not self._called:
            self._called = True
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=timeout)
        return (self._stdout, "")


def _task(**overrides):
    base = {
        "id": "t1",
        "prompt": "Where is the signal dispatch logic?",
        "ground_truth": {
            "relevant_files": ["django/dispatch/dispatcher.py"],
            "must_mention_files": ["django/dispatch/dispatcher.py"],
            "facts": [{"any_of": ["_live_receivers"]}],
        },
        "max_budget_usd": 1.0,
        "timeout_s": 5,
    }
    base.update(overrides)
    return base


def _repo():
    return {"name": "django", "pin": "sha1"}


class TestRunOnce:
    def test_run_once_baseline_selects_read_files(self, tmp_path, monkeypatch):
        stdout_text = (FIXTURES / "baseline_run.jsonl").read_text()
        monkeypatch.setattr(
            runner.subprocess, "Popen", lambda *a, **kw: _FakePopen(stdout_text)
        )
        record = runner.run_once(
            _task(), "baseline", _repo(), tmp_path, "claude-sonnet-5", tmp_path
        )

        assert record["task_id"] == "t1"
        assert record["condition"] == "baseline"
        assert record["timed_out"] is False
        assert sorted(record["files_touched"]) == sorted(
            ["django/dispatch/dispatcher.py", "django/dispatch/__init__.py"]
        )
        assert record["mechanical_correct"] is True

    def test_run_once_nexus_selects_surfaced_files(self, tmp_path, monkeypatch):
        stdout_text = (FIXTURES / "nexus_run.jsonl").read_text()
        monkeypatch.setattr(
            runner.subprocess, "Popen", lambda *a, **kw: _FakePopen(stdout_text)
        )
        record = runner.run_once(_task(), "nexus", _repo(), tmp_path, "claude-sonnet-5", tmp_path)

        # nexus_run.jsonl never opens dispatcher.py via Read — it's only
        # surfaced through a search result. Baseline's Read-only selection
        # would miss it entirely; the nexus condition must not.
        assert "django/dispatch/dispatcher.py" in record["files_touched"]

    def test_run_once_timeout_kills_process_group_and_scores_partial_output(
        self, tmp_path, monkeypatch
    ):
        partial_stdout = (
            '{"type": "assistant", "message": {"content": '
            '[{"type": "tool_use", "id": "c1", "name": "Grep", "input": {"pattern": "as_sql"}}], '
            '"usage": {"input_tokens": 100, "output_tokens": 10}}}\n'
        )
        fake_proc = _FakePopen(partial_stdout, raise_timeout_on_first_call=True)
        monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: fake_proc)

        killed = {}

        def fake_getpgid(pid):
            return pid

        def fake_killpg(pgid, sig):
            killed["pgid"] = pgid
            killed["sig"] = sig

        monkeypatch.setattr(runner.os, "getpgid", fake_getpgid)
        monkeypatch.setattr(runner.os, "killpg", fake_killpg)

        record = runner.run_once(
            _task(timeout_s=1), "baseline", _repo(), tmp_path, "claude-sonnet-5", tmp_path
        )

        assert record["timed_out"] is True
        assert killed["pgid"] == fake_proc.pid
        # Partial output (the Grep call) was still parsed and scored, not discarded.
        assert record["search_call_count"] == 1
        assert record["total_tokens"] is None  # no result event in a killed run

    def test_run_once_timeout_process_already_gone_does_not_raise(self, tmp_path, monkeypatch):
        fake_proc = _FakePopen("", raise_timeout_on_first_call=True)
        monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: fake_proc)
        monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid)

        def raise_lookup_error(pgid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(runner.os, "killpg", raise_lookup_error)

        record = runner.run_once(
            _task(timeout_s=1), "baseline", _repo(), tmp_path, "claude-sonnet-5", tmp_path
        )
        assert record["timed_out"] is True


def test_write_record_appends_jsonl(tmp_path):
    out = tmp_path / "runs.jsonl"
    runner.write_record({"task_id": "t1"}, out)
    runner.write_record({"task_id": "t2"}, out)
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "t1"


def test_write_record_creates_parent_dir(tmp_path):
    out = tmp_path / "nested" / "runs.jsonl"
    runner.write_record({"x": 1}, out)
    assert out.exists()


def test_run_suite_writes_each_record_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "repo_dir_for", lambda suite: tmp_path)

    def fake_run_once(task, condition, repo, repo_dir, model, config_dir):
        return {"task_id": task["id"], "condition": condition, "ok": True}

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    out = tmp_path / "runs.jsonl"
    records = runner.run_suite(
        _suite(), ["baseline"], 1, "claude-sonnet-5", tmp_path, out
    )
    assert len(records) == 2
    # Written incrementally, so the file has every record even though we only
    # inspect it after the call.
    assert len(out.read_text().strip().splitlines()) == 2


def test_run_suite_captures_run_error_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "repo_dir_for", lambda suite: tmp_path)

    def flaky_run_once(task, condition, repo, repo_dir, model, config_dir):
        if task["id"] == "t1":
            raise RuntimeError("claude blew up")
        return {"task_id": task["id"], "condition": condition, "ok": True}

    monkeypatch.setattr(runner, "run_once", flaky_run_once)
    out = tmp_path / "runs.jsonl"
    records = runner.run_suite(
        _suite(), ["baseline"], 1, "claude-sonnet-5", tmp_path, out
    )

    # The failing run must not lose the successful one.
    assert len(records) == 2
    by_id = {r["task_id"]: r for r in records}
    assert by_id["t1"]["is_error"] is True
    assert "claude blew up" in by_id["t1"]["run_error"]
    assert by_id["t2"]["ok"] is True
    # Both were persisted, including the error record.
    assert len(out.read_text().strip().splitlines()) == 2


def test_run_suite_missing_repo_raises_systemexit(tmp_path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setattr(runner, "repo_dir_for", lambda suite: missing)
    with pytest.raises(SystemExit):
        runner.run_suite(_suite(), ["baseline"], 1, "m", tmp_path, tmp_path / "o.jsonl")


def test_load_task_suite_parses_yaml(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        "repo:\n  name: django\n  pin: abc\ntasks:\n  - id: t1\n    prompt: q\n"
    )
    suite = runner.load_task_suite(suite_path)
    assert suite["repo"]["name"] == "django"
    assert suite["tasks"][0]["id"] == "t1"


def test_repo_dir_for_joins_repo_name():
    suite = {"repo": {"name": "home-assistant-core"}}
    assert runner.repo_dir_for(suite) == runner.REPOS_DIR / "home-assistant-core"


class TestMain:
    def test_main_smoke_limits_tasks_and_reps(self, tmp_path, monkeypatch):
        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            "repo:\n  name: django\n  pin: abc\n"
            "tasks:\n  - id: t1\n    prompt: a\n  - id: t2\n    prompt: b\n"
            "  - id: t3\n    prompt: c\n"
        )
        captured = {}

        def fake_run_suite(suite, condition_names, reps, model, config_dir, out_path, task_ids):
            captured["reps"] = reps
            captured["task_ids"] = task_ids
            captured["condition_names"] = condition_names
            return []

        monkeypatch.setattr(runner, "run_suite", fake_run_suite)
        rc = runner.main(["--tasks", str(suite_path), "--smoke"])

        assert rc == 0
        assert captured["reps"] == 1
        assert captured["task_ids"] == ["t1", "t2"]

    def test_main_parses_comma_separated_conditions(self, tmp_path, monkeypatch):
        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            "repo:\n  name: django\n  pin: abc\ntasks:\n  - id: t1\n    prompt: a\n"
        )
        captured = {}

        def fake_run_suite(suite, condition_names, reps, model, config_dir, out_path, task_ids):
            captured["condition_names"] = condition_names
            return []

        monkeypatch.setattr(runner, "run_suite", fake_run_suite)
        runner.main(["--tasks", str(suite_path), "--conditions", "baseline, nexus"])

        assert captured["condition_names"] == ["baseline", "nexus"]

    def test_main_writes_to_explicit_out_path(self, tmp_path, monkeypatch):
        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            "repo:\n  name: django\n  pin: abc\ntasks:\n  - id: t1\n    prompt: a\n"
        )
        out_path = tmp_path / "custom.jsonl"

        def fake_run_suite(suite, condition_names, reps, model, config_dir, out, task_ids):
            assert out == out_path
            return [{"task_id": "t1"}]

        monkeypatch.setattr(runner, "run_suite", fake_run_suite)
        rc = runner.main(["--tasks", str(suite_path), "--out", str(out_path)])
        assert rc == 0
