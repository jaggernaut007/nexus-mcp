"""Copy the log files of each benchmark run into benchmarks/runs/, so they stay in git.

benchmarks/results/ is git-ignored and holds the index folders too (hundreds of MB). This
script copies only the small log files: live records (*.jsonl), tier results (*.json), the
per-run summaries and the console logs given with --log. It leaves out index folders.

The files in benchmarks/runs/ are raw logs for issues and for checks of a result. They are
not published numbers: scripts/publish_results.py reads benchmarks/published/results.json only.

Run: python scripts/archive_runs.py [--log LABEL=PATH ...]
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
RUNS = ROOT / "benchmarks" / "runs"
SKIP_DIRS = {"retrieval-work", "indexes", "mcp-configs"}
SUFFIXES = {".json", ".jsonl", ".log"}
# A run log must never carry a credential. `set -x` once echoed the OAuth token into one.
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|oat01-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{20,}")


def run_label(rel: Path) -> str:
    """The run a result file belongs to: `languages-final/x.jsonl` -> `languages-final`."""
    first = rel.parts[0]
    if first.startswith("setup_meta-"):
        return "setup"  # one file per pinned install: version, commit, install time
    return first[: -len(".json")] if first.endswith(".json") else first


def log_files(results: Path):
    """Yield the log files under `results`, without index folders."""
    for path in sorted(results.rglob("*")):
        rel = path.relative_to(results)
        if path.is_file() and path.suffix in SUFFIXES and not SKIP_DIRS & set(rel.parts):
            yield path, rel


def check_clean(path: Path) -> None:
    """Stop when a file holds a credential-like string."""
    if SECRET.search(path.read_text(errors="ignore")):
        sys.exit(f"Not archived: {path} holds a credential-like string. Redact it first.")


def archive(results: Path = RESULTS, runs: Path = RUNS, logs: dict | None = None) -> list[Path]:
    """Copy each log file to runs/<run>/<file>. Returns the written paths."""
    written = []
    for path, rel in log_files(results):
        check_clean(path)
        label = run_label(rel)
        dest = runs / label / (rel.name if len(rel.parts) == 1 else Path(*rel.parts[1:]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        written.append(dest)
    for label, log in (logs or {}).items():
        check_clean(Path(log))
        dest = runs / label / "console.log"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log, dest)
        written.append(dest)
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--log", action="append", default=[], metavar="LABEL=PATH",
                        help="A console log to keep as benchmarks/runs/LABEL/console.log")
    args = parser.parse_args(argv)
    logs = dict(item.split("=", 1) for item in args.log)
    written = archive(logs=logs)
    print(f"archived {len(written)} files in {RUNS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
