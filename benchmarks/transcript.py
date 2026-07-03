"""Pure parser: `claude -p --output-format stream-json` event stream -> RunTrace.

No I/O here — runner.py feeds this an iterator of parsed JSON dicts (one per
stdout line). Tolerant of unknown/missing fields since the CLI's stream-json
schema is not a hard public contract; unknown event types are ignored rather
than raising, and malformed tool_result payloads degrade to "no files found"
instead of crashing the run.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

CHARS_PER_TOKEN = 4  # matches nexus_mcp.formatting.token_budget.TokenBudget

READ_TOOL = "Read"
SEARCH_TOOLS = ("Grep", "Glob")
MCP_TOOL_PREFIX = "mcp__"

_FILEPATH_KEYS = ("filepath", "absolute_path")


@dataclass
class ToolCall:
    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunTrace:
    tool_calls: List[ToolCall] = field(default_factory=list)
    read_files: List[str] = field(default_factory=list)
    nexus_result_files: List[str] = field(default_factory=list)
    search_call_count: int = 0
    retrieval_tokens_est: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    total_cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    duration_ms: Optional[int] = None
    final_answer: str = ""
    is_error: bool = False
    result_subtype: Optional[str] = None
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)
    parse_errors: int = 0

    @property
    def tool_call_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for call in self.tool_calls:
            counts[call.name] = counts.get(call.name, 0) + 1
        return counts

    @property
    def files_read_baseline(self) -> List[str]:
        """Distinct files read via the built-in Read tool."""
        return sorted(set(self.read_files))

    @property
    def files_surfaced_nexus(self) -> List[str]:
        """Distinct files surfaced to the model: Read + content-bearing nexus results."""
        return sorted(set(self.read_files) | set(self.nexus_result_files))

    @property
    def total_tokens(self) -> int:
        return (
            self.usage.get("input_tokens", 0)
            + self.usage.get("cache_creation_input_tokens", 0)
            + self.usage.get("cache_read_input_tokens", 0)
            + self.usage.get("output_tokens", 0)
        )

    @property
    def fresh_tokens(self) -> int:
        """Tokens excluding cache reads (cache_creation still counts as fresh work)."""
        return (
            self.usage.get("input_tokens", 0)
            + self.usage.get("cache_creation_input_tokens", 0)
            + self.usage.get("output_tokens", 0)
        )


def _extract_filepaths(obj: Any, out: Set[str]) -> None:
    """Recursively collect one path per dict that names a file.

    Nexus result entries carry both `filepath` (relative) and
    `absolute_path` (absolute) for the *same* file — only one is taken per
    dict (filepath preferred) so the same file isn't double-counted as two
    distinct "surfaced files".
    """
    if isinstance(obj, dict):
        for key in _FILEPATH_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value:
                out.add(value)
                break
        for value in obj.values():
            _extract_filepaths(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _extract_filepaths(item, out)


def _tool_result_text(content: Any) -> str:
    """Normalize a tool_result's `content` field (str or content-block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _handle_tool_result(text: str, tool_name: Optional[str], trace: RunTrace) -> None:
    trace.retrieval_tokens_est += len(text) // CHARS_PER_TOKEN
    if not tool_name or not tool_name.startswith(MCP_TOOL_PREFIX):
        return
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return
    paths: Set[str] = set()
    _extract_filepaths(parsed, paths)
    trace.nexus_result_files.extend(paths)


def _handle_assistant_event(
    event: Dict[str, Any], trace: RunTrace, pending: Dict[str, str]
) -> None:
    message = event.get("message", {})
    usage = message.get("usage")
    if isinstance(usage, dict):
        trace.usage = usage

    for block in message.get("content", []) or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        call_id = block.get("id", "")
        tool_input = block.get("input", {}) or {}
        trace.tool_calls.append(ToolCall(id=call_id, name=name, input=tool_input))
        if call_id:
            pending[call_id] = name

        if name == READ_TOOL:
            path = tool_input.get("file_path")
            if isinstance(path, str) and path:
                trace.read_files.append(path)
        elif name in SEARCH_TOOLS:
            trace.search_call_count += 1


def _handle_user_event(event: Dict[str, Any], trace: RunTrace, pending: Dict[str, str]) -> None:
    message = event.get("message", {})
    for block in message.get("content", []) or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id", "")
        tool_name = pending.get(tool_use_id)
        text = _tool_result_text(block.get("content"))
        _handle_tool_result(text, tool_name, trace)


def _handle_result_event(event: Dict[str, Any], trace: RunTrace) -> None:
    trace.result_subtype = event.get("subtype")
    trace.is_error = bool(event.get("is_error", False))
    trace.num_turns = event.get("num_turns")
    trace.duration_ms = event.get("duration_ms")
    trace.total_cost_usd = event.get("total_cost_usd")
    trace.final_answer = event.get("result", "") or ""
    usage = event.get("usage")
    if isinstance(usage, dict):
        trace.usage = usage


def _handle_system_event(event: Dict[str, Any], trace: RunTrace) -> None:
    if event.get("subtype") == "init":
        servers = event.get("mcp_servers")
        if isinstance(servers, list):
            trace.mcp_servers = servers


def parse_stream(events: Iterable[Dict[str, Any]]) -> RunTrace:
    """Parse a sequence of stream-json events into a RunTrace.

    `events` must already be JSON-decoded dicts (see parse_lines for raw text).
    """
    trace = RunTrace()
    pending: Dict[str, str] = {}  # tool_use_id -> tool name

    for event in events:
        if not isinstance(event, dict):
            trace.parse_errors += 1
            continue
        event_type = event.get("type")
        try:
            if event_type == "assistant":
                _handle_assistant_event(event, trace, pending)
            elif event_type == "user":
                _handle_user_event(event, trace, pending)
            elif event_type == "result":
                _handle_result_event(event, trace)
            elif event_type == "system":
                _handle_system_event(event, trace)
            # unknown event types are ignored, not an error
        except (KeyError, TypeError, AttributeError):
            trace.parse_errors += 1

    return trace


def parse_lines(lines: Iterable[str]) -> RunTrace:
    """Parse raw stdout lines (one JSON object per line) into a RunTrace.

    Blank lines are skipped silently. Lines that fail to JSON-decode are
    counted in `parse_errors` rather than raising — a single malformed line
    should not lose the rest of a run's data.
    """
    events: List[Dict[str, Any]] = []
    decode_errors = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except json.JSONDecodeError:
            decode_errors += 1

    trace = parse_stream(events)
    trace.parse_errors += decode_errors
    return trace
