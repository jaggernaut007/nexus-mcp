"""Tests for evals.routing: scoring, runner bookkeeping and report (no live claude call)."""

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks import transcript as tx
from benchmarks.transcript import ToolCall
from evals.routing import report, runner, scoring


def _call(name, **inp):
    return ToolCall(id=f"id-{name}", name=name, input=inp)


class TestNormalize:
    def test_normalize_tool_strips_server_prefix(self):
        assert scoring.normalize_tool("mcp__nexus-mcp__search") == "search"

    def test_normalize_tool_handles_plugin_prefix(self):
        assert scoring.normalize_tool("mcp__plugin_nexus-mcp_nexus-mcp__graph") == "graph"

    def test_normalize_tool_leaves_builtin_alone(self):
        assert scoring.normalize_tool("Grep") == "Grep"

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("mcp__nexus-mcp__search", True),
            ("mcp__nexus__search", True),
            ("mcp__plugin_nexus-mcp_nexus-mcp__map", True),
            ("mcp__other__search", False),
            ("Grep", False),
        ],
    )
    def test_is_nexus_tool(self, name, expected):
        assert scoring.is_nexus_tool(name) is expected


class TestArgsMatch:
    def test_args_match_none_expected_is_true(self):
        assert scoring.args_match({"a": 1}, None) is True

    def test_args_match_ignores_case_and_type(self):
        assert scoring.args_match({"transitive": True}, {"transitive": "true"}) is True

    def test_args_match_missing_key_is_false(self):
        assert scoring.args_match({}, {"direction": "callers"}) is False

    def test_args_match_wrong_value_is_false(self):
        assert scoring.args_match({"direction": "callers"}, {"direction": "callees"}) is False


class TestToolDefaults:
    def test_args_match_uses_the_tool_default_for_an_omitted_argument(self):
        assert scoring.args_match({}, {"direction": "callers"}, {"direction": "callers"}) is True

    def test_args_match_explicit_argument_beats_the_default(self):
        got = scoring.args_match(
            {"direction": "callees"}, {"direction": "callers"}, {"direction": "callers"}
        )
        assert got is False

    def test_score_prompt_graph_without_direction_passes_for_callers(self):
        spec = {"id": "g", "category": "graph", "expected_any_of": ["graph"],
                "expected_args": {"direction": "callers"}}
        calls = [_call("mcp__nexus-mcp__graph", symbol_name="reserve_stock")]
        assert scoring.score_prompt(spec, calls)["passed"] is True

    def test_score_prompt_graph_without_transitive_fails_a_blast_radius_prompt(self):
        spec = {"id": "g", "category": "graph", "expected_any_of": ["graph"],
                "expected_args": {"transitive": "true"}}
        calls = [_call("mcp__nexus-mcp__graph", symbol_name="check_stock")]
        assert scoring.score_prompt(spec, calls)["passed"] is False


