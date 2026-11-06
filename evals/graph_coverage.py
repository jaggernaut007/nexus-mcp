"""Graph coverage of one stored index: how much structure Nexus-MCP sees per language.

Run it with the Python of the install you measure, so the numbers are that version's:

    benchmarks/.venv-v2.2.2/bin/python evals/graph_coverage.py \
        --storage benchmarks/indexes/.venv-v2.2.2/tokio [--out coverage.json]

It reads the stored index and builds nothing. Per language it gives:
  files          source files with a graph module node
  symbols        tree-sitter symbols (the rows that search and `eval` use)
  functions      function graph nodes (ast-grep)
  classes        class graph nodes (ast-grep)
  calls          CALLS edges that start at a function of the language
  calls_per_fn   calls / functions
  fn_with_calls  share of functions with at least one CALLS edge out

A zero shows a parser gap: `graph` and the callers and callees of `find_symbol` then
return nothing for that language.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict


def _symbols_by_language(storage: Path) -> Dict[str, int]:
    """Tree-sitter symbol rows per language, read from the LanceDB chunks table."""
    import lancedb

    table = lancedb.connect(str(storage / "lancedb")).open_table("chunks")
    return dict(Counter(table.to_arrow().column("language").to_pylist()))


def coverage(storage: Path) -> Dict[str, Any]:
    """The coverage numbers of the index in `storage` (see the module docstring)."""
    os.environ["NEXUS_STORAGE_DIR"] = str(storage)
    os.environ.setdefault("NEXUS_AUTO_WATCH", "false")
    os.environ.setdefault("NEXUS_AUTO_RESTORE", "true")
    from nexus_mcp import core_api
    from nexus_mcp.core.graph_models import NodeType, RelationshipType
    from nexus_mcp.state import get_state

    if not core_api.restore_session():
        raise SystemExit(f"No index to restore in {storage}")
    graph = get_state().graph_engine
    per: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"files": 0, "functions": 0, "classes": 0, "calls": 0, "_callers": set()}
    )
    for node in graph.nodes.values():
        lang = node.language or "unknown"
        if node.node_type == NodeType.MODULE:
            per[lang]["files"] += 1
        elif node.node_type == NodeType.FUNCTION:
            per[lang]["functions"] += 1
        elif node.node_type == NodeType.CLASS:
            per[lang]["classes"] += 1
    for rel in graph.relationships.values():
        if rel.relationship_type != RelationshipType.CALLS:
            continue
        source = graph.nodes.get(rel.source_id)
        if source is None or source.node_type != NodeType.FUNCTION:
            continue
        lang = source.language or "unknown"
        per[lang]["calls"] += 1
        per[lang]["_callers"].add(rel.source_id)

    symbols = _symbols_by_language(storage)
    out = {}
    for lang in sorted(set(per) | set(symbols)):
        row = per[lang]
        fns = row["functions"]
        out[lang] = {
            "files": row["files"],
            "symbols": symbols.get(lang, 0),
            "functions": fns,
            "classes": row["classes"],
            "calls": row["calls"],
            "calls_per_fn": round(row["calls"] / fns, 2) if fns else 0.0,
            "fn_with_calls": round(len(row["_callers"]) / fns, 3) if fns else 0.0,
        }
    return out


def main(argv=None) -> int:
    """CLI: print (and with --out, write) the coverage of one stored index as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True, help="Index folder")
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON here too")
    args = parser.parse_args(argv)
    result = coverage(args.storage.resolve())
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
