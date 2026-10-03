"""Routing eval runner: does Claude call nexus-mcp tools unprompted?

Usage:
    python -m evals.routing.runner --smoke
    python -m evals.routing.runner --conditions mcp-only,nexus --tool-search on,off
    python -m evals.routing.runner --label after-descriptions

Each run starts `claude -p` headless in a copy of the shop_repo fixture, with the
nexus-mcp server connected, and sends one prompt that names no tool. The runner
reads the event stream and stops the process after WINDOW effective tool calls,
so a run costs a few thousand tokens. Records go to evals/results/routing-<label>.jsonl.
A run that already has a record is skipped, so an interrupted run can resume.

Auth: runs use an isolated CLAUDE_CONFIG_DIR. That directory has no login, so
export CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY
in the shell that starts this runner.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml

from benchmarks import conditions as cond
from benchmarks import transcript as tx
from benchmarks.runner import claude_version
from evals.routing import scoring

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVAL_DIR.parent
RESULTS_DIR = EVAL_DIR / "results"
WORK_DIR = EVAL_DIR / ".claude-eval"
CONFIG_DIR = WORK_DIR / "config"
FIXTURE_DIR = EVAL_DIR / "fixtures" / "shop_repo"
PROMPTS_PATH = Path(__file__).resolve().parent / "prompts.yaml"

DEFAULT_MODEL = "sonnet"
DEFAULT_CONDITIONS = ["mcp-only", "nexus"]
BUILTIN_TOOLS = "Read,Grep,Glob,ToolSearch"
MAX_BUDGET_USD = 0.50
TIMEOUT_S = 240
PROMPT_SUFFIX = "\n\nDo not edit any files."
# Phrases of the CLI's own usage-limit message. A plain API "rate limit" (HTTP 429)
# or a context-length error is a failed run, not a reason to stop the whole batch.
USAGE_LIMIT_MARKERS = ("usage limit", "out of extra usage", "5-hour limit", "weekly limit")


class UsageLimitReached(RuntimeError):
    """Raised when the CLI reports that the subscription or API limit is used up."""


def load_prompts(path: Path = PROMPTS_PATH) -> List[Dict[str, Any]]:
    """Load the prompt specs from prompts.yaml."""
    with open(path) as f:
        return yaml.safe_load(f)["prompts"]


def run_key(
    prompt_id: str, condition: str, tool_search: bool, rep: int
) -> Tuple[str, str, bool, int]:
    """Identity of one run, used to skip runs that already have a record."""
    return (prompt_id, condition, tool_search, rep)


def done_keys(out_path: Path) -> Set[Tuple[str, str, bool, int]]:
    """Run keys already present in a JSONL file (error records do not count)."""
    keys: Set[Tuple[str, str, bool, int]] = set()
    if not out_path.exists():
        return keys
    with open(out_path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("run_error") or rec.get("usage_limit") or rec.get("isolation_problems"):
                continue  # a failed or non-isolated run is retried on the next start
            keys.add(
                run_key(rec["prompt_id"], rec["condition"], rec["tool_search"], rec["rep"])
            )
    return keys


def existing_model(out_path: Path) -> Optional[str]:
    """Model name of the first record in a JSONL file, or None if there is none."""
    if not out_path.exists():
        return None
    with open(out_path) as f:
        for line in f:
            try:
                model = json.loads(line).get("model")
            except json.JSONDecodeError:
                continue
            if model:
                return model
    return None


def write_mcp_config(path: Path, python: str, src_dir: Path) -> Path:
    """Write an MCP config that starts the server from THIS checkout's source.

    The installed `nexus-mcp-ci` may point at another checkout, so the eval
    would measure stale tool descriptions. PYTHONPATH makes this checkout win.
    """
    config = {
        "mcpServers": {
            "nexus-mcp": {
                "command": python,
                "args": ["-c", "from nexus_mcp.server import main; main()"],
                "env": {"PYTHONPATH": str(src_dir), "NEXUS_AUDIT_ENABLED": "false"},
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    return path


def prepare_repo(python: str, src_dir: Path, work_dir: Path = WORK_DIR) -> Path:
    """Copy the fixture into the work dir and index it once. Returns the copy."""
    repo = work_dir / "shop_repo"
    if not (repo / ".nexus").exists():
        shutil.copytree(FIXTURE_DIR, repo, dirs_exist_ok=True)
        env = {**os.environ, "PYTHONPATH": str(src_dir)}
        meta = work_dir / "setup_meta.json"
        preindex = REPO_ROOT / "benchmarks" / "_preindex_one.py"
        subprocess.run(
            [python, str(preindex), str(repo), "shop_repo", str(meta)],
            check=True,
            env=env,
        )
    return repo


def looks_like_usage_limit(trace: tx.RunTrace) -> bool:
    """True when the run ended in an error that names a usage or rate limit."""
    if not trace.is_error:
        return False
    text = (trace.final_answer or "").lower()
    return any(marker in text for marker in USAGE_LIMIT_MARKERS)


def isolation_problems(trace: tx.RunTrace, condition: str) -> List[str]:
    """Reasons a run is not isolated from the user's own Claude Code setup.

    The `baseline` condition expects no MCP server. The others expect exactly the
    nexus server, connected. The `nexus-plugin` condition names it by plugin.
    """
    problems: List[str] = []
    init = trace.init_event
    if not init:
        return ["no system/init event"]
    if init.get("plugins") and condition != "nexus-plugin":
        problems.append(f"plugins loaded: {init['plugins']}")
    servers = [s for s in trace.mcp_servers if isinstance(s, dict)]
    nexus = [s for s in servers if "nexus" in str(s.get("name", ""))]
    extra = [s.get("name") for s in servers if s not in nexus]
    if extra:
        problems.append(f"unexpected MCP servers: {extra}")
    if condition == "baseline":
        if nexus:
            problems.append("nexus server present in the baseline condition")
    elif not nexus:
        problems.append("nexus server missing")
    elif nexus[0].get("status") != "connected":
        problems.append(f"nexus server status is {nexus[0].get('status')}")
    return problems


def _tool_use_names(event: Dict[str, Any]) -> List[str]:
    if event.get("type") != "assistant":
        return []
    content = event.get("message", {}).get("content") or []
    return [
        b.get("name", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def stream_run(
    argv: List[str],
    env: Dict[str, str],
    cwd: Path,
    timeout_s: float,
    max_calls: int,
) -> Tuple[List[str], bool, bool]:
    """Run the CLI, read its event stream, stop after `max_calls` effective tool calls.

    Returns (stdout_lines, timed_out, stopped_early). A watchdog timer kills the
    whole process group on timeout, because `claude` starts its own children.
    """
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    timed_out = False

    def kill() -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    lines: List[str] = []
    effective = 0
    stopped_early = False

    def on_timeout() -> None:
        nonlocal timed_out
        if stopped_early:  # the loop already ended the run; this is not a timeout
            return
        timed_out = True
        kill()

    timer = threading.Timer(timeout_s, on_timeout)
    timer.start()
    try:
        for line in proc.stdout:
            lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            effective += sum(
                1 for n in _tool_use_names(event) if n not in scoring.NEUTRAL_TOOLS
            )
            if effective >= max_calls:
                stopped_early = True
                kill()
                break
    finally:
        timer.cancel()
        kill()
        proc.wait()
    return lines, timed_out, stopped_early


def run_once(
    spec: Dict[str, Any],
    condition: str,
    tool_search: bool,
    repo_dir: Path,
    mcp_config: Path,
    model: str,
    claude_version: str,
) -> Dict[str, Any]:
    """Execute one routing run and return its scored record."""
    prompt = spec["prompt"].strip() + PROMPT_SUFFIX
    built = cond.build_run(
        condition,
        prompt,
        model,
        MAX_BUDGET_USD,
        CONFIG_DIR,
        tool_search=tool_search,
        builtin_tools=BUILTIN_TOOLS,
    )
    argv = list(built["argv"])
    # Point the server at this checkout's source instead of the packaged config.
    if "--mcp-config" in argv:
        argv[argv.index("--mcp-config") + 1] = str(mcp_config)

    started = time.time()
    lines, timed_out, stopped_early = stream_run(
        argv, built["env"], repo_dir, TIMEOUT_S, scoring.WINDOW
    )
    trace = tx.parse_lines(lines)
    if looks_like_usage_limit(trace):
        raise UsageLimitReached(trace.final_answer)

    score = scoring.score_prompt(spec, trace.tool_calls)
    return {
        "prompt_id": spec["id"],
        "category": spec["category"],
        "condition": condition,
        "tool_search": tool_search,
        "model": model,
        "claude_version": claude_version,
        "isolation_mode": built["isolation_mode"],
        "isolation_problems": isolation_problems(trace, condition),
        "init_tools": trace.init_event.get("tools"),
        "timed_out": timed_out,
        "stopped_early": stopped_early,
        "wall_seconds": round(time.time() - started, 2),
        "tool_calls": [
            {"name": c.name, "input": c.input} for c in trace.tool_calls
        ],
        "permission_denials": trace.permission_denials,
        "parse_errors": trace.parse_errors,
        **score,
    }


def write_record(record: Dict[str, Any], out_path: Path) -> None:
    """Append one record to the JSONL output."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def run_suite(
    specs: Iterable[Dict[str, Any]],
    conditions: List[str],
    tool_search_modes: List[bool],
    reps: int,
    out_path: Path,
    run_one,
) -> int:
    """Run every (prompt, condition, tool_search, rep) not yet in `out_path`.

    `run_one(spec, condition, tool_search)` returns a record. A usage-limit error
    stops the suite with exit code 3 and keeps all earlier records.
    Returns the number of records written.
    """
    done = done_keys(out_path)
    specs = list(specs)
    total = len(specs) * len(conditions) * len(tool_search_modes) * reps
    written = 0
    n = 0
    for spec in specs:
        for condition in conditions:
            for tool_search in tool_search_modes:
                for rep in range(reps):
                    n += 1
                    if run_key(spec["id"], condition, tool_search, rep) in done:
                        continue
                    print(
                        f"[{n}/{total}] {spec['id']} / {condition} / "
                        f"tool_search={'on' if tool_search else 'off'} / rep {rep + 1}",
                        file=sys.stderr,
                    )
                    try:
                        record = run_one(spec, condition, tool_search)
                    except UsageLimitReached as exc:
                        print(
                            f"Usage limit reached ({exc}). Run the same command later to resume.",
                            file=sys.stderr,
                        )
                        raise
                    except Exception as exc:  # noqa: BLE001 - one bad run must not stop the batch
                        record = {
                            "prompt_id": spec["id"],
                            "category": spec["category"],
                            "condition": condition,
                            "tool_search": tool_search,
                            "run_error": f"{type(exc).__name__}: {exc}",
                        }
                    record["rep"] = rep
                    write_record(record, out_path)
                    written += 1
    return written


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint. Returns 0 when done, 3 when the usage limit stopped the run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conditions", default=",".join(DEFAULT_CONDITIONS))
    parser.add_argument("--tool-search", default="on,off", help="Comma list of on/off")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--label", default="run", help="Names the output file")
    parser.add_argument("--only", default="", help="Comma list of prompt ids")
    parser.add_argument("--smoke", action="store_true", help="4 prompts, mcp-only, tool search on")
    parser.add_argument("--python", default=sys.executable, help="Python that can import nexus_mcp")
    args = parser.parse_args(argv)

    specs = load_prompts()
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    modes = [m.strip() == "on" for m in args.tool_search.split(",") if m.strip()]
    if args.only:
        wanted = {p.strip() for p in args.only.split(",")}
        specs = [s for s in specs if s["id"] in wanted]
    if args.smoke:
        wanted = {"search-retry", "graph-callers", "map-overview", "negative-no-code"}
        specs = [s for s in specs if s["id"] in wanted]
        conditions, modes = ["mcp-only"], [True]

    out_path = RESULTS_DIR / f"routing-{args.label}.jsonl"
    prior_model = existing_model(out_path)
    if prior_model and prior_model != args.model:
        raise SystemExit(
            f"{out_path} already holds runs for model {prior_model!r}. "
            f"Use a new --label to run model {args.model!r}."
        )

    src_dir = REPO_ROOT / "src"
    repo_dir = prepare_repo(args.python, src_dir)
    mcp_config = write_mcp_config(WORK_DIR / "mcp.json", args.python, src_dir)
    version = claude_version()

    def run_one(spec: Dict[str, Any], condition: str, tool_search: bool) -> Dict[str, Any]:
        return run_once(spec, condition, tool_search, repo_dir, mcp_config, args.model, version)

    try:
        written = run_suite(specs, conditions, modes, args.reps, out_path, run_one)
    except UsageLimitReached:
        return 3
    print(f"Wrote {written} records to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
