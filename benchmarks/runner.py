"""Benchmark runner: drives `claude -p` across tasks x conditions x reps.

Usage:
    python -m benchmarks.runner --tasks tasks/django.yaml --conditions baseline,nexus --reps 3
    python -m benchmarks.runner --tasks tasks/django.yaml --smoke

Writes one JSONL record per run to benchmarks/results/runs-<timestamp>.jsonl.
Each run is a subprocess with a wall-clock timeout independent of
--max-budget-usd (the CLI's own budget cap only bounds spend, not time).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from benchmarks import conditions as cond
from benchmarks import scoring
from benchmarks import transcript as tx

BENCH_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCH_DIR / "results"
REPOS_DIR = BENCH_DIR / "repos"
DEFAULT_MODEL = "sonnet"
DEFAULT_CONDITIONS = ["baseline", "nexus"]
DEFAULT_REPS = 3
PROMPT_SUFFIX = "\n\nDo not edit any files. End your response with a concise final answer."
# Phrases of the CLI's own usage-limit message. A plain API "rate limit" (HTTP 429)
# or a context-length error is a failed run, not a reason to stop the whole batch.
USAGE_LIMIT_MARKERS = ("usage limit", "out of extra usage", "5-hour limit", "weekly limit")


class UsageLimitReached(RuntimeError):
    """Raised when the CLI reports that the subscription or API limit is used up."""


LOGIN_HELP = (
    "The eval runs claude in an isolated config directory, which has no login.\n"
    "Run `claude setup-token`, then `export CLAUDE_CODE_OAUTH_TOKEN=<token>` in the same\n"
    "shell that starts this command (or export ANTHROPIC_API_KEY)."
)


def require_login(config_dir: Path, base_env: Optional[Dict[str, str]] = None) -> None:
    """Exit with a clear message if `claude` is not logged in under `config_dir`.

    `claude auth status` costs nothing. Without this check a missing login only
    shows up as a batch of runs that end in a second with no tool calls.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    try:
        out = subprocess.run(
            ["claude", "auth", "status"], capture_output=True, text=True, timeout=30, env=env
        )
        status = json.loads(out.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return  # cannot tell; let the first run report it
    if not status.get("loggedIn"):
        raise SystemExit(LOGIN_HELP)


def claude_version() -> str:
    """Output of `claude --version`, or 'unknown'. Stamped on every record."""
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def done_keys(out_path: Path, model: str) -> set:
    """(task_id, condition, rep) of runs of `model` that already finished without an error."""
    keys = set()
    if not out_path.exists():
        return keys
    with open(out_path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("run_error") or rec.get("is_error") or rec.get("model") != model:
                continue
            keys.add((rec.get("task_id"), rec.get("condition"), rec.get("rep")))
    return keys


def load_task_suite(path: Path) -> Dict[str, Any]:
    """Load and YAML-parse a task suite file (see benchmarks/tasks/*.yaml)."""
    with open(path) as f:
        return yaml.safe_load(f)


def repo_dir_for(suite: Dict[str, Any]) -> Path:
    """Local clone directory for a suite's repo, under benchmarks/repos/."""
    return REPOS_DIR / suite["repo"]["name"]


def run_once(
    task: Dict[str, Any],
    condition: str,
    repo: Dict[str, Any],
    repo_dir: Path,
    model: str,
    config_dir: Path,
    version: str = "unknown",
    raw_dir: Optional[Path] = None,
    rep: int = 0,
) -> Dict[str, Any]:
    """Execute one (task, condition) run and return its scored record.

    `raw_dir`, when given, receives the raw stream-json stdout of the run, so a
    published number can be recomputed if the parser changes.
    """
    max_budget = task.get("max_budget_usd", 1.00)
    timeout_s = task.get("timeout_s", 600)
    prompt = task["prompt"].strip() + PROMPT_SUFFIX

    built = cond.build_run(condition, prompt, model, max_budget, config_dir)
    argv, env, isolation_mode = built["argv"], built["env"], built["isolation_mode"]

    started = time.time()
    # Popen + start_new_session (not subprocess.run) so a timeout can kill the
    # whole process group, not just the `claude` PID. `claude` spawns its own
    # MCP server / model subprocesses; killing only the parent on timeout can
    # leave those orphaned and still spending the run's budget.
    proc = subprocess.Popen(
        argv,
        cwd=str(repo_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _stderr = proc.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, _stderr = proc.communicate()
    wall_s = time.time() - started

    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{task['id']}-{condition}-{rep}.jsonl").write_text(stdout)

    trace = tx.parse_lines(stdout.splitlines())
    if trace.is_error and any(
        m in (trace.final_answer or "").lower() for m in USAGE_LIMIT_MARKERS
    ):
        raise UsageLimitReached(trace.final_answer)
    files = (
        trace.files_read_baseline if condition == "baseline" else trace.files_surfaced_nexus
    )
    score = scoring.score_run(
        files,
        trace.final_answer,
        task["ground_truth"],
        repo_root=str(repo_dir),
    )

    return {
        "task_id": task["id"],
        "category": task.get("category"),
        "condition": condition,
        "repo": repo["name"],
        "repo_sha": repo["pin"],
        "model": model,
        "claude_version": version,
        "isolation_mode": isolation_mode,
        "timed_out": timed_out,
        "wall_seconds": round(wall_s, 2),
        "tool_call_counts": trace.tool_call_counts,
        "search_call_count": trace.search_call_count,
        "files_touched": files,
        "usage": trace.usage,
        "total_tokens": trace.total_tokens,
        "fresh_tokens": trace.fresh_tokens,
        "retrieval_tokens_est": trace.retrieval_tokens_est,
        "total_cost_usd": trace.total_cost_usd,
        "num_turns": trace.num_turns,
        "duration_ms": trace.duration_ms,
        "result_subtype": trace.result_subtype,
        "is_error": trace.is_error,
        "parse_errors": trace.parse_errors,
        "final_answer": trace.final_answer,
        **score,
    }


def run_suite(
    suite: Dict[str, Any],
    condition_names: List[str],
    reps: int,
    model: str,
    config_dir: Path,
    out_path: Path,
    task_ids: Optional[List[str]] = None,
    raw_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Run the suite, writing each record to `out_path` as it completes.

    Records are appended incrementally so a crash (or Ctrl-C) partway through
    a multi-run — which can cost tens of dollars — keeps everything already
    finished. A single run that raises is captured as an error record and the
    batch continues rather than discarding the whole run. A run that already has an
    error-free record in `out_path` is skipped, so the same command resumes after an
    interruption. A usage-limit error stops the batch and is re-raised.
    """
    repo = suite["repo"]
    repo_dir = repo_dir_for(suite)
    if not repo_dir.exists():
        raise SystemExit(
            f"Repo not found at {repo_dir}. Run benchmarks/setup_repos.sh first."
        )

    tasks = suite["tasks"]
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]

    defaults = suite.get("defaults", {})
    records = []
    total = len(tasks) * len(condition_names) * reps
    done = 0
    finished = done_keys(out_path, model)
    version = claude_version()
    for task in tasks:
        merged_task = {**defaults, **task}
        for condition in condition_names:
            for rep in range(reps):
                done += 1
                if (merged_task["id"], condition, rep) in finished:
                    continue
                print(
                    f"[{done}/{total}] {merged_task['id']} / {condition} / rep {rep + 1}",
                    file=sys.stderr,
                )
                try:
                    record = run_once(
                        merged_task, condition, repo, repo_dir, model, config_dir,
                        version=version, raw_dir=raw_dir, rep=rep,
                    )
                except UsageLimitReached as exc:
                    print(
                        f"    usage limit reached ({exc}); run the same command later to resume",
                        file=sys.stderr,
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 — one bad run must not kill the batch
                    print(
                        f"    run failed ({type(exc).__name__}: {exc}); recording and continuing",
                        file=sys.stderr,
                    )
                    record = {
                        "task_id": merged_task["id"],
                        "category": merged_task.get("category"),
                        "condition": condition,
                        "repo": repo["name"],
                        "repo_sha": repo["pin"],
                        "model": model,
                        "run_error": f"{type(exc).__name__}: {exc}",
                        "is_error": True,
                    }
                record["rep"] = rep
                records.append(record)
                write_record(record, out_path)
    return records


def write_record(record: Dict[str, Any], out_path: Path) -> None:
    """Append a single record to the JSONL output, creating the file if needed."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: parse args, run the suite, write JSONL results.

    --smoke overrides --reps to 1 and limits to the suite's first 2 tasks,
    for a cheap end-to-end sanity check before a full paid run. Returns 0 on
    completion (per-run failures are captured as error records, not raised) and 3
    when the usage limit stopped the run; the same command resumes it.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, help="Path to a task suite YAML file")
    parser.add_argument(
        "--conditions", default=",".join(DEFAULT_CONDITIONS), help="Comma-separated conditions"
    )
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--smoke", action="store_true", help="Run 2 tasks x 1 rep for a cheap sanity check"
    )
    parser.add_argument(
        "--out", default=None,
        help="Output JSONL path (default: results/runs-<repo>.jsonl; rerun with the same "
        "path to resume)",
    )
    parser.add_argument(
        "--save-raw", action="store_true",
        help="Keep the raw stream-json of each run under results/raw/ for recomputing",
    )
    args = parser.parse_args(argv)

    suite = load_task_suite(Path(args.tasks))
    condition_names = [c.strip() for c in args.conditions.split(",") if c.strip()]

    task_ids = None
    reps = args.reps
    if args.smoke:
        task_ids = [t["id"] for t in suite["tasks"][:2]]
        reps = 1

    config_dir = BENCH_DIR / ".claude-bench"
    require_login(config_dir)
    default_out = RESULTS_DIR / f"runs-{suite['repo']['name']}.jsonl"
    out_path = Path(args.out) if args.out else default_out
    raw_dir = RESULTS_DIR / "raw" / out_path.stem if args.save_raw else None
    try:
        records = run_suite(
            suite, condition_names, reps, args.model, config_dir, out_path, task_ids,
            raw_dir=raw_dir,
        )
    except UsageLimitReached:
        return 3
    print(f"Wrote {len(records)} records to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
