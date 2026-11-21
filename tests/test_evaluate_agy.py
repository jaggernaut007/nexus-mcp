"""The agy (Antigravity CLI) runner: argv, settings, the MCP switch and the stream parser.

No real agy runs here. The stream below was recorded from `agy -p --output-format
stream-json`; `runner` stands in for the process, which is the only boundary.
"""

import json

from nexus_mcp.evaluate import runners, specs

AGY_LINES = [
    '{"event":"init","init":{"model":"m","tools":["call_mcp_tool"]}}',
    '{"event":"step_update","step_update":{"step_index":1,"state":"DONE",'
    '"step_type":"agent_response","usage":{"input_tokens":11853,"output_tokens":40}}}',
    '{"event":"step_update","step_update":{"step_index":2,"state":"ACTIVE","step_type":"tool",'
    '"tool_name":"call_mcp_tool","tool_info":{"parameters":{"ToolName":"search"}}}}',
    '{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"tool",'
    '"tool_name":"call_mcp_tool","tool_info":{"parameters":{"ToolName":"search"}}}}',
    '{"event":"step_update","step_update":{"step_index":3,"state":"DONE","step_type":"tool",'
    '"tool_name":"view_file","tool_info":{"parameters":{}}}}',
    "jetski: a plain log line",
    '{"event":"result","result":{"status":"SUCCESS","response":"src/a.py","num_turns":1,'
    '"usage":{"input_tokens":14012,"output_tokens":78,"thinking_tokens":10,'
    '"cache_read_tokens":9802}}}',
]


def test_agy_is_a_known_runner_with_a_default_model():
    parsed = specs.parse_specs("agy")
    assert [s.label for s in parsed] == ["agy:gemini-3.8-flash-medium"]
    assert specs.run_estimate(parsed[0]) == specs.AGY_RUN_ESTIMATE_USD


def test_agy_argv_is_plan_mode_with_a_settings_file(tmp_path):
    argv = runners.agy_argv("gemini-x", "mcp", "the question", tmp_path / "s.json")
    assert argv[1:3] == ["-p", "the question"]
    assert argv[argv.index("--mode") + 1] == "plan"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "gemini-x"
    assert argv[argv.index("--settings") + 1] == str(tmp_path / "s.json")
    assert "--dangerously-skip-permissions" not in argv


def test_agy_argv_nexus_puts_the_skill_before_the_prompt(tmp_path):
    argv = runners.agy_argv("m", "nexus", "the question", tmp_path / "s.json")
    assert argv[2].endswith("the question") and len(argv[2]) > len("the question")
    assert runners.agy_argv("m", "baseline", "q", tmp_path / "s.json")[2] == "q"


def test_agy_settings_allow_only_the_nexus_tools_when_mcp_is_on():
    on = runners.agy_settings("nexus")["permissions"]["allow"]
    assert "mcp(nexus-mcp/search)" in on
    assert all(rule.startswith("mcp(nexus-mcp/") for rule in on)
    assert runners.agy_settings("baseline")["permissions"]["allow"] == []


def test_parse_agy_reads_answer_tokens_and_tool_names():
    out = runners.parse_agy(AGY_LINES)
    assert out["answer"] == "src/a.py"
    assert out["tokens"] == 14012 + 78 + 10
    assert out["cost_usd"] is None  # agy reports tokens, not USD
    assert out["tools"] == {"mcp:search": 1, "view_file": 1}
    assert out["tool_calls"] == 2
    assert out["is_error"] is False


def test_parse_agy_without_a_result_event_is_an_error():
    out = runners.parse_agy(AGY_LINES[:4])
    assert out["is_error"] is True and out["error"] == "no result event"


def test_parse_agy_a_failed_run_reports_the_error():
    line = json.dumps(
        {
            "event": "result",
            "result": {
                "status": "ERROR",
                "response": "",
                "error": "interrupted",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        }
    )
    out = runners.parse_agy([line])
    assert out["is_error"] is True and out["error"] == "interrupted" and out["tokens"] == 6


def test_agy_mcp_toggle_enables_for_nexus_and_disables_for_baseline():
    calls = []

    class Done:
        returncode = 0

    def fake(argv, **kw):
        calls.append(argv[1:])
        return Done()

    assert runners.agy_mcp_toggle("nexus", runner=fake) is True
    assert runners.agy_mcp_toggle("baseline", runner=fake) is True
    assert calls == [["mcp", "enable", "nexus-mcp"], ["mcp", "disable", "nexus-mcp"]]


def test_agy_mcp_toggle_is_false_when_agy_is_missing():
    def boom(*a, **k):
        raise FileNotFoundError("agy")

    assert runners.agy_mcp_toggle("nexus", runner=boom) is False


def test_run_agy_passes_the_index_in_the_env_and_restores_the_server(tmp_path, monkeypatch):
    seen = []

    class Done:
        returncode, stderr = 0, ""
        stdout = "\n".join(AGY_LINES)

    def fake(argv, **kw):
        seen.append(kw.get("env"))
        return Done()

    toggles = []
    monkeypatch.setattr(
        runners, "agy_mcp_toggle", lambda cond, runner=None: toggles.append(cond) or True
    )
    spec = specs.Spec("agy", "m")
    out = runners.run(spec, "baseline", "q", tmp_path, tmp_path / "idx", runner=fake)
    assert out["answer"] == "src/a.py" and out["isolation"] == "shared-config"
    assert toggles == ["baseline", "nexus"]  # off for the run, on again after
    assert seen[0]["NEXUS_STORAGE_DIR"] == str(tmp_path / "idx")


def test_run_agy_reports_an_error_when_the_server_cannot_be_switched(tmp_path, monkeypatch):
    monkeypatch.setattr(runners, "agy_mcp_toggle", lambda cond, runner=None: False)

    def never(*a, **k):
        raise AssertionError("agy must not start")

    out = runners.run(specs.Spec("agy", "m"), "nexus", "q", tmp_path, tmp_path, runner=never)
    assert out["is_error"] is True and "agy mcp add" in out["error"]
