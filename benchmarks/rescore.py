"""Recompute the scores of recorded runs from their stored answers. No Claude call, no cost.

Use it after a change to `benchmarks/scoring.py` or to a task's ground truth, so old runs and
new runs are scored by the same rules:

    python -m benchmarks.rescore --tasks benchmarks/tasks/private/jobscout.yaml \\
        --runs benchmarks/results/runs-jobscout.jsonl

The input file is never changed. The result goes to `<runs>.rescored.jsonl` (or `--out`). Each
record that changed keeps its earlier verdict in `mechanical_correct_before`.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from benchmarks import runner, scoring

SCORE_FIELDS = (
    "file_recall", "fact_score", "combined", "mechanical_correct", "wasted_read_ratio",
)


def rescore_record(
    record: Dict[str, Any], task: Dict[str, Any], repo_root: Optional[str] = None
) -> Dict[str, Any]:
    """A copy of `record` with its score fields recomputed from the stored answer.

    A record with no `final_answer` (a failed run) is returned unchanged: there is nothing
    to score. When a score field changes, the old `mechanical_correct` is kept in
    `mechanical_correct_before`.
    """
    if not record.get("final_answer"):
        return dict(record)
    score = scoring.score_run(
        record.get("files_touched") or [],
        record["final_answer"],
        task["ground_truth"],
        repo_root=repo_root,
    )
    updated = dict(record)
    updated.update({k: score[k] for k in SCORE_FIELDS})
    if any(updated.get(k) != record.get(k) for k in SCORE_FIELDS):
        updated["mechanical_correct_before"] = record.get("mechanical_correct")
    return updated


def rescore_all(
    records: Iterable[Dict[str, Any]], suite: Dict[str, Any], repo_root: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rescore every record of one suite. Returns (all records, the ones whose verdict changed).

    A record whose task is not in the suite raises KeyError: scoring it with another task's
    ground truth would be silent nonsense.
    """
    tasks = {t["id"]: t for t in suite["tasks"]}
    out: List[Dict[str, Any]] = []
    flipped: List[Dict[str, Any]] = []
    for record in records:
        new = rescore_record(record, tasks[record["task_id"]], repo_root)
        out.append(new)
        if new.get("mechanical_correct") != record.get("mechanical_correct"):
            flipped.append(new)
    return out, flipped


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tasks", required=True, help="The task suite YAML the runs used")
    parser.add_argument("--runs", required=True, help="JSONL file of recorded runs")
    parser.add_argument("--out", help="Output JSONL (default: <runs>.rescored.jsonl)")
    args = parser.parse_args(argv)

    runs = Path(args.runs)
    out = Path(args.out) if args.out else runs.with_suffix(".rescored.jsonl")
    if out.resolve() == runs.resolve():
        parser.error("--out must not be the input file")

    suite = runner.load_task_suite(Path(args.tasks))
    records = [json.loads(line) for line in runs.read_text().splitlines() if line.strip()]
    rescored, flipped = rescore_all(records, suite, str(runner.repo_dir_for(suite)))
    out.write_text("".join(json.dumps(r, default=str) + "\n" for r in rescored))

    print(f"{len(rescored)} records rescored -> {out}")
    print(f"{len(flipped)} changed verdict:")
    for r in flipped:
        print(
            f"  {r['task_id']:34} {r['condition']:30} "
            f"{r.get('mechanical_correct_before')} -> {r['mechanical_correct']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
