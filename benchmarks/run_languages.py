"""Run the per-language benchmark with one pinned Nexus-MCP install. One command, all tiers.

    python -m benchmarks.run_languages --nexus-venv benchmarks/.venv-v2.2.2 --label before
    python -m benchmarks.run_languages --nexus-venv benchmarks/.venv-after --label after \
        --languages C#,PHP --tiers quick,retrieval,graph

Install a version first:  benchmarks/install_nexus.sh <git-ref> <label>

Tiers (per corpus of benchmarks/corpora.yaml):
  quick      `nexus-mcp-ci eval` of the pinned install: Grep against Nexus on generated
             questions. Free, offline, no LLM.
  retrieval  evals/retrieval hit@1 on the language suite, run with the pinned install.
  graph      evals/graph_coverage.py: symbols, graph nodes and CALLS edges per language.
  live       benchmarks/runner.py: `claude -p` with Read, Grep and Glob against the same plus
             Nexus. Paid. Both conditions get the same model and the same effort level.

Results go to benchmarks/results/languages-<label>/ (raw files) and
benchmarks/results/languages-<label>.json (one summary row per corpus). A tier that is done
is skipped on a rerun; delete its file to run it again. The live tier resumes by itself.
Exit code 3 means the usage limit stopped a live run: run the same command later.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks import corpora, runner
from benchmarks import report as bench_report
from benchmarks.conditions import DEFAULT_EFFORT

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
RESULTS_DIR = BENCH_DIR / "results"
ALL_TIERS = ("quick", "retrieval", "graph", "live")
PRODUCT_MODE = "hybrid-source-first"  # the ranking that `search` returns (core_api)
# What `nexus-mcp-ci eval` prints when the install extracts no symbols from the language.
NO_SYMBOLS = "No source code was indexed"


def out_dir_for(label: str) -> Path:
    """Folder of the raw files of one labelled run."""
    return RESULTS_DIR / f"languages-{label}"


def _child_env(pinned: runner.PinnedNexus) -> Dict[str, str]:
    """Environment of a child process that must use the pinned package, not this checkout."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["NEXUS_EMBEDDING_DEVICE"] = "cpu"
    env["NEXUS_AUTO_WATCH"] = "false"
    env["PATH"] = f"{pinned.venv / 'bin'}:{env.get('PATH', '')}"
    return env


def _cached(path: Path) -> Optional[Any]:
    return json.loads(path.read_text()) if path.exists() else None


def tier_quick(corpus, pinned, out_dir: Path, questions: int = 30) -> Dict[str, Any]:
    """Raw JSON of `nexus-mcp-ci eval --json` on the corpus, run by the pinned install."""
    path = out_dir / f"{corpus['name']}-quick.json"
    if (cached := _cached(path)) is not None:
        return cached
    proc = subprocess.run(
        [str(pinned.bin), "eval", str(corpora.repo_dir(corpus)), "--questions", str(questions),
         "--json"],
        capture_output=True, text=True, env=_child_env(pinned), cwd=str(out_dir),
    )
    if proc.returncode != 0 and NO_SYMBOLS in proc.stderr:
        # The install has no tree-sitter symbols for this language. That is a result.
        result = {"unsupported": True, "reason": NO_SYMBOLS}
    elif proc.returncode != 0:
        raise RuntimeError(f"quick tier failed for {corpus['name']}: {proc.stderr[-500:]}")
    else:
        result = json.loads(proc.stdout[proc.stdout.index("{"):])
    path.write_text(json.dumps(result, indent=2))
    return result


