"""Tests for benchmarks.runner orchestration (no live claude subprocess).

These exercise the batch bookkeeping — incremental writes and per-run error
capture — by monkeypatching run_once, so no real CLI is spawned.
"""

import json

import pytest

from benchmarks import runner


def _suite():
    return {
        "repo": {"name": "django", "pin": "abc123"},
        "defaults": {"max_budget_usd": 1.0, "timeout_s": 600},
        "tasks": [
            {"id": "t1", "category": "conceptual", "prompt": "q1", "ground_truth": {}},
            {"id": "t2", "category": "impact", "prompt": "q2", "ground_truth": {}},
        ],
    }


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
