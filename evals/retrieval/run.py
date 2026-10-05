"""Retrieval eval: which embedding model finds the right files?

Usage:
    python -m evals.retrieval.run --candidates bge-small-en,gte-modernbert-int8
    python -m evals.retrieval.run --candidates all --label 2026-10
    python -m evals.retrieval.run --single bge-small-en   # one model, JSON on stdout

Each candidate runs in its own process, so peak memory is that model's own. For each
suite the eval indexes the code once, then ranks files for every query in three modes:
`bm25` (no model, the reference), `vector` (the model alone), `hybrid` (vector, bm25
and graph fused as in production, without the reranker and without live grep) and
`hybrid-no-graph` (the same without the graph list, to measure what the graph adds).

The eval never changes the shipped model registry. Candidates are added to the
in-memory registry of the child process only (see candidates.py).
"""

import argparse
import json
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from evals.retrieval import candidates as cand
from evals.retrieval import metrics

EVAL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVAL_DIR.parent
QUERIES_PATH = Path(__file__).resolve().parent / "queries.yaml"
WORK_DIR = EVAL_DIR / ".claude-eval" / "retrieval"
RESULTS_DIR = EVAL_DIR / "results"
MODES = ("bm25", "vector", "hybrid", "hybrid-no-graph")


LOCAL_QUERIES_PATH = QUERIES_PATH.with_name("queries.local.yaml")


def load_suites(path: Path = QUERIES_PATH) -> Dict[str, Any]:
    """Load the query suites from queries.yaml.

    For the default path, suites in the git-ignored queries.local.yaml (a private
    project) are added when that file exists.
    """
    with open(path) as f:
        suites = yaml.safe_load(f)["suites"]
    if path == QUERIES_PATH and LOCAL_QUERIES_PATH.exists():
        with open(LOCAL_QUERIES_PATH) as f:
            suites.update(yaml.safe_load(f)["suites"])
    return suites


def suite_root(suite: Dict[str, Any]) -> Path:
    """Directory a suite indexes. A relative root is relative to the repository; `~` expands."""
    root = Path(suite["root"]).expanduser()
    return (root if root.is_absolute() else REPO_ROOT / root).resolve()


def kind_of(query: Dict[str, Any]) -> str:
    """`identifier` for a query that names code symbols, else `concept`."""
    return query.get("kind", "concept")


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MiB (macOS reports bytes, Linux KiB)."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def to_relative_files(results: List[Dict[str, Any]], root: Path) -> List[str]:
    """File paths of ranked results, relative to the suite root, in rank order."""
    root = root.resolve()
    files = []
    for item in results:
        raw = item.get("absolute_path") or item.get("filepath")
        if not raw:
            continue
        path = Path(raw)
        try:
            files.append(str(path.resolve().relative_to(root)))
        except ValueError:
            files.append(str(path))
    return files


def rank_files(pipeline, settings, root: Path, query: str, mode: str, limit: int = 10) -> List[str]:
    """Ranked files for one query. Mirrors core_api.search, minus rerank and live grep."""
    from nexus_mcp.engines.fusion import ReciprocalRankFusion, graph_relevance_search

    overfetch = limit * 2
    ranked: Dict[str, list] = {}
    fused_modes = ("hybrid", "hybrid-no-graph")
    if mode == "vector" or mode in fused_modes:
        ranked["vector"] = pipeline.vector_engine.search(query, limit=overfetch)
    if mode == "bm25" or mode in fused_modes:
        found = pipeline.bm25_engine.search(query, limit=overfetch)
        if found:
            ranked["bm25"] = found
    if mode == "hybrid":
        graph = graph_relevance_search(pipeline.graph_engine, query, limit=overfetch)
        if graph:
            ranked["graph"] = graph
    if len(ranked) > 1:
        fusion = ReciprocalRankFusion(
            weights={
                "vector": settings.fusion_weight_vector,
                "bm25": settings.fusion_weight_bm25,
                "graph": settings.fusion_weight_graph,
            }
        )
        results = fusion.fuse(ranked)
    else:
        results = next(iter(ranked.values()), [])
    return to_relative_files(results, root)


