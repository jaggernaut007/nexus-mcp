"""Tests for benchmarks.rescore: rescoring recorded runs from their stored answers."""

import json

import pytest
import yaml

from benchmarks import rescore, scoring

SUITE = {
    "repo": {"name": "demo"},
    "tasks": [{
        "id": "t-flow",
        "ground_truth": {
            "relevant_files": ["src/pkg/gateway/gateway.py"],
            "must_mention_files": ["src/pkg/gateway/gateway.py"],
            "facts": [{"any_of": ["cache"]}],
        },
    }],
}


def _record(answer, **extra):
    """A record scored by the first scorer: the full path was required."""
    old = scoring.score_run([], answer, SUITE["tasks"][0]["ground_truth"])
    old["file_recall"] = 1.0 if "src/pkg/gateway/gateway.py" in answer else 0.0
    old["combined"] = 0.5 * old["file_recall"] + 0.5 * old["fact_score"]
    old["mechanical_correct"] = old["combined"] >= scoring.MECHANICAL_PASS_THRESHOLD
    return {"task_id": "t-flow", "condition": "baseline", "final_answer": answer,
            "files_touched": [], **old, **extra}


class TestRescoreRecord:
    def test_rescore_record_a_short_path_now_passes(self):
        record = _record("`gateway.py` checks the cache first")
        assert record["mechanical_correct"] is False
        new = rescore.rescore_record(record, SUITE["tasks"][0])
        assert new["file_recall"] == 1.0
        assert new["mechanical_correct"] is True
        assert new["mechanical_correct_before"] is False

    def test_rescore_record_does_not_change_its_input(self):
        record = _record("`gateway.py` checks the cache first")
        before = json.dumps(record, sort_keys=True)
        rescore.rescore_record(record, SUITE["tasks"][0])
        assert json.dumps(record, sort_keys=True) == before

    def test_rescore_record_an_unchanged_score_gets_no_before_field(self):
        record = _record("src/pkg/gateway/gateway.py caches results in the cache")
        new = rescore.rescore_record(record, SUITE["tasks"][0])
        assert "mechanical_correct_before" not in new
        assert new["mechanical_correct"] is True

    def test_rescore_record_a_wrong_answer_still_fails(self):
        record = _record("it is all in selector.py and the ledger")
        new = rescore.rescore_record(record, SUITE["tasks"][0])
        assert new["mechanical_correct"] is False

    def test_rescore_record_without_an_answer_is_unchanged(self):
        record = {"task_id": "t-flow", "condition": "baseline", "final_answer": "",
                  "run_error": "timeout"}
        assert rescore.rescore_record(record, SUITE["tasks"][0]) == record


class TestRescoreAll:
    def test_rescore_all_reports_only_the_changed_verdicts(self):
        records = [
            _record("`gateway.py` checks the cache first"),
            _record("src/pkg/gateway/gateway.py caches results in the cache"),
            _record("nothing useful"),
        ]
        out, flipped = rescore.rescore_all(records, SUITE)
        assert [r["mechanical_correct"] for r in out] == [True, True, False]
        assert len(flipped) == 1 and flipped[0]["final_answer"].startswith("`gateway.py`")

    def test_rescore_all_unknown_task_raises(self):
        with pytest.raises(KeyError):
            rescore.rescore_all([{**_record("x"), "task_id": "other"}], SUITE)


class TestMain:
    def _files(self, tmp_path, records):
        tasks = tmp_path / "suite.yaml"
        tasks.write_text(yaml.safe_dump(SUITE))
        runs = tmp_path / "runs.jsonl"
        runs.write_text("".join(json.dumps(r) + "\n" for r in records))
        return tasks, runs

    def test_main_writes_a_new_file_and_leaves_the_input_alone(self, tmp_path, capsys):
        tasks, runs = self._files(tmp_path, [_record("`gateway.py` checks the cache first")])
        original = runs.read_text()
        assert rescore.main(["--tasks", str(tasks), "--runs", str(runs)]) == 0
        assert runs.read_text() == original
        out = tmp_path / "runs.rescored.jsonl"
        assert json.loads(out.read_text())["mechanical_correct"] is True
        assert "1 changed verdict" in capsys.readouterr().out

    def test_main_refuses_to_overwrite_the_input(self, tmp_path):
        tasks, runs = self._files(tmp_path, [_record("x")])
        with pytest.raises(SystemExit):
            rescore.main(["--tasks", str(tasks), "--runs", str(runs), "--out", str(runs)])
