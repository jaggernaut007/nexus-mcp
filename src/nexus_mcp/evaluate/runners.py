"""Build and run one agent call for the agent tier: Claude Code, Codex or agy, one condition.

The argv builders and the output parsers are pure, so tests check them without a CLI.
``run`` is the only function that starts a process. Each run sends the prompt and the
code the agent reads to the model provider, through the user's own CLI and login.
"""

import json
import os
import shutil
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


AGY_SERVER = "nexus-mcp"
AGY_HOME_BIN = Path.home() / ".gemini" / "bin" / "agy"
AGY_ALLOWED_TOOLS = ("search", "find_symbol", "graph", "map", "index")


def agy_binary() -> str:
    """The agy executable: on PATH, else the default install folder, else the bare name."""
    return shutil.which("agy") or (str(AGY_HOME_BIN) if AGY_HOME_BIN.is_file() else "agy")


def agy_settings(condition: str) -> Dict[str, Any]:
    """Session settings for agy. Allow the Nexus-MCP tools: headless agy cannot ask."""
    allow = [f"mcp({AGY_SERVER}/{tool})" for tool in AGY_ALLOWED_TOOLS]
    return {"permissions": {"allow": allow if condition in ("mcp", "nexus") else []}}


def agy_argv(model: str, condition: str, prompt: str, settings_file: Path) -> List[str]:
    """argv for ``agy -p --output-format stream-json`` in plan mode (the read-only mode).

    agy has no per-session MCP setting. The server comes from ``agy mcp add``, and ``run``
    switches it on or off for the condition.
    """
    if condition == "nexus":
        prompt = skill_text() + "\n\n---\n\n" + prompt
    return [
        agy_binary(),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--mode",
        "plan",
        "--settings",
        str(settings_file),
    ]


def agy_mcp_toggle(condition: str, runner=subprocess.run) -> bool:
    """Enable the registered Nexus-MCP server for mcp and nexus, disable it for baseline.

    Returns False when agy refused (for example, the server was never added). The state is
    global to the user's agy config, so ``run`` puts it back after each call.
    """
    action = "enable" if condition in ("mcp", "nexus") else "disable"
    try:
        done = runner(
            [agy_binary(), "mcp", action, AGY_SERVER], capture_output=True, text=True, timeout=30
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return done.returncode == 0


def parse_agy(lines: Iterable[str]) -> Dict[str, Any]:
    """Usage, tool calls and the final answer from ``agy --output-format stream-json`` events.

    agy reports tokens, not USD, so cost_usd is None (not measured). Tokens are input plus
    output plus thinking. Cache reads are not counted, as in the agy total.
    """
    answer, usage, result_seen, status, error = "", None, False, "", ""
    names: Dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "step_update":
            step = event.get("step_update") or {}
            if step.get("step_type") == "tool" and step.get("state") in ("DONE", "ERROR"):
                info = step.get("tool_info") or {}
                name = step.get("tool_name") or info.get("name") or "tool"
                if name == "call_mcp_tool":
                    name = f"mcp:{(info.get('parameters') or {}).get('ToolName', '?')}"
                names[name] = names.get(name, 0) + 1
        elif event.get("event") == "result":
            result = event.get("result") or {}
            result_seen = True
            answer = result.get("response", "") or answer
            usage = result.get("usage") or usage
            status = result.get("status", "")
            error = result.get("error", "") or error
    tokens = None
    if usage:
        keys = ("input_tokens", "output_tokens", "thinking_tokens")
        tokens = sum(int(usage.get(k, 0) or 0) for k in keys)
    is_error = not result_seen or status != "SUCCESS" or bool(error)
    out: Dict[str, Any] = {
        "answer": answer,
        "tokens": tokens,
        "cost_usd": None,
        "turns": None,
        "tool_calls": sum(names.values()),
        "tools": names,
        "is_error": is_error,
    }
    if is_error:
        out["error"] = (error or answer or "no result event").strip()[:300]
    return out


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
        elif spec.runner == "agy":
            settings_file = Path(tmp) / "settings.json"
            settings_file.write_text(json.dumps(agy_settings(condition)))
            argv = agy_argv(spec.model, condition, prompt, settings_file)
            # agy starts the registered server with its own environment: pass the index.
            env = {**os.environ, **server_env(storage)}
            if not agy_mcp_toggle(condition):
                return {
                    "answer": "", "tokens": None, "cost_usd": None, "turns": None,
                    "tool_calls": 0, "tools": {}, "is_error": True, "seconds": 0.0,
                    "isolation": "shared-config",
                    "error": f"agy could not switch the '{AGY_SERVER}' server. "
                    f"Run: agy mcp add {AGY_SERVER} <python> -m nexus_mcp",
                }
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
    if spec.runner == "agy":
        agy_mcp_toggle("nexus")  # put the registered server back on, its normal state
    lines = out.splitlines()
    if spec.runner == "claude":
        parsed = parse_claude(lines)
    elif spec.runner == "agy":
        parsed = parse_agy(lines)
    else:
        parsed = parse_codex(lines, spec.model)
    parsed["seconds"] = round(time.time() - started, 1)
    parsed["isolation"] = "bare" if bare and spec.runner == "claude" else "reduced"
    if spec.runner == "agy":
        parsed["isolation"] = "shared-config"  # the agy MCP config is global to the user
    if failed and not parsed["answer"]:
        parsed["is_error"] = True
        parsed["error"] = note
    return parsed