def run_candidate(name: str, suite_names: List[str], work_dir: Path = WORK_DIR) -> Dict[str, Any]:
    """Index each suite with one model and score every query. Runs in this process."""
    from nexus_mcp.config import Settings
    from nexus_mcp.indexing import embedding_service as es
    from nexus_mcp.indexing.pipeline import IndexingPipeline

    cand.register(name, es.EMBEDDING_MODELS)
    suites = load_suites()
    out: Dict[str, Any] = {"candidate": name, "suites": {}}
    for suite_name in suite_names:
        suite = suites[suite_name]
        root = suite_root(suite)
        if not root.is_dir():
            out["suites"][suite_name] = {"error": f"root not found: {root}"}
            continue
        settings = Settings(
            storage_dir=str(work_dir / name / suite_name),
            embedding_model=name,
            embedding_device="cpu",
        )
        pipeline = IndexingPipeline(settings)
        started = time.time()
        result = pipeline.index(root)
        index_seconds = time.time() - started

        pipeline.vector_engine.search("warm up the model", limit=1)  # load once, untimed
        per_mode: Dict[str, Any] = {}
        for mode in MODES:
            scores, latencies = [], []
            by_kind: Dict[str, list] = {}
            for q in suite["queries"]:
                t = time.perf_counter()
                files = rank_files(pipeline, settings, root, q["query"], mode)
                latencies.append((time.perf_counter() - t) * 1000)
                score = metrics.score_query(files, set(q["relevant"]))
                scores.append(score)
                by_kind.setdefault(kind_of(q), []).append(score)
            per_mode[mode] = {
                **metrics.mean_metrics(scores),
                "median_query_ms": round(statistics.median(latencies), 1),
                "by_kind": {k: metrics.mean_metrics(v) for k, v in by_kind.items()},
            }
        out["suites"][suite_name] = {
            "files": result.total_files,
            "chunks": result.total_chunks,
            "index_seconds": round(index_seconds, 1),
            "modes": per_mode,
        }
    out["peak_rss_mb"] = round(peak_rss_mb(), 0)
    return out


def render_markdown(runs: List[Dict[str, Any]], suite_names: List[str]) -> str:
    """One table per suite: a row per candidate and mode."""
    lines: List[str] = []
    for suite_name in suite_names:
        lines += [
            f"### {suite_name}",
            "",
            "| Model | Mode | hit@1 | hit@5 | recall@10 | MRR@10 | ms/query | index s "
            "| peak RSS MB |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for run in runs:
            suite = run.get("suites", {}).get(suite_name)
            if not suite or suite.get("error"):
                lines.append(f"| {run['candidate']} | failed | | | | | | | |")
                continue
            for mode in MODES:
                m = suite["modes"][mode]
                lines.append(
                    f"| {run['candidate']} | {mode} | {m['hit@1']:.2f} | {m['hit@5']:.2f} "
                    f"| {m['recall@10']:.2f} | {m['mrr@10']:.2f} | {m['median_query_ms']} "
                    f"| {suite['index_seconds']} | {run['peak_rss_mb']:.0f} |"
                )
        lines.append("")
        lines += _render_by_kind(runs, suite_name)
    return "\n".join(lines)


def _render_by_kind(runs: List[Dict[str, Any]], suite_name: str) -> List[str]:
    """hit@1 split by query kind, so a model that only wins on prose queries shows up."""
    rows = []
    kinds: List[str] = []
    for run in runs:
        suite = run.get("suites", {}).get(suite_name)
        if not suite or suite.get("error"):
            continue
        for mode in MODES:
            by_kind = suite["modes"][mode].get("by_kind", {})
            for kind in by_kind:
                if kind not in kinds:
                    kinds.append(kind)
            rows.append((run["candidate"], mode, by_kind))
    if len(kinds) < 2:
        return []
    kinds.sort()
    lines = [
        f"hit@1 by query kind, {suite_name} (queries per kind in brackets)",
        "",
        "| Model | Mode | " + " | ".join(kinds) + " |",
        "|---|---|" + "---|" * len(kinds),
    ]
    for name, mode, by_kind in rows:
        cells = [
            f"{by_kind[k]['hit@1']:.2f} ({by_kind[k]['queries']})" if k in by_kind else ""
            for k in kinds
        ]
        lines.append(f"| {name} | {mode} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def run_in_subprocess(name: str, suite_names: List[str]) -> Dict[str, Any]:
    """Run one candidate in a child process and return its JSON result (or an error)."""
    cmd = [sys.executable, "-m", "evals.retrieval.run", "--single", name,
           "--suites", ",".join(suite_names)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    for line in reversed(proc.stdout.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
    return {"candidate": name, "suites": {}, "peak_rss_mb": 0, "error": " | ".join(tail)}


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint. With --single, print one JSON line. Otherwise run and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="bge-small-en")
    parser.add_argument("--single", default="", help="Run one candidate here, print JSON")
    parser.add_argument("--suites", default="nexus_mcp,shop_repo")
    parser.add_argument("--label", default="run")
    args = parser.parse_args(argv)
    suite_names = [s.strip() for s in args.suites.split(",") if s.strip()]

    if args.single:
        print(json.dumps(run_candidate(args.single, suite_names)))
        return 0

    names = list(cand.CANDIDATES) if args.candidates == "all" else [
        c.strip() for c in args.candidates.split(",") if c.strip()
    ]
    runs = []
    for name in names:
        print(f"== {name}", file=sys.stderr)
        runs.append(run_in_subprocess(name, suite_names))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"retrieval-{args.label}.json"
    out_path.write_text(json.dumps(runs, indent=2))
    print(render_markdown(runs, suite_names))
    errors = [r for r in runs if r.get("error")]
    for r in errors:
        print(f"{r['candidate']} failed: {r['error']}", file=sys.stderr)
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
