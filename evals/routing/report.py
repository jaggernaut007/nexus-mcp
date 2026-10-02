"""Render routing-eval JSONL records as markdown tables.

Usage:
    python -m evals.routing.report evals/results/routing-baseline.jsonl
    python -m evals.routing.report "evals/results/routing-*.jsonl"
"""

import argparse
import sys
from typing import Any, Dict, List, Optional

from benchmarks.report import group_by, load_records
from evals.routing import scoring


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{round(value * 100)}%"


def _label(tool_search: Any) -> str:
    return "on" if tool_search else "off"


def latest_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the last record for each run, so a retry replaces its failed attempt."""
    latest: Dict[Any, Dict[str, Any]] = {}
    for rec in records:
        key = (rec.get("prompt_id"), rec.get("condition"), rec.get("tool_search"),
               rec.get("rep"))
        latest[key] = rec
    return list(latest.values())


def render_markdown(records: List[Dict[str, Any]]) -> str:
    """Two tables: rates by (condition, tool search), then pass rate by category."""
    records = latest_records(records)
    good = [r for r in records if not r.get("run_error") and not r.get("isolation_problems")]
    errors = len(records) - len(good)
    lines = [
        "| Condition | Tool Search | Runs | Pass | Nexus first | Right tool | Args ok "
        "| Negative ok |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (condition, tool_search), recs in sorted(
        group_by(good, "condition", "tool_search").items(), key=lambda kv: str(kv[0])
    ):
        agg = scoring.aggregate(recs)
        lines.append(
            f"| {condition} | {_label(tool_search)} | {agg['n']} | {_pct(agg['pass_rate'])} "
            f"| {_pct(agg['nexus_first_rate'])} | {_pct(agg['right_tool_rate'])} "
            f"| {_pct(agg['args_ok_rate'])} | {_pct(agg['negative_pass_rate'])} |"
        )

    lines += ["", "| Category | Runs | Pass |", "|---|---|---|"]
    for category, recs in sorted(group_by(good, "category").items(), key=lambda kv: str(kv[0])):
        agg = scoring.aggregate(recs)
        lines.append(f"| {category} | {agg['n']} | {_pct(agg['pass_rate'])} |")

    flagged = [r for r in records if r.get("isolation_problems")]
    denied = [r for r in good if r.get("permission_denials")]
    lines += [
        "",
        f"Runs left out (errors or isolation problems): {errors}. "
        f"Runs with isolation problems: {len(flagged)}. "
        f"Runs with permission denials: {len(denied)}.",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: print the report for one or more JSONL files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    records = load_records(args.paths)
    if not records:
        print("No records found.", file=sys.stderr)
        return 1
    print(render_markdown(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