class TestScorePrompt:
    SPEC = {"id": "x", "category": "graph", "expected_any_of": ["graph"],
            "expected_args": {"direction": "callers"}}

    def test_score_prompt_right_tool_first_passes(self):
        calls = [_call("mcp__nexus-mcp__graph", direction="callers", symbol_name="f")]
        result = scoring.score_prompt(self.SPEC, calls)
        assert result["passed"] is True
        assert result["first_is_nexus"] is True
        assert result["calls_before_nexus"] == 0

    def test_score_prompt_tool_search_is_neutral(self):
        calls = [_call("ToolSearch", query="graph"),
                 _call("mcp__nexus-mcp__graph", direction="callers")]
        result = scoring.score_prompt(self.SPEC, calls)
        assert result["passed"] is True
        assert result["used_tool_search"] is True
        assert result["first_tool"] == "graph"

    def test_score_prompt_status_then_tool_inside_window_passes(self):
        calls = [_call("mcp__nexus-mcp__status"), _call("mcp__nexus-mcp__index"),
                 _call("mcp__nexus-mcp__graph", direction="callers")]
        assert scoring.score_prompt(self.SPEC, calls)["passed"] is True

    def test_score_prompt_tool_outside_window_fails(self):
        calls = [_call("Grep"), _call("Grep"), _call("Read"),
                 _call("mcp__nexus-mcp__graph", direction="callers")]
        result = scoring.score_prompt(self.SPEC, calls)
        assert result["passed"] is False
        assert result["right_tool"] is False

    def test_score_prompt_wrong_args_fails(self):
        calls = [_call("mcp__nexus-mcp__graph", direction="callees")]
        result = scoring.score_prompt(self.SPEC, calls)
        assert result["right_tool"] is True
        assert result["args_ok"] is False
        assert result["passed"] is False

    def test_score_prompt_native_first_counts_calls_before_nexus(self):
        calls = [_call("Grep"), _call("mcp__nexus-mcp__graph", direction="callers")]
        result = scoring.score_prompt(self.SPEC, calls)
        assert result["calls_before_nexus"] == 1
        assert result["first_is_nexus"] is False

    def test_score_prompt_no_calls(self):
        result = scoring.score_prompt(self.SPEC, [])
        assert result["passed"] is False
        assert result["first_tool"] is None
        assert result["calls_before_nexus"] is None

    def test_score_prompt_negative_native_passes_on_read(self):
        spec = {"id": "n", "category": "negative", "expect": "native"}
        assert scoring.score_prompt(spec, [_call("Read", file_path="a")])["passed"] is True

    def test_score_prompt_negative_native_fails_on_nexus(self):
        spec = {"id": "n", "category": "negative", "expect": "native"}
        assert scoring.score_prompt(spec, [_call("mcp__nexus-mcp__search")])["passed"] is False

    def test_score_prompt_negative_none_passes_without_calls(self):
        spec = {"id": "n", "category": "negative", "expect": "none"}
        assert scoring.score_prompt(spec, [])["passed"] is True
        assert scoring.score_prompt(spec, [_call("Read")])["passed"] is False


class TestAggregate:
    def test_aggregate_splits_negative_from_positive(self):
        records = [
            {"category": "graph", "passed": True, "first_is_nexus": True,
             "right_tool": True, "args_ok": True},
            {"category": "graph", "passed": False, "first_is_nexus": False,
             "right_tool": False, "args_ok": False},
            {"category": "negative", "passed": True},
        ]
        agg = scoring.aggregate(records)
        assert agg["n"] == 3
        assert agg["pass_rate"] == pytest.approx(2 / 3)
        assert agg["right_tool_rate"] == 0.5
        assert agg["negative_pass_rate"] == 1.0

    def test_aggregate_empty_returns_none_rates(self):
        agg = scoring.aggregate([])
        assert agg["n"] == 0
        assert agg["pass_rate"] is None

    def test_aggregate_skips_error_records(self):
        agg = scoring.aggregate([{"category": "graph", "run_error": "boom"}])
        assert agg["n"] == 0


class TestPrompts:
    def test_load_prompts_ids_unique_and_valid(self):
        specs = runner.load_prompts()
        ids = [s["id"] for s in specs]
        assert len(ids) == len(set(ids))
        for spec in specs:
            assert spec["category"] in {
                "search", "symbol", "graph", "map", "analyze", "memory", "negative"
            }
            if spec["category"] == "negative":
                assert spec["expect"] in {"native", "none"}
            else:
                assert spec["expected_any_of"]

    def test_load_prompts_expected_tools_exist(self):
        known = {"index", "status", "health", "search", "find_symbol", "graph",
                 "explain", "analyze", "map", "memory"}
        for spec in runner.load_prompts():
            assert set(spec.get("expected_any_of", [])) <= known

    def test_fixture_repo_has_python_and_typescript(self):
        assert (runner.FIXTURE_DIR / "shop" / "orders.py").exists()
        assert (runner.FIXTURE_DIR / "web" / "src" / "cart.ts").exists()


