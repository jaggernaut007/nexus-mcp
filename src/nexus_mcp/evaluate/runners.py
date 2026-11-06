"""Build and run one agent call for the agent tier: Claude Code or Codex, one condition.

The argv builders and the output parsers are pure, so tests check them without a CLI.
``run`` is the only function that starts a process. Each run sends the prompt and the
code the agent reads to the model provider, through the user's own CLI and login.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from nexus_mcp.evaluate import specs, transcript

RUN_TIMEOUT_S = 300
PER_RUN_BUDGET_USD = 1.0
CLAUDE_BUILTIN_TOOLS = "Read,Grep,Glob"
CLAUDE_DISALLOWED = "Edit,Write,NotebookEdit,WebFetch,WebSearch,Task,Bash"
CLAUDE_ALLOWED = "Read,Grep,Glob,mcp__nexus-mcp__*"
_SKILL_IN_CHECKOUT = Path(__file__).resolve().parents[3] / "plugin" / "skills" / "nexus-mcp"


def skill_text() -> str:
    """The routing skill body (frontmatter removed) for the ``nexus`` condition."""
    from nexus_mcp.setup_clients import skill_body

    for path in (
        Path(__file__).parent / "data" / "SKILL.md",
        _SKILL_IN_CHECKOUT / "SKILL.md",
    ):
        if path.is_file():
            return skill_body(path.read_text())
    return ""


def server_env(storage: Path) -> Dict[str, str]:
    """Environment of the Nexus-MCP server: the prebuilt index, no watcher, no download."""
    return {
        "NEXUS_STORAGE_DIR": str(storage),
        "NEXUS_AUTO_WATCH": "false",
        "NEXUS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }


def claude_mcp_config(storage: Path, python: Optional[str] = None) -> Dict[str, Any]:
    return {
        "mcpServers": {
            "nexus-mcp": {
                "command": python or sys.executable,
                "args": ["-m", "nexus_mcp"],
                "env": server_env(storage),
            }
        }
    }


def claude_argv(
    model: str,
    condition: str,
    prompt: str,
    mcp_config: Optional[Path],
    bare: bool,
) -> List[str]:
    """argv for ``claude -p``. Read-only tools, no Bash, no user settings or other servers."""
    argv = ["claude", "-p"]
    if bare:
        argv.append("--bare")
    argv += [
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--tools",
        CLAUDE_BUILTIN_TOOLS,
        "--allowedTools",
        CLAUDE_ALLOWED,
        "--disallowedTools",
        CLAUDE_DISALLOWED,
        "--no-session-persistence",
        "--max-budget-usd",
        str(PER_RUN_BUDGET_USD),
        "--strict-mcp-config",
    ]
    if condition in ("mcp", "nexus"):
        if mcp_config is None:
            raise ValueError(f"Condition {condition} needs an MCP config")
        argv += ["--mcp-config", str(mcp_config)]
    if condition == "nexus":
        argv += ["--append-system-prompt", skill_text()]
    if not bare:
        argv += ["--setting-sources", ""]
    return argv + ["--", prompt]


def claude_env(condition: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(base if base is not None else os.environ)
    if condition in ("mcp", "nexus"):
        env["ENABLE_TOOL_SEARCH"] = "false"  # load the nexus tools up front, no extra turn
    return env


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def codex_argv(
    model: str,
    condition: str,
    prompt: str,
    root: Path,
    storage: Optional[Path],
    python: Optional[str] = None,
) -> List[str]:
    """argv for ``codex exec --json`` (experimental). Read-only sandbox, no user config."""
    argv = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "-m",
        model,
        "-C",
        str(root),
    ]
    if condition in ("mcp", "nexus"):
        if storage is None:
            raise ValueError(f"Condition {condition} needs an index folder")
        env = ", ".join(f"{k} = {_toml_str(v)}" for k, v in server_env(storage).items())
        argv += [
            "-c",
            f"mcp_servers.nexus.command={_toml_str(python or sys.executable)}",
            "-c",
            'mcp_servers.nexus.args=["-m", "nexus_mcp"]',
            "-c",
            f"mcp_servers.nexus.env={{ {env} }}",
        ]
    if condition == "nexus":
        prompt = skill_text() + "\n\n---\n\n" + prompt
    return argv + ["--", prompt]


def parse_codex(lines: Iterable[str], model: str) -> Dict[str, Any]:
    """Usage, tool calls and the final answer from ``codex exec --json`` events."""
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    answer, tools, turns, errors, completed = "", 0, 0, 0, False
    names: Dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            turns += 1
            completed = True
            for key in usage:
                usage[key] += int((event.get("usage") or {}).get(key, 0) or 0)
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                answer = item.get("text", "") or answer
            elif item.get("type") in ("mcp_tool_call", "command_execution"):
                tools += 1
                names[item["type"]] = names.get(item["type"], 0) + 1
        elif kind in ("turn.failed", "error"):
            errors += 1
    tokens = usage["input_tokens"] + usage["output_tokens"] if completed else None
    return {
        "answer": answer,
        "tokens": tokens,
        "cost_usd": specs.codex_cost(model, usage) if completed else None,
        "turns": turns or None,
        "tool_calls": tools,
        "tools": names,
        "is_error": not completed or errors > 0 and not answer,
    }


def _last_assistant_text(lines: Iterable[str]) -> str:
    """The text of the last assistant message: where the CLI puts a login or API error."""
    text = ""
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "") or text
    return text


def parse_claude(lines: Iterable[str]) -> Dict[str, Any]:
    lines = list(lines)
    trace = transcript.parse_lines(lines)
    is_error = trace.is_error or not trace.has_result_event
    out = {
        "answer": trace.final_answer,
        "tokens": trace.total_tokens,
        "cost_usd": trace.total_cost_usd,
        "turns": trace.num_turns,
        "tool_calls": len(trace.tool_calls),
        # Names and counts, so the report shows whether a nexus run used Nexus-MCP at all.
        "tools": trace.tool_call_counts,
        "is_error": is_error,
    }
    if is_error:
        out["error"] = (trace.final_answer or _last_assistant_text(lines)).strip()[:300]
    return out


def run(
    spec: specs.Spec,
    condition: str,
    prompt: str,
    root: Path,
    storage: Path,
    runner=subprocess.run,
) -> Dict[str, Any]:
    """Run one agent call. Returns the parsed result plus wall time; never raises on failure."""
    bare = bool(os.environ.get("ANTHROPIC_API_KEY"))
    with tempfile.TemporaryDirectory(prefix="nexus-agent-") as tmp:
        if spec.runner == "claude":
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps(claude_mcp_config(storage)))
            argv = claude_argv(spec.model, condition, prompt, config, bare)
            env = claude_env(condition)
        else:
            argv = codex_argv(spec.model, condition, prompt, root, storage)
            env = dict(os.environ)
        started = time.time()
        try:
            proc = runner(
                argv,
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_S,
            )
            out, failed = proc.stdout or "", proc.returncode != 0
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Keep what a timed-out run printed: its tool calls show where it was stuck.
            partial = getattr(exc, "stdout", None) or ""
            out = partial.decode("utf-8", "replace") if isinstance(partial, bytes) else partial
            failed = True
            note = type(exc).__name__
        else:
            note = (proc.stderr or "").strip()[-300:] if failed else ""
    lines = out.splitlines()
    parsed = parse_claude(lines) if spec.runner == "claude" else parse_codex(lines, spec.model)
    parsed["seconds"] = round(time.time() - started, 1)
    parsed["isolation"] = "bare" if bare and spec.runner == "claude" else "reduced"
    if failed and not parsed["answer"]:
        parsed["is_error"] = True
        parsed["error"] = note
    return parsed
