"""Measure the peak memory of nexus-mcp in four separate phases.

Usage:
    python scripts/measure_memory.py [PROJECT_DIR] [--model bge-small-en]

Each phase runs in a fresh process, so its peak is its own and does not include an
earlier phase. The phases are:

  index    a full index of PROJECT_DIR (parse, embed, store)
  idle     the server is created and no tool has run
  restore  a new process reattaches to the stored index and answers a graph query
  search   a new process runs one search (this loads the embedding model)

The README target (350 MB) is about a running server, which is `idle`, `restore` and
`search`. Indexing is a separate, higher peak. Peak RSS comes from getrusage, so it is the
highest resident size of the process, imports included.
"""

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

PHASES = ("index", "idle", "restore", "search")


def peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def run_phase(phase: str, project: Path) -> dict:
    """Run one phase in this process and return its peak memory."""
    from nexus_mcp import core_api

    detail = ""
    if phase == "index":
        import asyncio

        result = asyncio.run(core_api.index(str(project)))
        detail = f"{result.get('total_files')} files, {result.get('total_chunks')} chunks"
    elif phase == "idle":
        from nexus_mcp.server import create_server

        create_server()
    elif phase == "restore":
        status = core_api.status()
        graph = core_api.graph("main", direction="callers")
        nodes = status.get("graph", {}).get("total_nodes")
        detail = f"indexed={status['indexed']}, graph nodes={nodes}"
        detail += f", graph query ok={'error' not in graph}"
    elif phase == "search":
        result = core_api.search("where is the configuration read", limit=3, rerank=False)
        detail = f"{len(result.get('results', []))} results"
    return {"phase": phase, "peak_rss_mb": round(peak_rss_mb()), "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default="src/nexus_mcp")
    parser.add_argument("--model", default="")
    parser.add_argument("--phase", choices=PHASES, help="(internal) run one phase and print JSON")
    args = parser.parse_args()
    project = Path(args.project).resolve()

    if args.phase:
        print(json.dumps(run_phase(args.phase, project)))
        return 0

    storage = tempfile.mkdtemp(prefix="nexus-mem-")
    src = Path(__file__).resolve().parent.parent / "src"  # absolute: the child changes directory
    env = {**os.environ, "NEXUS_STORAGE_DIR": storage, "NEXUS_AUTO_RESTORE": "true",
           "PYTHONPATH": str(src)}
    if args.model:
        env["NEXUS_EMBEDDING_MODEL"] = args.model
    print(f"project {project}\nstorage {storage}\n")
    print(f"{'phase':8} {'peak RSS MB':>12}  detail")
    for phase in PHASES:
        out = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(project), "--phase", phase],
            capture_output=True, text=True, env=env, cwd=str(project.parent),
        )
        line = next((ln for ln in reversed(out.stdout.splitlines()) if ln.startswith("{")), None)
        if line is None:
            print(f"{phase:8} {'failed':>12}  {out.stderr.strip().splitlines()[-1:]}")
            continue
        row = json.loads(line)
        print(f"{row['phase']:8} {row['peak_rss_mb']:>12}  {row['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