class TestRunnerHelpers:
    def test_write_mcp_config_points_at_checkout_source(self, tmp_path):
        path = runner.write_mcp_config(tmp_path / "mcp.json", "/py", Path("/repo/src"))
        server = json.loads(path.read_text())["mcpServers"]["nexus-mcp"]
        assert server["command"] == "/py"
        assert server["env"]["PYTHONPATH"] == "/repo/src"

    def test_done_keys_reads_existing_records(self, tmp_path):
        out = tmp_path / "r.jsonl"
        runner.write_record({"prompt_id": "a", "condition": "nexus", "tool_search": True,
                             "rep": 0}, out)
        runner.write_record({"prompt_id": "b", "condition": "nexus", "tool_search": True,
                             "rep": 0, "run_error": "boom"}, out)
        assert runner.done_keys(out) == {("a", "nexus", True, 0)}

    def test_done_keys_missing_file_is_empty(self, tmp_path):
        assert runner.done_keys(tmp_path / "none.jsonl") == set()

    def test_looks_like_usage_limit_true(self):
        trace = tx.RunTrace(is_error=True, final_answer="Claude usage limit reached")
        assert runner.looks_like_usage_limit(trace) is True

    def test_looks_like_usage_limit_false_without_error(self):
        trace = tx.RunTrace(is_error=False, final_answer="usage limit")
        assert runner.looks_like_usage_limit(trace) is False

    def test_isolation_problems_flags_extra_server_and_plugins(self):
        trace = tx.RunTrace(
            init_event={"plugins": ["x"]},
            mcp_servers=[{"name": "nexus-mcp", "status": "connected"},
                         {"name": "other", "status": "connected"}],
        )
        problems = runner.isolation_problems(trace, "nexus")
        assert any("plugins" in p for p in problems)
        assert any("unexpected MCP servers" in p for p in problems)

    def test_isolation_problems_ignores_the_builtin_telemetry_plugin(self):
        trace = tx.RunTrace(
            init_event={"plugins": [{"name": "telemetry", "path": "builtin",
                                     "source": "telemetry@builtin"}]},
            mcp_servers=[{"name": "nexus-mcp", "status": "connected"}],
        )
        assert runner.isolation_problems(trace, "mcp-only") == []

    def test_isolation_problems_flags_a_user_plugin(self):
        trace = tx.RunTrace(
            init_event={"plugins": [{"name": "mine", "path": "/home/u/plugin"}]},
            mcp_servers=[{"name": "nexus-mcp", "status": "connected"}],
        )
        assert any("plugins" in p for p in runner.isolation_problems(trace, "mcp-only"))

    def test_isolation_problems_clean_run(self):
        trace = tx.RunTrace(init_event={"type": "system"},
                            mcp_servers=[{"name": "nexus-mcp", "status": "connected"}])
        assert runner.isolation_problems(trace, "nexus") == []

    def test_isolation_problems_ignores_non_dict_server_entries(self):
        trace = tx.RunTrace(init_event={"type": "system"},
                            mcp_servers=["nexus-mcp", {"name": "nexus-mcp", "status": "connected"}])
        assert runner.isolation_problems(trace, "nexus") == []

    def test_isolation_problems_baseline_expects_no_server(self):
        clean = tx.RunTrace(init_event={"type": "system"}, mcp_servers=[])
        assert runner.isolation_problems(clean, "baseline") == []
        leaked = tx.RunTrace(init_event={"type": "system"},
                             mcp_servers=[{"name": "nexus-mcp", "status": "connected"}])
        assert runner.isolation_problems(leaked, "baseline")

    def test_isolation_problems_plugin_condition_accepts_the_plugin_server_name(self):
        trace = tx.RunTrace(
            init_event={"plugins": ["nexus-mcp"]},
            mcp_servers=[{"name": "plugin:nexus-mcp:nexus-mcp", "status": "connected"}],
        )
        assert runner.isolation_problems(trace, "nexus-plugin") == []

    def test_isolation_problems_disconnected_server(self):
        trace = tx.RunTrace(init_event={"type": "system"},
                            mcp_servers=[{"name": "nexus-mcp", "status": "failed"}])
        assert any("status" in p for p in runner.isolation_problems(trace, "nexus"))

    def test_done_keys_skips_runs_with_isolation_problems(self, tmp_path):
        out = tmp_path / "r.jsonl"
        runner.write_record({"prompt_id": "a", "condition": "nexus", "tool_search": True,
                             "rep": 0, "isolation_problems": ["server failed"]}, out)
        assert runner.done_keys(out) == set()

    def test_existing_model_reads_the_first_record(self, tmp_path):
        out = tmp_path / "r.jsonl"
        assert runner.existing_model(out) is None
        runner.write_record({"prompt_id": "a", "model": "sonnet"}, out)
        assert runner.existing_model(out) == "sonnet"

    def test_isolation_problems_without_init_event(self):
        assert runner.isolation_problems(tx.RunTrace(), "nexus") == ["no system/init event"]


