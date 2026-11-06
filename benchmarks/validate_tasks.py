"""Check the ground truth of the live task suites against the pinned corpus checkouts.

    python -m benchmarks.validate_tasks [--languages Rust,Go]

For each live corpus of benchmarks/corpora.yaml and each task it uses, this checks that:
  - every file in relevant_files, acceptable_extra_files and must_mention_files exists;
  - each fact has at least one `any_of` string in the text of those files (a number must be
    a line that exists in one of them);
  - the repo pin of the task suite is the pin of the manifest.
It costs nothing and runs offline. A corpus that is not cloned is reported, not failed.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from benchmarks import corpora

ROOT = Path(__file__).resolve().parent.parent


def _files(truth: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("relevant_files", "acceptable_extra_files", "must_mention_files"):
        out += truth.get(key) or []
    return sorted(set(out))


def check_task(task: Dict[str, Any], repo_dir: Path) -> List[str]:
    """Problems of one task against a checkout. An empty list means the task is valid."""
    truth = task.get("ground_truth") or {}
    problems = []
    texts = []
    for rel in _files(truth):
        path = repo_dir / rel
        if not path.is_file():
            problems.append(f"{task['id']}: missing file {rel}")
        else:
            texts.append(path.read_text(errors="ignore"))
    corpus = "\n".join(texts)
    longest = max((t.count("\n") + 1 for t in texts), default=0)
    for fact in truth.get("facts") or []:
        options = [str(o) for o in fact.get("any_of", [])]
        paths = " ".join(_files(truth))
        found = any(
            o in corpus or o in paths or (o.isdigit() and int(o) <= longest) for o in options
        )
        if texts and not found:
            problems.append(f"{task['id']}: no fact option found in the files: {options}")
    return problems


def validate(languages: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """Problems per live corpus name. A corpus without a checkout gets one 'not cloned' line."""
    result: Dict[str, List[str]] = {}
    for corpus in corpora.select(corpora.load(), languages, tier="live"):
        repo_dir = corpora.repo_dir(corpus)
        suite = yaml.safe_load((ROOT / corpus["tasks"]).read_text())
        problems = []
        if suite["repo"].get("pin") != corpus["pin"]:
            problems.append(f"pin of {corpus['tasks']} differs from corpora.yaml")
        tasks = suite["tasks"]
        wanted = corpus.get("task_ids")
        if wanted:
            known = {t["id"] for t in tasks}
            problems += [f"unknown task id {t}" for t in wanted if t not in known]
            tasks = [t for t in tasks if t["id"] in wanted]
        elif len(tasks) != 6:
            problems.append(f"{corpus['tasks']} has {len(tasks)} tasks, expected 6")
        if not repo_dir.is_dir():
            problems.append("not cloned (python -m benchmarks.corpora setup)")
        else:
            for task in tasks:
                problems += check_task(task, repo_dir)
        result[corpus["name"]] = problems
    return result


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: print the problems per corpus; exit 1 if there are any."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", default="")
    args = parser.parse_args(argv)
    langs = [s for s in args.languages.split(",") if s.strip()] or None
    result = validate(langs)
    bad = 0
    for name, problems in result.items():
        print(f"{name}: {'ok' if not problems else str(len(problems)) + ' problem(s)'}")
        for p in problems:
            print(f"  {p}")
        bad += len(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
