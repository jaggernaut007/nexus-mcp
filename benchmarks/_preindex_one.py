"""Pre-index a single benchmark repo and record wall-clock setup cost.

Invoked by setup_repos.sh with PYTHONPATH already pointing at src/, so
nexus_mcp is importable without additional sys.path surgery here.

Usage: python3 _preindex_one.py <repo_path> <repo_name> <meta_json_path>
"""

import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    repo_path, repo_name, meta_file = sys.argv[1], sys.argv[2], sys.argv[3]

    # nexus_mcp's default storage_dir (".nexus") is resolved relative to the
    # process CWD, not the indexed codebase path. The runner later launches
    # `claude`/nexus-mcp-ci with cwd=repo_dir, which looks for `.nexus` under
    # repo_dir using the SAME default — so the index built here must land
    # there too, or every nexus-condition run re-indexes from scratch inside
    # its (budget-capped, timed) measurement window. Must be set before
    # Settings() is constructed (create_server() below triggers that).
    os.environ.setdefault("NEXUS_STORAGE_DIR", str(Path(repo_path) / ".nexus"))

    from nexus_mcp.server import create_server

    mcp = create_server()
    # NOTE: reaches into FastMCP internals (`_local_provider._components`), same
    # as self_test/demo_mcp.py. Fragile to a FastMCP upgrade — if a version bump
    # moves the tool registry, this AttributeErrors loudly (setup tooling, out of
    # the shipped package, so a hard failure here is acceptable).
    tool_map = {}
    for key, component in mcp._local_provider._components.items():
        if key.startswith("tool:"):
            tool_map[component.name] = component.fn

    start = time.time()
    result = tool_map["index"](repo_path)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    elapsed = time.time() - start

    if isinstance(result, dict) and result.get("error"):
        print(
            f"[setup] index() reported an error for {repo_name}: {result['error']}",
            file=sys.stderr,
        )
        sys.exit(1)

    meta_path = Path(meta_file)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    entries = {}
    if meta_path.exists():
        try:
            entries = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            entries = {}
    result_summary = None
    if isinstance(result, dict):
        result_summary = {k: v for k, v in result.items() if k != "error"}
    entries[repo_name] = {
        "index_seconds": round(elapsed, 1),
        "result": result_summary,
    }
    meta_path.write_text(json.dumps(entries, indent=2, default=str))

    print(f"[setup] {repo_name} indexed in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
