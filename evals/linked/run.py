"""Edge eval of linked repos: precision and recall of the HTTP edges (ADR-027, phase 2.5).

Free and offline. For each suite of `labels.yaml` it parses the repos with the real parser,
resolves the CALLS edges, runs the service resolver and compares the HTTP edges with the
list that a person wrote by hand.

    python -m evals.linked.run                    # every suite; exit 1 if a gate fails
    python -m evals.linked.run --suites linked_fixture --json

Precision is the share of predicted edges that are labelled. Recall is the share of labelled
edges that are predicted. The gate of the plan is precision >= 0.9 (a wrong edge costs more
than a missing one, ADR-019); recall is reported, not gated. A suite whose repos are not on
disk is skipped with a note, as the retrieval suites that need a clone are.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LABELS_PATH = Path(__file__).with_name("labels.yaml")
MIN_PRECISION = 0.9

Edge = Tuple[str, str]  # (client, route), each `repo:path#name`


def build_graph(root: Path):
    """The graph of one repo as the pipeline builds it, without embeddings or a store."""
    from nexus_mcp.config import Settings
    from nexus_mcp.core.graph_models import UniversalGraph
    from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
    from nexus_mcp.indexing.call_resolver import resolve_calls
    from nexus_mcp.indexing.pipeline import _transfer_graph, discover_files
    from nexus_mcp.parsing.astgrep_parser import AstGrepParser

    universal = UniversalGraph()
    parser = AstGrepParser()
    for path in discover_files(root, Settings()):
        if parser.can_parse(str(path)):
            parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _label(repo: str, node, root: Path) -> str:
    try:
        rel = Path(node.location.file_path).resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(node.location.file_path)
    owner = (node.metadata or {}).get("parent_class")
    name = f"{owner}.{node.name}" if owner else node.name
    return f"{repo}:{rel.as_posix()}#{name}"


def predicted_edges(repos: Dict[str, Path]) -> Dict[Edge, str]:
    """The HTTP edges that the resolver makes for these repos, as (client, route) -> via."""
    from nexus_mcp.indexing.service_resolver import resolve_services

    graphs = {name: build_graph(root) for name, root in repos.items()}
    bridge = resolve_services(graphs, {name: [root] for name, root in repos.items()})
    found: Dict[Edge, str] = {}
    for edge in bridge.edges:
        client = graphs[edge.client_repo].get_node(edge.client_id)
        route = graphs[edge.route_repo].get_node(edge.route_id)
        key = (
            _label(edge.client_repo, client, repos[edge.client_repo]),
            _label(edge.route_repo, route, repos[edge.route_repo]),
        )
        found[key] = f"{edge.rel.metadata['via']} ({edge.rel.metadata['resolution']})"
    return found


def score(predicted: Set[Edge], labelled: Set[Edge]) -> Dict[str, Any]:
    """Precision and recall of the predicted edges against the labelled ones."""
    true_pos = predicted & labelled
    precision = len(true_pos) / len(predicted) if predicted else 1.0
    recall = len(true_pos) / len(labelled) if labelled else 1.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "predicted": len(predicted),
        "labelled": len(labelled),
        "true_positives": len(true_pos),
        "false_positives": sorted(predicted - labelled),
        "missed": sorted(labelled - predicted),
    }


def load_suites(path: Path = LABELS_PATH) -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)["suites"]


def run_suite(suite: Dict[str, Any]) -> Dict[str, Any]:
    repos = {
        name: (Path(p) if Path(p).is_absolute() else REPO_ROOT / p)
        for name, p in suite["repos"].items()
    }
    missing = [str(p) for p in repos.values() if not p.is_dir()]
    if missing:
        return {"skipped": f"repo not found: {', '.join(missing)}"}
    labelled = {(e["client"], e["route"]) for e in suite["edges"]}
    known_miss = {(e["client"], e["route"]) for e in suite["edges"] if e.get("known_miss")}
    found = predicted_edges(repos)
    result = score(set(found), labelled)
    result["known_misses"] = sorted(known_miss)
    result["unexpected_misses"] = [m for m in result["missed"] if tuple(m) not in known_miss]
    result["via"] = {f"{c} -> {r}": v for (c, r), v in sorted(found.items())}
    return result


def run(names: Optional[List[str]] = None) -> Dict[str, Any]:
    suites = load_suites()
    return {
        name: run_suite(suite)
        for name, suite in suites.items()
        if not names or name in names
    }


def gate_failures(results: Dict[str, Any]) -> List[str]:
    """What fails the gate: low precision, no edge at all for a labelled suite, a missed edge.

    Precision alone is 1.0 when nothing is predicted, so a resolver that finds nothing would
    pass it. A missed edge that is not marked `known_miss` is a regression of the recall.
    """
    failures = []
    for name, r in results.items():
        if "skipped" in r:
            continue
        if r["precision"] < MIN_PRECISION:
            failures.append(f"{name}: precision {r['precision']} < {MIN_PRECISION}")
        if r.get("predicted", 0) == 0 and r.get("labelled", 0) > 0:
            failures.append(f"{name}: no edge predicted for {r['labelled']} labelled edges")
        for client, route in r.get("unexpected_misses", []):
            failures.append(f"{name}: missed edge {client} -> {route}")
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Precision and recall of the HTTP edges.")
    parser.add_argument("--suites", default="", help="comma separated suite names")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    names = [n for n in args.suites.split(",") if n]
    results = run(names)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for name, r in results.items():
            if "skipped" in r:
                print(f"{name}: skipped ({r['skipped']})")
                continue
            print(
                f"{name}: precision {r['precision']} recall {r['recall']} "
                f"({r['true_positives']}/{r['predicted']} predicted, "
                f"{r['true_positives']}/{r['labelled']} labelled)"
            )
            for edge in r["false_positives"]:
                print(f"  FALSE POSITIVE {edge[0]} -> {edge[1]}")
            for edge in r["missed"]:
                note = "known miss" if tuple(edge) in map(tuple, r["known_misses"]) else "MISSED"
                print(f"  {note}: {edge[0]} -> {edge[1]}")
    failures = gate_failures(results)
    for failure in failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