class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.pid = 999999

    def wait(self):
        return 0


def _tool_event(name):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "i", "name": name, "input": {}}]}}) + "\n"


class TestStreamRun:
    def test_stream_run_stops_after_max_effective_calls(self, monkeypatch):
        lines = [_tool_event("ToolSearch"), _tool_event("mcp__nexus-mcp__status"),
                 _tool_event("Grep"), _tool_event("Read"), _tool_event("Read")]
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
        monkeypatch.setattr(runner.os, "killpg", lambda *a, **k: None)
        monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid)
        got, timed_out, early, _err = runner.stream_run(["claude"], {}, Path("."), 30, 3)
        assert early is True
        assert timed_out is False
        assert len(got) == 4  # ToolSearch does not count; stops at the third effective call

    def test_stream_run_reads_all_lines_when_under_limit(self, monkeypatch):
        lines = [_tool_event("Read")]
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
        monkeypatch.setattr(runner.os, "killpg", lambda *a, **k: None)
        monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid)
        got, _timed_out, early, _err = runner.stream_run(["claude"], {}, Path("."), 30, 3)
        assert early is False
        assert len(got) == 1


class TestStderrTail:
    def test_stream_run_returns_the_stderr_of_a_child_that_dies(self, tmp_path):
        import sys as _sys

        code = "import sys; sys.stderr.write('Error: bad flag'); sys.exit(1)"
        lines, timed_out, early, tail = runner.stream_run(
            [_sys.executable, "-c", code], {}, tmp_path, 30, 3
        )
        assert lines == [] and not timed_out and not early
        assert "bad flag" in tail

    def test_a_run_that_died_before_init_keeps_its_stderr_in_the_record(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            runner, "stream_run", lambda *a, **k: ([], False, False, "Error: Not logged in")
        )
        spec = {"id": "x", "category": "map", "prompt": "q", "expected_any_of": ["map"]}
        record = runner.run_once(spec, "mcp-only", True, tmp_path, tmp_path / "m.json",
                                 "sonnet", "2.1.280")
        assert record["stderr_tail"] == "Error: Not logged in"
        assert record["isolation_problems"] == ["no system/init event"]


