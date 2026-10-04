"""Pure scoring for the routing eval: did the model pick the right nexus tool?

No I/O here. The runner feeds in a `RunTrace` (see benchmarks.transcript) and a
prompt spec from prompts.yaml. Tool names are normalised so the result does not
depend on how the server was registered: `mcp__nexus-mcp__search` and
`mcp__plugin_nexus-mcp_nexus-mcp__search` both become `search`.
"""

from typing import Any, Dict, List, Optional

from benchmarks.transcript import MCP_TOOL_PREFIX, READ_TOOL, SEARCH_TOOLS, ToolCall

NEUTRAL_TOOLS = ("ToolSearch",)
# Session set-up calls. An agent that checks `status` and runs `index` before the real
# work is doing what the server instructions say, so they do not use up the window.
BOOTSTRAP_TOOLS = frozenset({"status", "index", "health"})
MAX_TOTAL_CALLS = 8  # hard stop for a run that keeps setting up or wandering
NATIVE_TOOLS = (READ_TOOL,) + SEARCH_TOOLS
WINDOW = 3  # the expected tool must appear within this many effective calls


def is_nexus_tool(name: str) -> bool:
    """True for an MCP tool that belongs to a nexus server, under any prefix."""
    if not name.startswith(MCP_TOOL_PREFIX):
        return False
    server = name[len(MCP_TOOL_PREFIX):].split("__", 1)[0]
    return "nexus" in server


def normalize_tool(name: str) -> str:
    """Strip the MCP prefix: `mcp__nexus-mcp__graph` -> `graph`."""
    if name.startswith(MCP_TOOL_PREFIX):
        return name.rsplit("__", 1)[-1]
    return name


def is_neutral(name: str) -> bool:
    """True for calls that do not count: tool discovery and nexus session set-up."""
    if name in NEUTRAL_TOOLS:
        return True
    return is_nexus_tool(name) and normalize_tool(name) in BOOTSTRAP_TOOLS


def effective_calls(calls: List[ToolCall]) -> List[ToolCall]:
    """Tool calls without discovery (ToolSearch) and nexus set-up (status, index, health)."""
    return [c for c in calls if not is_neutral(c.name)]


# Server-side defaults of the nexus tools. A model that omits `direction` gets
# "callers", so an omitted argument counts as that default.
TOOL_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "graph": {"direction": "callers", "transitive": False},
    "map": {"detail": "summary"},
}


def args_match(
    call_input: Dict[str, Any],
    expected: Optional[Dict[str, Any]],
    defaults: Optional[Dict[str, Any]] = None,
) -> bool:
    """Every expected key must equal the call's argument, ignoring case and type.

    A key that the call omits is read from `defaults` (the tool's server default).
    """
    if not expected:
        return True
    defaults = defaults or {}
    for key, want in expected.items():
        got = call_input.get(key, defaults.get(key))
        if got is None or str(got).strip().lower() != str(want).strip().lower():
            return False
    return True


def score_prompt(spec: Dict[str, Any], calls: List[ToolCall]) -> Dict[str, Any]:
    """Score one run against its prompt spec.

    Returns a flat dict that the runner merges into the JSONL record:
      first_tool, first_is_nexus, used_tool_search, calls_before_nexus,
      right_tool, args_ok, passed.
    """
    effective = effective_calls(calls)
    window = effective[:WINDOW]
    first = effective[0] if effective else None
    first_nexus_index = next(
        (i for i, c in enumerate(effective) if is_nexus_tool(c.name)), None
    )

    result: Dict[str, Any] = {
        "first_tool": normalize_tool(first.name) if first else None,
        "first_is_nexus": bool(first and is_nexus_tool(first.name)),
        "used_tool_search": any(c.name in NEUTRAL_TOOLS for c in calls),
        "calls_before_nexus": first_nexus_index,
        "right_tool": None,
        "args_ok": None,
    }

    expect = spec.get("expect")
    if expect == "none":
        result["passed"] = len(effective) == 0
        return result
    if expect == "native":
        result["passed"] = bool(first and first.name in NATIVE_TOOLS)
        return result

    expected_tools = set(spec.get("expected_any_of", []))
    hits = [
        c for c in window
        if is_nexus_tool(c.name) and normalize_tool(c.name) in expected_tools
    ]
    result["right_tool"] = bool(hits)
    result["args_ok"] = any(
        args_match(c.input, spec.get("expected_args"), TOOL_DEFAULTS.get(normalize_tool(c.name)))
        for c in hits
    )
    result["passed"] = result["right_tool"] and result["args_ok"]
    return result


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rates over a list of scored records (errors and skipped runs excluded)."""
    scored = [r for r in records if r.get("passed") is not None and not r.get("run_error")]
    positive = [r for r in scored if r.get("category") != "negative"]
    negative = [r for r in scored if r.get("category") == "negative"]

    def rate(items: List[Dict[str, Any]], key: str) -> Optional[float]:
        if not items:
            return None
        return sum(1 for r in items if r.get(key)) / len(items)

    return {
        "n": len(scored),
        "pass_rate": rate(scored, "passed"),
        "nexus_first_rate": rate(positive, "first_is_nexus"),
        "right_tool_rate": rate(positive, "right_tool"),
        "args_ok_rate": rate(positive, "args_ok"),
        "negative_pass_rate": rate(negative, "passed"),
    }
