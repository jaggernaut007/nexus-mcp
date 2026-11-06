"""Benchmark runner: drives `claude -p` across tasks x conditions x reps.

Usage:
    python -m benchmarks.runner --tasks tasks/django.yaml --conditions baseline,nexus --reps 3
    python -m benchmarks.runner --tasks tasks/django.yaml --smoke
    python -m benchmarks.runner --tasks tasks/private/jobscout.yaml \
        --conditions baseline,nexus --embedding-models bge-small-en,granite-97m-r2-int8

With --embedding-models, each nexus condition runs once per model as `<name>@<model>`
(for example `nexus@granite-97m-r2-int8`). Build those indexes first with
`python -m benchmarks.preindex_models`.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from benchmarks import conditions as cond
from benchmarks import scoring
from benchmarks import transcript as tx
from benchmarks.preindex_models import storage_dir_for

BENCH_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCH_DIR / "results"
REPOS_DIR = BENCH_DIR / "repos"
# Indexes of a pinned Nexus-MCP install live here, outside the corpus, one folder per venv.
INDEXES_DIR = BENCH_DIR / "indexes"
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


@dataclass(frozen=True)
class PinnedNexus:
    """One Nexus-MCP install made by `benchmarks/install_nexus.sh`, measured as installed."""

    venv: Path
    version: str
    ref: str

    @property
    def bin(self) -> Path:
        return self.venv / "bin" / "nexus-mcp-ci"

    @property
    def python(self) -> Path:
        return self.venv / "bin" / "python"


def load_pinned(venv: Path) -> PinnedNexus:
    """Read the version and the git ref of the install in `venv`. Exit if it is not usable."""
    venv = venv.resolve()
    python = venv / "bin" / "python"
    if not (venv / "bin" / "nexus-mcp-ci").exists() or not python.exists():
        raise SystemExit(f"No nexus-mcp-ci in {venv}. Run benchmarks/install_nexus.sh first.")
    out = subprocess.run(
        [str(python), "-c",
         "import importlib.metadata as m; print(m.version('nexus-mcp-ci'))"],
        capture_output=True, text=True, timeout=60, env={"PATH": os.environ.get("PATH", "")},
    )
    if out.returncode != 0:
        raise SystemExit(f"Cannot read the nexus-mcp-ci version in {venv}: {out.stderr}")
    ref_file = venv / "NEXUS_REF"
    ref = ref_file.read_text().strip() if ref_file.exists() else "unknown"
    return PinnedNexus(venv=venv, version=out.stdout.strip(), ref=ref)


def pinned_index_dir(pinned: PinnedNexus, repo_dir: Path) -> Path:
    """Index folder of one corpus for one pinned install (never inside the corpus)."""
    return INDEXES_DIR / pinned.venv.name / repo_dir.name


def ensure_pinned_index(pinned: PinnedNexus, repo_dir: Path) -> Path:
    """Build the index of `repo_dir` with the pinned install, once. Returns its folder.

    The index is built before any timed run, so its cost is never inside a measured run.
    The build time goes to results/setup_meta-<venv>.json. PYTHONPATH is cleared so the
    pinned package, not this checkout's `src/`, does the work.
    """
    storage = pinned_index_dir(pinned, repo_dir)
    if (storage / "index_metadata.json").exists():
        return storage
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["NEXUS_STORAGE_DIR"] = str(storage)
    env["NEXUS_EMBEDDING_DEVICE"] = cond.BENCH_EMBEDDING_DEVICE
    env["NEXUS_AUTO_WATCH"] = "false"
    meta = RESULTS_DIR / f"setup_meta-{pinned.venv.name}.json"
    print(f"[index] {repo_dir.name} with nexus-mcp-ci {pinned.version}", file=sys.stderr)
    proc = subprocess.run(
        [str(pinned.python), str(BENCH_DIR / "_preindex_one.py"), str(repo_dir),
         repo_dir.name, str(meta)],
        cwd=str(repo_dir), env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"Indexing {repo_dir} with {pinned.venv} failed")
    return storage


def mcp_config_for(
    condition: str, repo_dir: Path, pinned: Optional[PinnedNexus] = None
) -> Path:
    """The --mcp-config file of one condition on one corpus."""
    _, embedding_model = cond.split_condition(condition)
    if pinned is not None:
        return cond.write_pinned_mcp_config(
            RESULTS_DIR / "mcp-configs" / f"{repo_dir.name}-{pinned.venv.name}.json",
            pinned.bin,
            pinned_index_dir(pinned, repo_dir),
        )
    if embedding_model:
        return cond.write_model_mcp_config(
            RESULTS_DIR / "mcp-configs" / f"{repo_dir.name}-{embedding_model}.json",
            embedding_model,
            storage_dir_for(repo_dir, embedding_model),
        )
    return cond.NEXUS_MCP_CONFIG


def run_order(
    tasks: List[Dict[str, Any]], condition_names: List[str], reps: int
) -> List[Tuple[Dict[str, Any], str, int]]:
    """The (task, condition, rep) runs in an interleaved order.

    Rep is the outer loop, so the reps of one task are spread over the batch. The order of
    the conditions turns round for each task and rep (A B, then B A), so drift in time and
    prompt-cache warmth fall on both sides in the same way.
    """
    order = []
    for rep in range(reps):
        for i, task in enumerate(tasks):
            names = condition_names if (rep + i) % 2 == 0 else list(reversed(condition_names))
            order += [(task, name, rep) for name in names]
    return order


def claude_version() -> str:
    """Output of `claude --version`, or 'unknown'. Stamped on every record."""
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def done_keys(out_path: Path, model: str, effort: Optional[str] = None) -> set:
    """(task_id, condition, rep) of runs of `model` that already finished without an error.

    With `effort`, a run at another effort level does not count as done.
    """
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
            if effort is not None and rec.get("effort") != effort:
                continue
            keys.add((rec.get("task_id"), rec.get("condition"), rec.get("rep")))
    return keys


def load_task_suite(path: Path) -> Dict[str, Any]:
    """Load and YAML-parse a task suite file (see benchmarks/tasks/*.yaml)."""
    with open(path) as f:
        return yaml.safe_load(f)


def repo_dir_for(suite: Dict[str, Any]) -> Path:
    """Local directory of a suite's repo: `repo.path` (--repo-path), else benchmarks/repos/."""
    path = suite["repo"].get("path")
    return Path(path) if path else REPOS_DIR / suite["repo"]["name"]


def git_head(path: Path) -> str:
    """The commit of a local repo, or "local" for a folder that is not a git checkout."""
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30
    )
    return proc.stdout.strip() if proc.returncode == 0 else "local"


def use_repo_path(suite: Dict[str, Any], repo_path: Path) -> Dict[str, Any]:
    """Point a suite at a local folder instead of benchmarks/repos/<name>."""
    repo_path = repo_path.resolve()
    suite["repo"] = {**suite.get("repo", {}), "path": str(repo_path), "pin": git_head(repo_path)}
    suite["repo"].setdefault("name", repo_path.name)
    return suite


def generate_suite(repo_path: Path, count: int, seed: int = 7) -> Dict[str, Any]:
    """A task suite made from the folder's own symbols, as `nexus-mcp-ci eval` makes them.

    The ground truth is the file that defines each symbol, from the parser, never from a
    Nexus-MCP ranking. Builds a throwaway index to read the symbols (the model must be cached
    or downloadable).
    """
    import shutil
    import tempfile

    from nexus_mcp.evaluate import agent

    repo_path = repo_path.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="nexus-bench-generate-"))
    try:
        rows = agent.build_index(repo_path, tmp)["rows"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tasks = [
        {
            "id": f"gen-{q.id}",
            "category": q.kind,
            "prompt": agent.make_prompt(q.query),
            "ground_truth": {
                "relevant_files": [q.relevant],
                "must_mention_files": [q.relevant],
                "facts": [{"any_of": [q.symbol.split(".")[-1]]}],
            },
        }
        for q in agent.pick_tasks(rows, repo_path, count, seed)
    ]
    if not tasks:
        raise ValueError(f"No usable tasks in {repo_path}: too few documented symbols")
    suite = {"repo": {"name": repo_path.name}, "defaults": {"max_budget_usd": 1.0}, "tasks": tasks}
    return use_repo_path(suite, repo_path)


WARM_UP_PROMPT = "Reply with the single word: ready. Do not call a tool."
WARM_UP_TTL_S = 240  # the prompt cache lives 5 minutes; warm again after 4
WARM_UP_BUDGET_USD = 0.25
WARM_UP_TIMEOUT_S = 120


def warm_up(
    condition: str,
    repo_dir: Path,
    model: str,
    config_dir: Path,
    effort: Optional[str] = None,
    pinned: Optional[PinnedNexus] = None,
) -> None:
    """Send one small call for a condition, so that its prompt is in the cache.

    The system prompt and the tool definitions of a condition are about 17,000 tokens.
    The first call writes them to the prompt cache (about $0.06); later calls read them.
    Without this call the first task of a batch pays that write and the other tasks do
    not, so the cost of a task depended on its place in the batch. The call is not
    recorded. A failure here is not an error: the run that follows shows the fault.
    """
    built = cond.build_run(
        condition, WARM_UP_PROMPT, model, WARM_UP_BUDGET_USD, config_dir,
        mcp_config_path=mcp_config_for(condition, repo_dir, pinned), effort=effort,
    )
    proc = subprocess.Popen(
        built["argv"], cwd=str(repo_dir), env=built["env"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        proc.wait(timeout=WARM_UP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


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
    effort: Optional[str] = None,
    pinned: Optional[PinnedNexus] = None,
) -> Dict[str, Any]:
    """Execute one (task, condition) run and return its scored record.

    `raw_dir`, when given, receives the raw stream-json stdout of the run, so a
    published number can be recomputed if the parser changes.
    """
    max_budget = task.get("max_budget_usd", 1.00)
    timeout_s = task.get("timeout_s", 600)
    prompt = task["prompt"].strip() + PROMPT_SUFFIX

    base, embedding_model = cond.split_condition(condition)
    built = cond.build_run(
        condition, prompt, model, max_budget, config_dir,
        mcp_config_path=mcp_config_for(condition, repo_dir, pinned), effort=effort,
    )
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
        trace.files_read_baseline if base == "baseline" else trace.files_surfaced_nexus
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
        "embedding_model": embedding_model,
        "repo": repo["name"],
        "repo_sha": repo["pin"],
        "model": model,
        "effort": effort,
        "nexus_version": pinned.version if pinned else None,
        "nexus_ref": pinned.ref if pinned else None,
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
        "result_tokens_by_tool": trace.result_tokens_by_tool,
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
    warm: bool = False,
    effort: Optional[str] = None,
    pinned: Optional[PinnedNexus] = None,
) -> List[Dict[str, Any]]:
    """Run the suite, writing each record to `out_path` as it completes.

    Records are appended incrementally so a crash (or Ctrl-C) partway through
    a multi-run — which can cost tens of dollars — keeps everything already
    finished. A single run that raises is captured as an error record and the
    batch continues rather than discarding the whole run. A run that already has an
    error-free record in `out_path` is skipped, so the same command resumes after an
    interruption. A usage-limit error stops the batch and is re-raised.

    With `warm`, a condition that had no call in the last `WARM_UP_TTL_S` seconds gets
    one `warm_up` call before its run, so that each recorded run starts with a warm
    prompt cache.
    """
    repo = suite["repo"]
    repo_dir = repo_dir_for(suite)
    if not repo_dir.exists():
        raise SystemExit(
            f"Repo not found at {repo_dir}. Run benchmarks/setup_repos.sh first."
        )

    if pinned is not None and any(cond.split_condition(n)[1] for n in condition_names):
        raise SystemExit("--nexus-venv measures one install; do not combine it with "
                         "--embedding-models")
    if pinned is not None and any(n != "baseline" for n in condition_names):
        ensure_pinned_index(pinned, repo_dir)
    for name in condition_names:
        embedding_model = cond.split_condition(name)[1]
        if embedding_model and not storage_dir_for(repo_dir, embedding_model).exists():
            raise SystemExit(
                f"No {embedding_model} index for {repo_dir.name}. Build it first:\n"
                f"  PYTHONPATH=src:. python -m benchmarks.preindex_models "
                f"--repo {repo_dir.name} --models {embedding_model}"
            )

    tasks = suite["tasks"]
    if task_ids:
        tasks = [t for t in tasks if t["id"] in task_ids]

    defaults = suite.get("defaults", {})
    records = []
    total = len(tasks) * len(condition_names) * reps
    done = 0
    finished = done_keys(out_path, model, effort)
    version = claude_version()
    last_call: Dict[str, float] = {}
    for task, condition, rep in run_order(tasks, condition_names, reps):
        merged_task = {**defaults, **task}
        done += 1
        if (merged_task["id"], condition, rep) in finished:
            continue
        print(
            f"[{done}/{total}] {merged_task['id']} / {condition} / rep {rep + 1}",
            file=sys.stderr,
        )
        if warm and time.time() - last_call.get(condition, 0.0) > WARM_UP_TTL_S:
            print(f"    warm-up call for {condition}", file=sys.stderr)
            warm_up(condition, repo_dir, model, config_dir, effort, pinned)
        try:
            record = run_once(
                merged_task, condition, repo, repo_dir, model, config_dir,
                version=version, raw_dir=raw_dir, rep=rep, effort=effort,
                pinned=pinned,
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
        last_call[condition] = time.time()
        record["rep"] = rep
        record["warm_cache"] = warm
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
    parser.add_argument("--tasks", help="Path to a task suite YAML file")
    parser.add_argument(
        "--repo-path", type=Path, default=None,
        help="Run on this local folder instead of benchmarks/repos/<name>. The nexus "
        "conditions index it on first use unless it already has an index",
    )
    parser.add_argument(
        "--generate", type=int, default=0, metavar="N",
        help="Make N tasks from the --repo-path folder's own symbols (no --tasks needed). "
        "The suite is saved to results/generated-<folder>.yaml for reruns",
    )
    parser.add_argument(
        "--conditions", default=",".join(DEFAULT_CONDITIONS), help="Comma-separated conditions"
    )
    parser.add_argument(
        "--embedding-models", default="",
        help="Comma-separated embedding models. Each nexus condition runs once per model "
        "(needs an index from preindex_models.py). Empty: the server's default model",
    )
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--effort", default=cond.DEFAULT_EFFORT,
        help="Effort level of every condition (low, medium, high, xhigh, max). One level "
        "for both sides; empty string: the CLI default",
    )
    parser.add_argument(
        "--nexus-venv", type=Path, default=None,
        help="Measure the Nexus-MCP install in this venv (benchmarks/install_nexus.sh), "
        "with its own index outside the corpus. Default: nexus-mcp-ci on PATH",
    )
    parser.add_argument(
        "--smoke", action="store_true", help="Run 2 tasks x 1 rep for a cheap sanity check"
    )
    parser.add_argument(
        "--task-ids", default="",
        help="Comma-separated task ids: run only these tasks of the suite",
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
    parser.add_argument(
        "--warm-up", action="store_true",
        help="Send one unrecorded call for a condition before its run when its prompt "
        "cache is cold, so that the cost of a task does not depend on its place",
    )
    args = parser.parse_args(argv)

    if args.generate and not args.repo_path:
        parser.error("--generate needs --repo-path")
    if not args.generate and not args.tasks:
        parser.error("give --tasks, or --repo-path with --generate N")
    if args.generate:
        suite = generate_suite(args.repo_path, args.generate)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        saved = RESULTS_DIR / f"generated-{suite['repo']['name']}.yaml"
        saved.write_text(yaml.safe_dump(suite, sort_keys=False))
        print(f"Generated {len(suite['tasks'])} tasks: {saved}", file=sys.stderr)
    else:
        suite = load_task_suite(Path(args.tasks))
        if args.repo_path:
            suite = use_repo_path(suite, args.repo_path)
    condition_names = cond.expand_conditions(
        [c.strip() for c in args.conditions.split(",") if c.strip()],
        [m.strip() for m in args.embedding_models.split(",") if m.strip()],
    )

    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] or None
    if task_ids:
        missing = set(task_ids) - {t["id"] for t in suite["tasks"]}
        if missing:
            parser.error(f"--task-ids not in the suite: {', '.join(sorted(missing))}")
        suite["tasks"] = [t for t in suite["tasks"] if t["id"] in task_ids]
    reps = args.reps
    if args.smoke:
        task_ids = [t["id"] for t in suite["tasks"][:2]]
        reps = 1

    pinned = load_pinned(args.nexus_venv) if args.nexus_venv else None
    effort = args.effort or None
    config_dir = BENCH_DIR / ".claude-bench"
    require_login(config_dir)
    default_out = RESULTS_DIR / f"runs-{suite['repo']['name']}.jsonl"
    out_path = Path(args.out) if args.out else default_out
    raw_dir = RESULTS_DIR / "raw" / out_path.stem if args.save_raw else None
    try:
        records = run_suite(
            suite, condition_names, reps, args.model, config_dir, out_path, task_ids,
            raw_dir=raw_dir, warm=args.warm_up, effort=effort, pinned=pinned,
        )
    except UsageLimitReached:
        return 3
    print(f"Wrote {len(records)} records to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
