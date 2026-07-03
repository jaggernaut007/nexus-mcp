"""Tests for benchmarks.transcript: stream-json event parsing -> RunTrace."""

from pathlib import Path

from benchmarks.transcript import parse_lines, parse_stream

FIXTURES = Path(__file__).parent / "fixtures" / "bench"


def _load_lines(name):
    return (FIXTURES / name).read_text().splitlines()


def test_parse_lines_baseline_extracts_read_files():
    trace = parse_lines(_load_lines("baseline_run.jsonl"))
    assert trace.files_read_baseline == sorted(
        ["django/dispatch/dispatcher.py", "django/dispatch/__init__.py"]
    )


def test_parse_lines_baseline_counts_grep_separately():
    trace = parse_lines(_load_lines("baseline_run.jsonl"))
    assert trace.search_call_count == 1
    assert "django/dispatch/dispatcher.py" in trace.files_read_baseline


def test_parse_lines_baseline_tool_call_counts():
    trace = parse_lines(_load_lines("baseline_run.jsonl"))
    assert trace.tool_call_counts == {"Grep": 1, "Read": 2}


def test_parse_lines_baseline_final_answer_and_usage():
    trace = parse_lines(_load_lines("baseline_run.jsonl"))
    assert "dispatcher.py" in trace.final_answer
    assert trace.usage["input_tokens"] == 850
    assert trace.total_tokens == 850 + 0 + 100 + 80
    assert trace.is_error is False
    assert trace.result_subtype == "success"


def test_parse_lines_nexus_extracts_result_filepaths():
    trace = parse_lines(_load_lines("nexus_run.jsonl"))
    assert "django/dispatch/dispatcher.py" in trace.nexus_result_files


def test_parse_lines_nexus_files_surfaced_includes_result_files():
    trace = parse_lines(_load_lines("nexus_run.jsonl"))
    assert trace.files_surfaced_nexus == ["django/dispatch/dispatcher.py"]


def test_parse_lines_nexus_mcp_tool_calls_recorded():
    trace = parse_lines(_load_lines("nexus_run.jsonl"))
    assert trace.tool_call_counts.get("mcp__nexus-mcp__search") == 1
    assert trace.tool_call_counts.get("mcp__nexus-mcp__status") == 1


def test_parse_lines_nexus_retrieval_tokens_estimated():
    trace = parse_lines(_load_lines("nexus_run.jsonl"))
    assert trace.retrieval_tokens_est > 0


def test_parse_lines_budget_capped_marks_error():
    trace = parse_lines(_load_lines("budget_capped_run.jsonl"))
    assert trace.is_error is True
    assert trace.result_subtype == "error_max_budget"
    assert trace.final_answer == ""


def test_parse_lines_malformed_lines_counted_not_raised():
    trace = parse_lines(_load_lines("malformed_lines.jsonl"))
    assert trace.parse_errors >= 1
    assert trace.files_read_baseline == ["a.py"]
    assert trace.final_answer == "ok"


def test_parse_lines_blank_lines_skipped_silently():
    trace = parse_lines(["", "   ", ""])
    assert trace.parse_errors == 0
    assert trace.tool_calls == []


def test_parse_stream_unknown_event_type_ignored():
    trace = parse_stream([{"type": "some_future_event", "data": 123}])
    assert trace.parse_errors == 0
    assert trace.tool_calls == []


def test_parse_stream_non_dict_event_counted_as_error():
    trace = parse_stream(["not-a-dict", 42])
    assert trace.parse_errors == 2


def test_parse_stream_empty_input_returns_empty_trace():
    trace = parse_stream([])
    assert trace.tool_calls == []
    assert trace.usage == {}
    assert trace.final_answer == ""


def test_total_tokens_none_when_no_result_event():
    # An assistant turn but no result event (crashed/killed mid-run): the
    # per-turn usage must NOT be reported as the cumulative total.
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "partial"}],
                "usage": {"input_tokens": 700, "output_tokens": 45},
            },
        }
    ]
    trace = parse_stream(events)
    assert trace.has_result_event is False
    assert trace.total_tokens is None
    assert trace.fresh_tokens is None


def test_total_tokens_present_when_result_event_seen():
    trace = parse_lines(_load_lines("baseline_run.jsonl"))
    assert trace.has_result_event is True
    assert trace.total_tokens == 850 + 0 + 100 + 80


def test_budget_capped_run_has_result_event_and_tokens():
    # A budget-capped run is still a *clean* result event (error subtype), so
    # its cumulative usage is trustworthy — distinct from no-result-at-all.
    trace = parse_lines(_load_lines("budget_capped_run.jsonl"))
    assert trace.has_result_event is True
    assert trace.total_tokens is not None


def test_result_event_usage_overrides_earlier_assistant_usage():
    # total_tokens relies on the result event's usage being *cumulative* and
    # winning over any earlier per-turn assistant usage — not the other way
    # around, and not summed together.
    events = [
        {
            "type": "assistant",
            "message": {"content": [], "usage": {"input_tokens": 100, "output_tokens": 5}},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "usage": {"input_tokens": 1950, "output_tokens": 65},
        },
    ]
    trace = parse_stream(events)
    assert trace.usage["input_tokens"] == 1950
    assert trace.total_tokens == 1950 + 65


def test_parse_stream_system_init_records_mcp_servers():
    events = [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "nexus-mcp", "status": "connected"}],
        }
    ]
    trace = parse_stream(events)
    assert trace.mcp_servers == [{"name": "nexus-mcp", "status": "connected"}]


def test_parse_stream_system_non_init_subtype_ignored():
    events = [{"type": "system", "subtype": "warning", "mcp_servers": [{"name": "x"}]}]
    trace = parse_stream(events)
    assert trace.mcp_servers == []


def test_parse_stream_system_init_non_list_mcp_servers_ignored():
    events = [{"type": "system", "subtype": "init", "mcp_servers": "not-a-list"}]
    trace = parse_stream(events)
    assert trace.mcp_servers == []


def test_files_surfaced_nexus_dedupes_read_and_result_overlap():
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "Read", "input": {"file_path": "x.py"}}
                ],
                "usage": {},
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "c2",
                        "name": "mcp__nexus-mcp__search",
                        "input": {"query": "x"},
                    }
                ],
                "usage": {},
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c2",
                        "content": '{"results": [{"filepath": "x.py"}]}',
                    }
                ]
            },
        },
    ]
    trace = parse_stream(events)
    assert trace.files_surfaced_nexus == ["x.py"]


def test_tool_result_with_non_json_content_does_not_raise():
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "mcp__nexus-mcp__search", "input": {}}
                ],
                "usage": {},
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": "error: index not found",
                    }
                ]
            },
        },
    ]
    trace = parse_stream(events)
    assert trace.nexus_result_files == []
    assert trace.retrieval_tokens_est > 0