class TestRunSuite:
    SPECS = [{"id": "a", "category": "map"}, {"id": "b", "category": "map"}]

    def test_run_suite_writes_a_record_for_each_combination(self, tmp_path):
        out = tmp_path / "o.jsonl"
        calls = []

        def run_one(spec, condition, tool_search):
            calls.append((spec["id"], condition, tool_search))
            return {"prompt_id": spec["id"], "category": spec["category"],
                    "condition": condition, "tool_search": tool_search, "passed": True}

        written = runner.run_suite(self.SPECS, ["nexus"], [True, False], 1, out, run_one)
        assert written == 4
        assert len(calls) == 4

    def test_run_suite_resumes_and_skips_done_runs(self, tmp_path):
        out = tmp_path / "o.jsonl"
        runner.write_record({"prompt_id": "a", "condition": "nexus", "tool_search": True,
                             "rep": 0}, out)
        seen = []

        def run_one(spec, condition, tool_search):
            seen.append(spec["id"])
            return {"prompt_id": spec["id"], "category": "map", "condition": condition,
                    "tool_search": tool_search}

        runner.run_suite(self.SPECS, ["nexus"], [True], 1, out, run_one)
        assert seen == ["b"]

    def test_run_suite_records_error_and_continues(self, tmp_path):
        out = tmp_path / "o.jsonl"

        def run_one(spec, condition, tool_search):
            if spec["id"] == "a":
                raise ValueError("bad")
            return {"prompt_id": spec["id"], "category": "map", "condition": condition,
                    "tool_search": tool_search}

        runner.run_suite(self.SPECS, ["nexus"], [True], 1, out, run_one)
        records = [json.loads(line) for line in out.read_text().splitlines()]
        assert "ValueError" in records[0]["run_error"]
        assert records[1]["prompt_id"] == "b"

    def test_run_suite_usage_limit_stops_and_keeps_earlier_records(self, tmp_path):
        out = tmp_path / "o.jsonl"

        def run_one(spec, condition, tool_search):
            if spec["id"] == "b":
                raise runner.UsageLimitReached("limit")
            return {"prompt_id": spec["id"], "category": "map", "condition": condition,
                    "tool_search": tool_search}

        with pytest.raises(runner.UsageLimitReached):
            runner.run_suite(self.SPECS, ["nexus"], [True], 1, out, run_one)
        assert len(out.read_text().splitlines()) == 1


class TestRunOnce:
    def test_run_once_scores_a_recorded_stream(self, monkeypatch, tmp_path):
        stream = [
            json.dumps({"type": "system", "subtype": "init", "tools": ["Read"],
                        "mcp_servers": [{"name": "nexus-mcp", "status": "connected"}]}) + "\n",
            _tool_event("mcp__nexus-mcp__map"),
        ]
        monkeypatch.setattr(runner, "stream_run", lambda *a, **k: (stream, False, True, ""))
        spec = {"id": "map-overview", "category": "map", "prompt": "overview?",
                "expected_any_of": ["map"]}
        record = runner.run_once(spec, "mcp-only", True, tmp_path, tmp_path / "m.json",
                                 "sonnet", "2.1.280")
        assert record["passed"] is True
        assert record["isolation_problems"] == []
        assert record["tool_search"] is True
        assert record["claude_version"] == "2.1.280"


class TestReport:
    def test_latest_records_replaces_a_failed_attempt_with_its_retry(self):
        records = [
            {"prompt_id": "a", "condition": "nexus", "tool_search": True, "rep": 0,
             "run_error": "boom"},
            {"prompt_id": "a", "condition": "nexus", "tool_search": True, "rep": 0,
             "category": "map", "passed": True},
        ]
        assert report.latest_records(records) == [records[1]]

    def test_render_markdown_leaves_out_runs_with_isolation_problems(self):
        records = [
            {"prompt_id": "a", "condition": "nexus", "tool_search": True, "rep": 0,
             "category": "map", "passed": False, "isolation_problems": ["server failed"]},
        ]
        text = report.render_markdown(records)
        assert "Runs left out (errors or isolation problems): 1" in text

    def test_render_markdown_has_condition_and_category_tables(self):
        records = [
            {"prompt_id": "a", "rep": 0, "condition": "nexus", "tool_search": True,
             "category": "map", "passed": True,
             "first_is_nexus": True, "right_tool": True, "args_ok": True},
            {"prompt_id": "b", "rep": 0, "condition": "nexus", "tool_search": True,
             "category": "negative", "passed": True},
        ]
        text = report.render_markdown(records)
        assert "| nexus | on | 2 | 100%" in text
        assert "| map | 1 | 100% |" in text

    def test_main_returns_one_without_records(self, tmp_path, capsys):
        assert report.main([str(tmp_path / "none.jsonl")]) == 1
