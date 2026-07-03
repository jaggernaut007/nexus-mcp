"""Aggregate runner.py's JSONL output into a README-ready report.

Usage:
    python -m benchmarks.report benchmarks/results/runs-*.jsonl
    python -m benchmarks.report benchmarks/results/runs-123.jsonl --out report.md
"""

import argparse
import csv
import glob
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def load_records(paths: Sequence[str]) -> List[Dict[str, Any]]:
    """Load JSONL records from one or more glob patterns.

    Malformed lines (e.g. a partially-written record from a run that crashed
    mid-write) are skipped with a warning rather than aborting the whole
    report — a report over N-1 good records beats no report at all.
    """
    records = []
    for pattern in paths:
        for path in sorted(glob.glob(pattern)):
            with open(path) as f:
                for lineno, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        print(
                            f"Skipping malformed line {path}:{lineno}: {exc}",
                            file=sys.stderr,
                        )
    return records


def median(values: Sequence[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return statistics.median(clean)


def iqr(values: Sequence[float]) -> Optional[float]:
    clean = sorted(v for v in values if v is not None)
    if len(clean) < 2:
        return None
    q1, q3 = statistics.quantiles(clean, n=4)[0], statistics.quantiles(clean, n=4)[2]
    return q3 - q1


def group_by(records: List[Dict[str, Any]], *keys: str) -> Dict[Any, List[Dict[str, Any]]]:
    """Group records by one or more field values.

    A single key groups by that field's scalar value directly
    (`group_by(records, "condition")` -> keys like `"baseline"`). Multiple
    keys group by a tuple of values (`group_by(records, "category",
    "condition")` -> keys like `("impact", "nexus")`).
    """
    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for record in records:
        key = tuple(record.get(k) for k in keys) if len(keys) > 1 else record.get(keys[0])
        groups.setdefault(key, []).append(record)
    return groups


def aggregate_condition(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate all reps of one (task, condition) into a per-task-condition summary."""
    n = len(records)
    correct = sum(1 for r in records if r.get("mechanical_correct"))
    return {
        "n_reps": n,
        "tasks_correct_frac": correct / n if n else None,
        "wasted_read_ratio": median([r.get("wasted_read_ratio") for r in records]),
        "total_tokens": median([r.get("total_tokens") for r in records]),
        "fresh_tokens": median([r.get("fresh_tokens") for r in records]),
        "total_cost_usd": median([r.get("total_cost_usd") for r in records]),
        "num_turns": median([r.get("num_turns") for r in records]),
        "wall_seconds": median([r.get("wall_seconds") for r in records]),
    }


def aggregate_by_condition(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate across ALL tasks for each condition (macro-average of per-task medians)."""
    by_condition: Dict[str, Dict[str, Any]] = {}
    for condition, cond_records in group_by(records, "condition").items():
        per_task = aggregate_by_task(cond_records)
        n_tasks = len(per_task)
        correct = sum(
            1 for v in per_task.values()
            if v["tasks_correct_frac"] and v["tasks_correct_frac"] >= 0.5
        )
        by_condition[condition] = {
            "n_tasks": n_tasks,
            "tasks_correct": correct,
            "wasted_read_ratio": median([v["wasted_read_ratio"] for v in per_task.values()]),
            "wasted_read_ratio_iqr": iqr([v["wasted_read_ratio"] for v in per_task.values()]),
            "total_tokens": median([v["total_tokens"] for v in per_task.values()]),
            "fresh_tokens": median([v["fresh_tokens"] for v in per_task.values()]),
            "total_cost_usd": median([v["total_cost_usd"] for v in per_task.values()]),
            "num_turns": median([v["num_turns"] for v in per_task.values()]),
            "wall_seconds": median([v["wall_seconds"] for v in per_task.values()]),
        }
    return by_condition


def aggregate_by_task(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        task_id: aggregate_condition(recs)
        for task_id, recs in group_by(records, "task_id").items()
    }


def aggregate_by_category(records: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    result = {}
    for (category, condition), recs in group_by(records, "category", "condition").items():
        result[(category, condition)] = aggregate_condition(recs)
    return result


def _fmt(value: Optional[float], suffix: str = "", digits: int = 2) -> str:
    """Format a metric for a markdown cell: em-dash for None, else fixed-digit float."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(records: List[Dict[str, Any]]) -> str:
    if not records:
        return "# Token-efficiency benchmark\n\nNo records found.\n"

    repo = records[0].get("repo", "?")
    sha = records[0].get("repo_sha", "?")
    model = records[0].get("model", "?")
    # rep is 0-indexed; the run count is the max seen + 1, not whatever
    # record happens to be first in the list.
    reps = max((r.get("rep", 0) for r in records), default=0) + 1
    n_tasks = len({r["task_id"] for r in records})

    by_condition = aggregate_by_condition(records)
    by_category = aggregate_by_category(records)

    lines = [
        f"## Token-efficiency benchmark — {repo} @ {sha[:12]}, {model}, "
        f"{n_tasks} tasks, N={reps} reps",
        "",
        "| Condition | Tasks correct | Wasted-read ratio (median) | Tokens-to-answer (median) | "
        "Fresh tokens (median) | Cost/task (median) | Turns (median) | Wall time (median) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for condition, agg in by_condition.items():
        cost = f"${agg['total_cost_usd']:.3f}" if agg["total_cost_usd"] is not None else "—"
        lines.append(
            f"| {condition} | {agg['tasks_correct']}/{agg['n_tasks']} | "
            f"{_fmt(agg['wasted_read_ratio'])} | {_fmt(agg['total_tokens'], digits=0)} | "
            f"{_fmt(agg['fresh_tokens'], digits=0)} | {cost} | "
            f"{_fmt(agg['num_turns'], digits=1)} | {_fmt(agg['wall_seconds'], 's')} |"
        )

    lines += [
        "",
        "### By category",
        "",
        "| Category | Condition | Tasks correct | Wasted-read ratio |",
        "|---|---|---|---|",
    ]
    for (category, condition), agg in sorted(
        by_category.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
    ):
        frac = agg["tasks_correct_frac"]
        correct_pct = f"{frac:.0%}" if frac is not None else "—"
        lines.append(
            f"| {category} | {condition} | {correct_pct} | {_fmt(agg['wasted_read_ratio'])} |"
        )

    return "\n".join(lines) + "\n"


def write_csv(records: List[Dict[str, Any]], out_path: Path) -> None:
    if not records:
        return
    fieldnames = [
        "task_id", "category", "condition", "rep", "repo", "model",
        "mechanical_correct", "wasted_read_ratio", "total_tokens", "fresh_tokens",
        "total_cost_usd", "num_turns", "wall_seconds", "is_error", "result_subtype",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: load JSONL result files, render a markdown report.

    `paths` accepts one or more glob patterns. Writes markdown to --out if
    given, else prints to stdout; optionally also writes --csv. Returns 0.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Glob(s) matching runs-*.jsonl files")
    parser.add_argument("--out", default=None, help="Markdown output path (default: stdout)")
    parser.add_argument("--csv", default=None, help="Optional CSV output path")
    args = parser.parse_args(argv)

    records = load_records(args.paths)
    markdown = render_markdown(records)

    if args.out:
        Path(args.out).write_text(markdown)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(markdown)

    if args.csv:
        write_csv(records, Path(args.csv))
        print(f"Wrote {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