def tier_retrieval(corpus, pinned, out_dir: Path, label: str) -> Dict[str, Any]:
    """hit@1 of the corpus's suite, in every mode, run with the pinned install."""
    path = out_dir / f"{corpus['name']}-retrieval.json"
    if (cached := _cached(path)) is not None:
        return cached
    suite = corpus["suite"]
    run_label = f"{label}-{corpus['name']}"
    env = _child_env(pinned)
    env["NEXUS_EVAL_WORK_DIR"] = str(out_dir / "retrieval-work")  # never shared with another run
    proc = subprocess.run(
        [str(pinned.python), "-m", "evals.retrieval.run", "--candidates", "bge-small-en",
         "--suites", suite, "--label", run_label],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    result_file = REPO_ROOT / "evals" / "results" / f"retrieval-{run_label}.json"
    if proc.returncode != 0 or not result_file.exists():
        raise RuntimeError(f"retrieval tier failed for {corpus['name']}: {proc.stderr[-500:]}")
    run = json.loads(result_file.read_text())[0]
    if run.get("error") or suite not in run.get("suites", {}):
        # The install crashed while it indexed the suite (C# on 2.2.2): a result.
        raise RuntimeError(str(run.get("error") or "no result for the suite")[-400:])
    result = run["suites"][suite]
    if "error" in result:
        raise RuntimeError(f"retrieval tier failed for {corpus['name']}: {result['error']}")
    path.write_text(json.dumps(result, indent=2))
    return result


def tier_graph(corpus, pinned, out_dir: Path) -> Dict[str, Any]:
    """Graph coverage of the corpus's pinned index (evals/graph_coverage.py)."""
    path = out_dir / f"{corpus['name']}-graph.json"
    if (cached := _cached(path)) is not None:
        return cached
    storage = runner.pinned_index_dir(pinned, corpora.repo_dir(corpus))
    proc = subprocess.run(
        [str(pinned.python), str(REPO_ROOT / "evals" / "graph_coverage.py"), "--storage",
         str(storage), "--out", str(path)],
        capture_output=True, text=True, env=_child_env(pinned), cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"graph tier failed for {corpus['name']}: {proc.stderr[-500:]}")
    return json.loads(path.read_text())


def tier_live(corpus, pinned, out_dir: Path, args) -> Dict[str, Any]:
    """Paid A/B on the corpus's 6 tasks. Returns the per-condition aggregate."""
    out = out_dir / f"{corpus['name']}-live.jsonl"
    cmd = [
        sys.executable, "-m", "benchmarks.runner", "--tasks", str(REPO_ROOT / corpus["tasks"]),
        "--conditions", "baseline,nexus", "--reps", str(args.reps), "--model", args.model,
        "--effort", args.effort, "--nexus-venv", str(pinned.venv), "--out", str(out),
        "--warm-up",
    ]
    if corpus.get("task_ids"):
        cmd += ["--task-ids", ",".join(corpus["task_ids"])]
    code = subprocess.run(cmd, cwd=str(REPO_ROOT)).returncode
    if code == 3:
        raise runner.UsageLimitReached("usage limit; rerun the same command later")
    if code != 0:
        raise RuntimeError(f"live tier failed for {corpus['name']} (exit {code})")
    records = bench_report.load_records([str(out)])
    return {
        "model": args.model,
        "effort": args.effort,
        "reps": args.reps,
        "isolation": sorted({r.get("isolation_mode") for r in records if r.get("isolation_mode")}),
        "errors": sum(1 for r in records if r.get("is_error") or r.get("run_error")),
        "conditions": bench_report.aggregate_by_condition(records),
    }


def index_meta(pinned: runner.PinnedNexus, corpus) -> Optional[Dict[str, Any]]:
    """Index time and peak memory recorded when the pinned index was built."""
    meta = _cached(RESULTS_DIR / f"setup_meta-{pinned.venv.name}.json") or {}
    return meta.get(corpora.repo_dir(corpus).name)


def _tier(row: Dict[str, Any], name: str, fn) -> None:
    """Run one tier and store its result in `row`.

    A failure is stored as {"error": ...}: an install that crashes on a language is a result
    to publish, not a reason to stop. The usage limit is the one error that stops the batch.
    """
    try:
        row[name] = fn()
    except runner.UsageLimitReached:
        raise
    except (RuntimeError, SystemExit, KeyError, ValueError) as exc:
        row[name] = {"error": str(exc)[-400:] or type(exc).__name__}
        print(f"[{row['name']}] {name} tier failed: {row[name]['error']}", file=sys.stderr)


def run_corpus(corpus, pinned, tiers: List[str], label: str, args) -> Dict[str, Any]:
    """Run the chosen tiers on one corpus and return its summary row."""
    out_dir = out_dir_for(label)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpora.checkout(corpus)
    available = [t for t in tiers if t in corpus["tiers"]]
    row: Dict[str, Any] = {
        "name": corpus["name"], "language": corpus["language"], "repo": corpus["repo"],
        "url": corpus["url"], "pin": corpus["pin"], "label": label,
        "nexus_version": pinned.version, "nexus_ref": pinned.ref,
    }
    if "quick" in available:
        _tier(row, "quick", lambda: tier_quick(corpus, pinned, out_dir))
    if "retrieval" in available:
        _tier(row, "retrieval", lambda: tier_retrieval(corpus, pinned, out_dir, label))
    if any(t in available for t in ("graph", "live")):
        _tier(row, "index", lambda: str(
            runner.ensure_pinned_index(pinned, corpora.repo_dir(corpus))))
        row["index_meta"] = index_meta(pinned, corpus)
        if isinstance(row["index"], dict):  # {"error": ...}: no index, so no graph or live
            return row
    if "graph" in available:
        _tier(row, "graph", lambda: tier_graph(corpus, pinned, out_dir))
    if "live" in available:
        _tier(row, "live", lambda: tier_live(corpus, pinned, out_dir, args))
    return row


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: run the tiers for the chosen languages and write the summary file."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nexus-venv", type=Path, required=True)
    parser.add_argument("--label", required=True, help="before, after, or a release name")
    parser.add_argument("--languages", default="", help="Comma-separated; default all")
    parser.add_argument("--tiers", default=",".join(ALL_TIERS))
    parser.add_argument("--reps", type=int, default=runner.DEFAULT_REPS)
    parser.add_argument("--model", default=runner.DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    args = parser.parse_args(argv)

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unknown = set(tiers) - set(ALL_TIERS)
    if unknown:
        parser.error(f"unknown tiers: {sorted(unknown)}")
    langs = [s for s in args.languages.split(",") if s.strip()] or None
    pinned = runner.load_pinned(args.nexus_venv)
    summary_path = RESULTS_DIR / f"languages-{args.label}.json"
    summary = _cached(summary_path) or {"label": args.label, "rows": {}}
    code = 0
    for corpus in corpora.select(corpora.load(), langs):
        try:
            row = run_corpus(corpus, pinned, tiers, args.label, args)
        except runner.UsageLimitReached as exc:
            print(str(exc), file=sys.stderr)
            code = 3
            break
        if any(isinstance(v, dict) and "error" in v for v in row.values()):
            code = code or 1
        summary["rows"][corpus["name"]] = {**summary["rows"].get(corpus["name"], {}), **row}
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2))
        print(f"[{corpus['name']}] done: {', '.join(t for t in tiers if t in row)}",
              file=sys.stderr)
    print(f"Wrote {summary_path}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
