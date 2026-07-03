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
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_CONDITIONS = ["baseline", "nexus"]
DEFAULT_REPS = 3
PROMPT_SUFFIX = "\n\nDo not edit any files. End your response with a concise final answer."


def load_task_suite(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def repo_dir_for(suite: Dict[str, Any]) -> Path:
    return REPOS_DIR / suite["repo"]["name"]


def run_once(
    task: Dict[str, Any],
    condition: str,
    repo: Dict[str, Any],
    repo_dir: Path,
    model: str,
    config_dir: Path,
) -> Dict[str, Any]:
    """Execute one (task, condition) run and return its scored record."""
    max_budget = task.get("max_budget_usd", 1.00)
    timeout_s = task.get("timeout_s", 600)
    prompt = task["prompt"].strip() + PROMPT_SUFFIX

    built = cond.build_run(condition, prompt, model, max_budget, config_dir)
    argv, env, isolation_mode = built["argv"], built["env"], built["isolation_mode"]

    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout = proc.stdout
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        raw_stdout = exc.stdout or ""
        stdout = raw_stdout.decode() if isinstance(raw_stdout, bytes) else raw_stdout
        timed_out = True
    wall_s = time.time() - started

    trace = tx.parse_lines(stdout.splitlines())
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
) -> List[Dict[str, Any]]:
    """Run the suite, writing each record to `out_path` as it completes.

    Records are appended incrementally so a crash (or Ctrl-C) partway through
    a multi-run — which can cost tens of dollars — keeps everything already
    finished. A single run that raises is captured as an error record and the
    batch continues rather than discarding the whole run.
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
    for task in tasks:
        merged_task = {**defaults, **task}
        for condition in condition_names:
            for rep in range(reps):
                done += 1
                print(
                    f"[{done}/{total}] {merged_task['id']} / {condition} / rep {rep + 1}",
                    file=sys.stderr,
                )
                try:
                    record = run_once(merged_task, condition, repo, repo_dir, model, config_dir)
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
    parser.add_argument("--out", default=None, help="Output JSONL path (default: timestamped)")
    args = parser.parse_args(argv)

    suite = load_task_suite(Path(args.tasks))
    condition_names = [c.strip() for c in args.conditions.split(",") if c.strip()]

    task_ids = None
    reps = args.reps
    if args.smoke:
        task_ids = [t["id"] for t in suite["tasks"][:2]]
        reps = 1

    config_dir = BENCH_DIR / ".claude-bench"
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"runs-{int(time.time())}.jsonl"
    records = run_suite(
        suite, condition_names, reps, args.model, config_dir, out_path, task_ids
    )
    print(f"Wrote {len(records)} records to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
