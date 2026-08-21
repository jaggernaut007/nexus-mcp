"""Transport-agnostic core logic for Nexus-MCP's tools.

Every function here is a plain, importable Python function with no dependency
on FastMCP, MCP's wire protocol, or a running server process. `server.py`'s
`@mcp.tool()` functions are thin wrappers around these that add MCP-specific
concerns (permission/rate-limit guards, audit logging, `Context` progress
bridging). Any other Python process — including an in-process agent harness —
can import this module directly and call the same functions server.py calls,
with no subprocess, stdio, or protocol overhead.

Keep this module's function names/signatures the source of truth for tool
behavior; server.py should never re-implement logic that belongs here.
"""

import logging
import re
import resource
import sys
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional

from nexus_mcp import __version__
from nexus_mcp.core.graph_models import UniversalNode, UniversalRelationship

logger = logging.getLogger(__name__)

# Module-level pipeline reference (persists across calls, mirrors server.py's
# previous module-level `_pipeline`/`_pipeline_lock`).
_pipeline = None
_pipeline_lock = threading.Lock()


# --- Background reindex / staleness -----------------------------------------

def _trigger_background_reindex(
    codebase_path: Optional[Path], codebase_paths: Optional[list] = None
) -> None:
    """Kick off an incremental reindex on a daemon thread, without blocking the caller.

    Skips (rather than queues or blocks) if a foreground index() is already running
    or another background reindex is already in flight — both hold `_pipeline_lock`
    for their full duration, so a non-blocking acquire here is enough to detect either.
    """
    global _pipeline

    if _pipeline is None or codebase_path is None:
        return

    if not _pipeline_lock.acquire(blocking=False):
        logger.debug("Background reindex skipped: pipeline busy.")
        return

    def _run():
        try:
            from nexus_mcp.state import get_state

            if codebase_paths and len(codebase_paths) > 1:
                _pipeline.multi_index(codebase_paths)
            else:
                _pipeline.incremental_index(codebase_path)

            state = get_state()
            state.vector_engine = _pipeline.vector_engine
            state.bm25_engine = _pipeline.bm25_engine
            state.graph_engine = _pipeline.graph_engine
            state._staleness_cache = None  # force a fresh check on next status()/search()
            logger.info("Background reindex complete for %s", codebase_path)
        except Exception as e:
            logger.warning("Background reindex failed for %s: %s", codebase_path, e)
        finally:
            _pipeline_lock.release()

    threading.Thread(target=_run, daemon=True, name="nexus-bg-reindex").start()


def _get_staleness(state) -> dict:
    """Throttled staleness check — reuses the cached result within
    settings.staleness_check_interval_s to avoid a filesystem walk on every call.
    """
    import time

    global _pipeline

    if _pipeline is None or not state.is_indexed:
        return {"stale": False, "changed_files": 0, "reason": None}

    now = time.monotonic()
    interval = state.settings.staleness_check_interval_s
    if state._staleness_cache is not None and (now - state._staleness_checked_at) < interval:
        return state._staleness_cache

    roots = state.codebase_paths or [state.codebase_path]
    result = _pipeline.check_staleness(roots)
    state._staleness_cache = result
    state._staleness_checked_at = now
    return result


async def _ensure_file_watcher(state, auto_watch_enabled: bool) -> None:
    """Start a DebouncedFileWatcher per indexed root, if enabled and not already running."""
    if not auto_watch_enabled or state._file_watchers:
        return

    roots = state.codebase_paths or ([state.codebase_path] if state.codebase_path else [])
    if not roots:
        return

    from nexus_mcp.parsing.file_watcher import DebouncedFileWatcher
    from nexus_mcp.parsing.language_registry import get_supported_extensions

    extensions = get_supported_extensions()

    def _make_callback(root: Path):
        def _on_change() -> None:
            _trigger_background_reindex(root, state.codebase_paths)

        return _on_change

    for root in roots:
        watcher = DebouncedFileWatcher(
            project_root=root,
            callback=_make_callback(root),
            supported_extensions=extensions,
        )
        try:
            await watcher.start()
            state._file_watchers.append(watcher)
        except Exception as e:
            logger.warning("Failed to start file watcher for %s: %s", root, e)


# --- Input validation ---------------------------------------------------------

def validate_path(path: str) -> tuple[Optional[Path], Optional[dict]]:
    """Validate and resolve a codebase path.

    Returns (resolved_path, None) on success, or (None, error_dict) on failure.
    """
    if "\x00" in path:
        return None, {"error": "Path contains null bytes."}

    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError) as e:
        return None, {"error": f"Invalid path: {e}"}

    if not resolved.is_dir():
        return None, {"error": f"Not a directory: {path}"}

    return resolved, None


def validate_symbol_name(name: str) -> Optional[dict]:
    """Validate a symbol name. Returns error dict or None if valid."""
    if "\x00" in name:
        return {"error": "Symbol name contains null bytes."}
    if len(name) > 500:
        return {"error": "Symbol name too long (max 500 characters)."}
    if not name or not re.search(r"\w", name):
        return {"error": "Symbol name must contain at least one alphanumeric character."}
    return None


def validate_query(query: str) -> Optional[dict]:
    """Validate a search query. Returns error dict or None if valid."""
    if "\x00" in query:
        return {"error": "Query contains null bytes."}
    if len(query) > 10000:
        return {"error": "Query too long (max 10,000 characters)."}
    if not query.strip():
        return {"error": "Query must not be empty."}
    return None


# --- Shared helpers -------------------------------------------------------

def require_indexed():
    """Check that a codebase is indexed and graph engine is available.

    Returns (state, None) on success, or (None, error_dict) on failure.
    """
    from nexus_mcp.state import get_state

    state = get_state()
    if not state.is_indexed or not state.graph_engine:
        return None, {"error": "No codebase indexed. Run 'index' first."}
    return state, None


def _serialize_node(node: UniversalNode, codebase_path: Optional[Path] = None) -> dict[str, Any]:
    """Convert a UniversalNode to a JSON-serializable dict."""
    file_path = node.location.file_path
    if codebase_path:
        try:
            file_path = str(Path(file_path).relative_to(codebase_path))
        except ValueError:
            pass

    return {
        "id": node.id,
        "name": node.name,
        "type": node.node_type.value,
        "language": node.language,
        "location": {
            "file": file_path,
            "start_line": node.location.start_line,
            "end_line": node.location.end_line,
        },
        "complexity": node.complexity,
        "line_count": node.line_count,
        "docstring": node.docstring,
        "visibility": node.visibility,
        "is_async": node.is_async,
        "return_type": node.return_type,
        "parameter_types": node.parameter_types,
    }


def _serialize_relationship(rel: UniversalRelationship) -> dict[str, Any]:
    """Convert a UniversalRelationship to a JSON-serializable dict."""
    return {
        "type": rel.relationship_type.value,
        "source_id": rel.source_id,
        "target_id": rel.target_id,
        "strength": rel.strength,
    }


def _resolve_symbol(graph, name: str, exact: bool = True) -> List[UniversalNode]:
    """Find nodes by name in the graph."""
    return graph.find_nodes_by_name(name, exact=exact)


def _filter_location_list(items: list, filter_path: str) -> list:
    """Filter a list of dicts by location string prefix."""
    return [item for item in items if item.get("location", "").startswith(filter_path)]


def _relativize_location_str(location: str, root: Path) -> str:
    """Make a 'filepath:line' location string relative to root."""
    if ":" in location:
        fp, rest = location.rsplit(":", 1)
        try:
            fp = str(Path(fp).relative_to(root))
        except ValueError:
            pass
        return f"{fp}:{rest}"
    return location


# --- Discovery & indexing ---------------------------------------------------

def status() -> dict[str, Any]:
    """Whether a codebase is indexed, index size/engine availability, memory
    usage, and a stale/staleness_warning pair if files changed since the last
    index (a background reindex is auto-triggered)."""
    from nexus_mcp.state import get_state

    state = get_state()
    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        peak_rss_mb = rss_raw / (1024 * 1024)
    else:
        peak_rss_mb = rss_raw / 1024

    result: dict[str, Any] = {
        "version": __version__,
        "indexed": state.is_indexed,
        "codebase_path": str(state.codebase_path) if state.codebase_path else None,
        "memory": {"peak_rss_mb": round(peak_rss_mb, 1)},
    }

    if state.is_indexed:
        if state.vector_engine:
            result["vector_chunks"] = state.vector_engine.count()
        if state.bm25_engine:
            result["bm25_fts_ready"] = state.bm25_engine._fts_index_created
        if state.graph_engine:
            result["graph"] = state.graph_engine.get_statistics()

        staleness = _get_staleness(state)
        result["stale"] = staleness["stale"]
        if staleness["stale"]:
            result["staleness_warning"] = (
                f"Index may be out of date ({staleness['reason']}). "
                "A background reindex has been triggered."
            )
            _trigger_background_reindex(state.codebase_path, state.codebase_paths)
        else:
            result["staleness_warning"] = None

        result["hint"] = (
            "Codebase is indexed. Use 'search' to find code (preferred over Grep/Glob), "
            "'find_symbol' for definitions, 'graph' for the call graph, "
            "'explain' for understanding symbols, 'graph(transitive=True)' before refactoring."
        )
    else:
        result["hint"] = (
            "Codebase not indexed. Run 'index' first to enable "
            "semantic search, call graphs, and code analysis."
        )

    return result


def health() -> dict[str, Any]:
    """Liveness probe — uptime, which engines are up. Not for freshness; use status()."""
    import time

    from nexus_mcp.state import get_state

    state = get_state()
    uptime = time.time() - state.started_at

    return {
        "status": "healthy",
        "uptime_seconds": round(uptime, 1),
        "indexed": state.is_indexed,
        "engines": {
            "vector": state.vector_engine is not None,
            "bm25": state.bm25_engine is not None,
            "graph": state.graph_engine is not None,
            "memory": state.memory_store is not None,
        },
    }


async def index(
    path: str,
    paths: str = "",
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict[str, Any]:
    """Index (or incrementally reindex) one or more codebase roots.

    `progress_callback(stage, info)`, if given, is invoked from whichever
    thread runs the blocking pipeline work (never necessarily the caller's
    thread) — bridge to an event loop/UI yourself if you need that. This
    mirrors the original MCP tool's `ctx.report_progress` bridging, which now
    lives in server.py since it's MCP-transport-specific.
    """
    from nexus_mcp.config import get_settings
    from nexus_mcp.indexing.pipeline import IndexingPipeline
    from nexus_mcp.state import get_state

    global _pipeline

    raw_paths = [p.strip() for p in path.split(",") if p.strip()]
    if paths:
        raw_paths.extend(p.strip() for p in paths.split(",") if p.strip())

    validated: list[Path] = []
    for raw in raw_paths:
        resolved, err = validate_path(raw)
        if err:
            return {"error": f"Invalid path '{raw}': {err['error']}"}
        validated.append(resolved)

    if not validated:
        return {"error": "No valid paths provided."}

    settings = get_settings()
    import asyncio

    def _cb(stage: str, info: dict) -> None:
        if progress_callback is None:
            logger.info("[index] %s: %s", stage, info)
            return
        try:
            progress_callback(stage, info or {})
        except Exception as e:
            logger.debug("progress_callback failed for %s: %s", stage, e)

    await asyncio.to_thread(_pipeline_lock.acquire)
    try:
        if _pipeline is None:
            _pipeline = IndexingPipeline(settings)

        if len(validated) > 1:
            result = await asyncio.to_thread(_pipeline.multi_index, validated, _cb)
            state = get_state()
            state.codebase_path = validated[0]
            state.codebase_paths = validated
        else:
            codebase_path = validated[0]
            metadata_path = settings.storage_path / "index_metadata.json"
            if metadata_path.exists():
                result = await asyncio.to_thread(
                    _pipeline.incremental_index, codebase_path, _cb
                )
            else:
                result = await asyncio.to_thread(_pipeline.index, codebase_path, _cb)
            state = get_state()
            state.codebase_path = codebase_path
            state.codebase_paths = [codebase_path]

        state.vector_engine = _pipeline.vector_engine
        state.bm25_engine = _pipeline.bm25_engine
        state.graph_engine = _pipeline.graph_engine
        state._staleness_cache = None
    finally:
        _pipeline_lock.release()

    await _ensure_file_watcher(state, settings.auto_watch_enabled)

    return result.to_dict()


# --- Search -----------------------------------------------------------------

def search(
    query: str,
    limit: int = 10,
    language: str = "",
    symbol_type: str = "",
    mode: str = "hybrid",
    rerank: bool = True,
    live_grep: bool = False,
) -> dict[str, Any]:
    """Primary code discovery: hybrid vector+BM25+graph search with RRF fusion,
    optional reranking, and an automatic live-grep fallback when results are sparse."""
    from nexus_mcp.config import get_settings
    from nexus_mcp.engines.fusion import ReciprocalRankFusion, graph_relevance_search
    from nexus_mcp.engines.reranker import FlashReranker
    from nexus_mcp.state import get_state

    err = validate_query(query)
    if err:
        return err

    state = get_state()
    if not state.is_indexed or not state.vector_engine:
        return {"error": "No codebase indexed. Run 'index' first."}

    staleness = _get_staleness(state)
    search_warning = None
    if staleness["stale"]:
        search_warning = (
            f"Index may be out of date ({staleness['reason']}); refreshing in background."
        )
        _trigger_background_reindex(state.codebase_path, state.codebase_paths)

    settings = get_settings()
    limit = max(1, min(limit, 100))

    kwargs = {}
    if language:
        kwargs["language"] = language
    if symbol_type:
        kwargs["symbol_type"] = symbol_type

    engines_used = []
    ranked_lists: dict[str, list] = {}
    overfetch = limit * 2

    if mode in ("hybrid", "vector"):
        try:
            vector_results = state.vector_engine.search(query, limit=overfetch, **kwargs)
            ranked_lists["vector"] = vector_results
            engines_used.append("vector")
        except Exception as e:
            logger.warning("Vector search failed: %s", e)

    if mode in ("hybrid", "bm25") and state.bm25_engine:
        try:
            bm25_results = state.bm25_engine.search(query, limit=overfetch, **kwargs)
            if bm25_results:
                ranked_lists["bm25"] = bm25_results
                engines_used.append("bm25")
        except Exception as e:
            logger.warning("BM25 search failed: %s", e)

    if mode == "hybrid" and state.graph_engine:
        try:
            graph_results = graph_relevance_search(state.graph_engine, query, limit=limit * 2)
            if graph_results:
                if language or symbol_type:
                    graph_results = [
                        r
                        for r in graph_results
                        if (not language or r.get("language") == language)
                        and (not symbol_type or r.get("symbol_type") == symbol_type)
                    ]
                if graph_results:
                    ranked_lists["graph"] = graph_results
                    engines_used.append("graph")
        except Exception as e:
            logger.warning("Graph relevance search failed: %s", e)

    if len(ranked_lists) > 1:
        fusion = ReciprocalRankFusion(
            weights={
                "vector": settings.fusion_weight_vector,
                "bm25": settings.fusion_weight_bm25,
                "graph": settings.fusion_weight_graph,
            }
        )
        results = fusion.fuse(ranked_lists)
    elif ranked_lists:
        results = list(ranked_lists.values())[0]
    else:
        results = []

    if rerank and results:
        if not hasattr(state, "_reranker") or state._reranker is None:
            state._reranker = FlashReranker(model_name=settings.reranker_model)
        results = state._reranker.rerank(query, results, limit=limit)
    else:
        results = results[:limit]

    if not symbol_type and (live_grep or (mode == "hybrid" and len(results) < limit)):
        try:
            from nexus_mcp.engines.live_grep import LiveGrepEngine
            from nexus_mcp.parsing.language_registry import get_language_for_file

            live_engine = LiveGrepEngine(str(state.codebase_path))
            live_results = live_engine.search(query, limit=limit)

            if live_results:
                filtered_live = []
                seen = {
                    (r.get("absolute_path") or r.get("filepath", ""), r.get("line_start"))
                    for r in results
                }

                for lr in live_results:
                    abs_p = lr.get("absolute_path") or lr.get("filepath")
                    if not abs_p:
                        continue
                    key = (abs_p, lr.get("line_start"))
                    if key in seen:
                        continue

                    lr_lang = get_language_for_file(abs_p) or "unknown"
                    if language and lr_lang != language:
                        continue

                    lr["language"] = lr_lang
                    filtered_live.append(lr)
                    seen.add(key)

                if filtered_live:
                    results.extend(filtered_live)
                    if "live_grep" not in engines_used:
                        engines_used.append("live_grep")
        except Exception as e:
            logger.warning("Live grep failed: %s", e)

    roots = getattr(state, "codebase_paths", [])
    if not roots and state.codebase_path:
        roots = [state.codebase_path]
    for r in results:
        r.pop("vector", None)

        if "text" in r:
            code = r.pop("text")
            if len(code) > 2000:
                r["code_snippet"] = code[:2000] + "\n... (truncated)"
            else:
                r["code_snippet"] = code

        abs_path = r.get("absolute_path") or r.get("filepath")
        if not abs_path:
            continue

        r["absolute_path"] = str(abs_path)

        for root in roots:
            try:
                r["filepath"] = str(Path(abs_path).relative_to(root))
                break
            except ValueError:
                r["filepath"] = str(abs_path)

        if not r.get("language") or r.get("language") == "unknown":
            from nexus_mcp.parsing.language_registry import get_language_for_file

            r["language"] = get_language_for_file(r["absolute_path"]) or "unknown"

    return {
        "query": query,
        "total": len(results),
        "search_mode": mode,
        "engines_used": engines_used,
        "results": results,
        "warning": search_warning,
        "hint": (
            "Results include code_snippet — you can often answer "
            "without needing to Read the file."
        ),
    }


# --- Graph analysis -----------------------------------------------------------

def find_symbol(name: str, exact: bool = True) -> dict[str, Any]:
    """Look up a symbol by name, with its call-graph relationships."""
    err = validate_symbol_name(name)
    if err:
        return err

    state, err = require_indexed()
    if err:
        return err

    matches = _resolve_symbol(state.graph_engine, name, exact=exact)
    if not matches:
        msg = f"Symbol '{name}' not found."
        if exact:
            msg += " Try exact=False for fuzzy matching."
        return {"error": msg}

    symbols = []
    for node in matches:
        entry = _serialize_node(node, state.codebase_path)
        rels_from = state.graph_engine.get_relationships_from(node.id)
        rels_to = state.graph_engine.get_relationships_to(node.id)
        entry["relationships_out"] = [_serialize_relationship(r) for r in rels_from]
        entry["relationships_in"] = [_serialize_relationship(r) for r in rels_to]
        symbols.append(entry)

    return {"total": len(symbols), "symbols": symbols}


def _graph_immediate(state, symbol_name: str, direction: str) -> dict[str, Any]:
    matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
    if not matches:
        return {"error": f"Symbol '{symbol_name}' not found."}

    get_related = (
        state.graph_engine.get_callers if direction == "callers" else state.graph_engine.get_callees
    )

    all_related = []
    seen: set[str] = set()
    for node in matches:
        for related in get_related(node.id):
            if related.id not in seen:
                seen.add(related.id)
                all_related.append(_serialize_node(related, state.codebase_path))

    return {
        "symbol": symbol_name,
        "direction": direction,
        "total": len(all_related),
        direction: all_related,
    }


def _graph_transitive_impact(state, symbol_name: str, max_depth: int) -> dict[str, Any]:
    max_depth = max(1, min(max_depth, 50))

    matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
    if not matches:
        return {"error": f"Symbol '{symbol_name}' not found."}

    all_impacted = []
    seen: set[str] = set()
    for node in matches:
        for caller in state.graph_engine.get_transitive_callers(node.id, max_depth=max_depth):
            if caller.id not in seen:
                seen.add(caller.id)
                all_impacted.append(_serialize_node(caller, state.codebase_path))

    by_file: dict[str, list[str]] = {}
    for item in all_impacted:
        fp = item["location"]["file"]
        by_file.setdefault(fp, []).append(item["name"])

    return {
        "symbol": symbol_name,
        "direction": "callers",
        "transitive": True,
        "max_depth": max_depth,
        "total_impacted": len(all_impacted),
        "impacted_symbols": all_impacted,
        "impacted_files": by_file,
    }


def graph(
    symbol_name: str,
    direction: str = "callers",
    transitive: bool = False,
    max_depth: int = 10,
) -> dict[str, Any]:
    """Trace callers/callees of a symbol, or (transitive=True, direction='callers')
    the full transitive change-impact blast radius. MUST run transitive=True before
    refactoring a widely-shared symbol."""
    if direction not in ("callers", "callees"):
        return {"error": "direction must be 'callers' or 'callees'."}
    if transitive and direction != "callers":
        return {
            "error": (
                "transitive=True is only supported with direction='callers' "
                "(change-impact analysis). Use direction='callees' with "
                "transitive=False to trace immediate callees."
            )
        }

    err = validate_symbol_name(symbol_name)
    if err:
        return err

    state, err = require_indexed()
    if err:
        return err

    if transitive:
        return _graph_transitive_impact(state, symbol_name, max_depth)
    return _graph_immediate(state, symbol_name, direction)


def analyze(path: str = "") -> dict[str, Any]:
    """Code quality: cyclomatic/cognitive complexity, dependencies, code smells,
    quality score. Optionally scoped to a subdirectory or file via `path`."""
    state, err = require_indexed()
    if err:
        return err

    from nexus_mcp.analysis.code_analyzer import CodeAnalyzer

    analyzer = CodeAnalyzer(state.graph_engine)

    result = {
        "complexity": analyzer.analyze_complexity(),
        "dependencies": analyzer.analyze_dependencies(),
        "code_smells": analyzer.detect_code_smells(),
        "quality": analyzer.calculate_quality_metrics(),
    }

    root = state.codebase_path

    if path and root:
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return {"error": f"Path '{path}' is outside codebase root."}
        filter_path = str(candidate)

        if "high_complexity_functions" in result["complexity"]:
            result["complexity"]["high_complexity_functions"] = _filter_location_list(
                result["complexity"]["high_complexity_functions"], filter_path
            )

        for smell_key in ["long_functions", "complex_functions", "large_classes", "dead_code"]:
            if smell_key in result["code_smells"]:
                result["code_smells"][smell_key] = _filter_location_list(
                    result["code_smells"][smell_key], filter_path
                )

    if root:
        for item in result["complexity"].get("high_complexity_functions", []):
            if "location" in item:
                item["location"] = _relativize_location_str(item["location"], root)

        for smell_key in ["long_functions", "complex_functions", "large_classes", "dead_code"]:
            for item in result["code_smells"].get(smell_key, []):
                if "location" in item:
                    item["location"] = _relativize_location_str(item["location"], root)

    return result


def explain(symbol_name: str, verbosity: str = "detailed") -> dict[str, Any]:
    """Symbol definition + call-graph relationships + related code + quality
    metrics in one call — usually replaces a Read entirely."""
    from nexus_mcp.formatting.response_builder import ResponseBuilder
    from nexus_mcp.formatting.token_budget import TokenBudget

    err = validate_symbol_name(symbol_name)
    if err:
        return err

    if verbosity not in TokenBudget.BUDGETS:
        valid = list(TokenBudget.BUDGETS.keys())
        return {"error": f"Invalid verbosity: {verbosity}. Must be one of {valid}"}

    state, err = require_indexed()
    if err:
        return err

    matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
    if not matches:
        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=False)
    if not matches:
        return {"error": f"Symbol '{symbol_name}' not found."}

    node = matches[0]
    symbol_data = _serialize_node(node, state.codebase_path)

    rels_from = state.graph_engine.get_relationships_from(node.id)
    rels_to = state.graph_engine.get_relationships_to(node.id)
    symbol_data["callers"] = [
        _serialize_node(c, state.codebase_path) for c in state.graph_engine.get_callers(node.id)
    ]
    symbol_data["callees"] = [
        _serialize_node(c, state.codebase_path) for c in state.graph_engine.get_callees(node.id)
    ]
    symbol_data["relationships_out"] = [_serialize_relationship(r) for r in rels_from]
    symbol_data["relationships_in"] = [_serialize_relationship(r) for r in rels_to]

    search_results = []
    if state.vector_engine:
        search_text = node.name
        if node.docstring:
            search_text += " " + node.docstring
        try:
            search_results = state.vector_engine.search(search_text, limit=10)
        except Exception as e:
            logger.debug("Vector search failed in explain: %s", e)

    analysis = {}
    try:
        from nexus_mcp.analysis.code_analyzer import CodeAnalyzer

        analyzer = CodeAnalyzer(state.graph_engine)
        analysis = {
            "complexity": analyzer.analyze_complexity(),
            "quality": analyzer.calculate_quality_metrics(),
        }
    except Exception as e:
        logger.debug("Code analysis failed in explain: %s", e)

    builder = ResponseBuilder(verbosity)
    return builder.build_explain_response(symbol_data, search_results, analysis)


def _build_overview(state) -> dict[str, Any]:
    from nexus_mcp.analysis.code_analyzer import CodeAnalyzer
    from nexus_mcp.core.graph_models import NodeType

    graph_engine = state.graph_engine
    stats = graph_engine.get_statistics()

    languages = {}
    for lang, count in stats.get("nodes_by_language", {}).items():
        languages[lang] = count

    directories: dict[str, int] = {}
    root = state.codebase_path
    for fp in graph_engine._file_nodes:
        try:
            rel = str(Path(fp).relative_to(root).parent)
        except ValueError:
            rel = "."
        directories[rel] = directories.get(rel, 0) + 1

    modules = graph_engine.get_nodes_by_type(NodeType.MODULE)
    module_summaries = []
    for mod in modules:
        rels = graph_engine.get_relationships_from(mod.id)
        child_count = len(rels)
        mod_path = mod.location.file_path
        if root:
            try:
                mod_path = str(Path(mod_path).relative_to(root))
            except ValueError:
                pass
        module_summaries.append(
            {"name": mod.name, "file": mod_path, "symbols": child_count, "lines": mod.line_count}
        )
    module_summaries.sort(key=lambda m: m["symbols"], reverse=True)

    analyzer = CodeAnalyzer(graph_engine)
    quality = analyzer.calculate_quality_metrics()

    chunk_count = 0
    if state.vector_engine:
        chunk_count = state.vector_engine.count()

    return {
        "project_path": str(root) if root else None,
        "total_files": stats.get("total_files", 0),
        "total_symbols": stats.get("total_nodes", 0),
        "total_relationships": stats.get("total_relationships", 0),
        "vector_chunks": chunk_count,
        "symbols_by_type": stats.get("nodes_by_type", {}),
        "languages": languages,
        "directories": dict(sorted(directories.items())),
        "quality": quality,
        "top_modules": module_summaries[:20],
    }


def _build_architecture(state) -> dict[str, Any]:
    from nexus_mcp.analysis.code_analyzer import CodeAnalyzer
    from nexus_mcp.core.graph_models import NodeType, RelationshipType

    graph_engine = state.graph_engine
    root = state.codebase_path
    analyzer = CodeAnalyzer(graph_engine)

    dep_analysis = analyzer.analyze_dependencies()

    classes = graph_engine.get_nodes_by_type(NodeType.CLASS)
    class_info = []
    for cls in classes:
        rels_out = graph_engine.get_relationships_from(cls.id)
        rels_in = graph_engine.get_relationships_to(cls.id)
        methods = sum(1 for r in rels_out if r.relationship_type == RelationshipType.CONTAINS)
        parents = [
            graph_engine.get_node(r.source_id)
            for r in rels_in
            if r.relationship_type == RelationshipType.CONTAINS
        ]
        parent_name = parents[0].name if parents else None

        cls_path = cls.location.file_path
        if root:
            try:
                cls_path = str(Path(cls_path).relative_to(root))
            except ValueError:
                pass

        class_info.append(
            {
                "name": cls.name,
                "file": cls_path,
                "methods": methods,
                "lines": cls.line_count,
                "parent_module": parent_name,
                "visibility": cls.visibility,
            }
        )
    class_info.sort(key=lambda c: c["methods"], reverse=True)

    layers: dict[str, dict[str, Any]] = {}
    modules = graph_engine.get_nodes_by_type(NodeType.MODULE)
    for mod in modules:
        mod_path = mod.location.file_path
        if root:
            try:
                mod_path = str(Path(mod_path).relative_to(root))
            except ValueError:
                pass
        parts = Path(mod_path).parts
        if len(parts) >= 2:
            layer = str(Path(parts[0]) / parts[1]) if len(parts) > 2 else parts[0]
        else:
            layer = parts[0] if parts else "root"

        if layer not in layers:
            layers[layer] = {"modules": [], "total_symbols": 0}
        rels = graph_engine.get_relationships_from(mod.id)
        symbol_count = len(rels)
        layers[layer]["modules"].append(mod.name)
        layers[layer]["total_symbols"] += symbol_count

    entry_patterns = {"main", "run", "start", "handler", "create_server", "app"}
    functions = graph_engine.get_nodes_by_type(NodeType.FUNCTION)
    entry_points = []
    for func in functions:
        if func.name in entry_patterns:
            fp = func.location.file_path
            if root:
                try:
                    fp = str(Path(fp).relative_to(root))
                except ValueError:
                    pass
            entry_points.append({"name": func.name, "file": fp, "line": func.location.start_line})

    hub_symbols = []
    for node in graph_engine.nodes.values():
        in_deg, out_deg = graph_engine.get_node_degree(node.id)
        total_deg = in_deg + out_deg
        if total_deg >= 5:
            fp = node.location.file_path
            if root:
                try:
                    fp = str(Path(fp).relative_to(root))
                except ValueError:
                    pass
            hub_symbols.append(
                {
                    "name": node.name,
                    "type": node.node_type.value,
                    "file": fp,
                    "in_degree": in_deg,
                    "out_degree": out_deg,
                    "total_connections": total_deg,
                }
            )
    hub_symbols.sort(key=lambda h: h["total_connections"], reverse=True)

    complexity = analyzer.analyze_complexity()

    return {
        "layers": {k: v for k, v in sorted(layers.items())},
        "dependencies": dep_analysis,
        "classes": class_info[:30],
        "entry_points": entry_points,
        "hub_symbols": hub_symbols[:20],
        "complexity_summary": {
            "total_functions": complexity.get("total_functions", 0),
            "average_complexity": complexity.get("average_complexity", 0),
            "hotspots": complexity.get("high_complexity_functions", [])[:10],
        },
    }


def map_(detail: str = "summary") -> dict[str, Any]:
    """Project orientation, preferred over Glob/ls/manual browsing. 'summary'
    for a quick look, 'architecture' for design/dependency structure, 'full' for both."""
    if detail not in ("summary", "architecture", "full"):
        return {"error": "detail must be 'summary', 'architecture', or 'full'."}

    state, err = require_indexed()
    if err:
        return err

    if detail == "summary":
        return _build_overview(state)
    if detail == "architecture":
        return _build_architecture(state)
    return {**_build_overview(state), **_build_architecture(state)}


# --- Memory -------------------------------------------------------------------

def _get_memory_store():
    """Lazily initialize the memory store."""
    from nexus_mcp.config import get_settings
    from nexus_mcp.indexing.embedding_service import get_embedding_service
    from nexus_mcp.state import get_state

    state = get_state()
    if state.memory_store is None:
        settings = get_settings()
        embedding_svc = get_embedding_service(settings.embedding_model)
        from nexus_mcp.indexing.embedding_service import EMBEDDING_MODELS
        from nexus_mcp.memory.memory_store import MemoryStore

        model_config = EMBEDDING_MODELS.get(settings.embedding_model, {})
        state.memory_store = MemoryStore(
            db_path=str(settings.lancedb_path),
            embedding_service=embedding_svc,
            vector_dims=model_config.get("dimensions", 768),
        )
    return state.memory_store


def memory_store_action(
    content: str, memory_type: str, tags: str, ttl: str, project: str
) -> dict[str, Any]:
    """Store a semantic memory for later recall."""
    import uuid

    from nexus_mcp.core.models import Memory, MemoryType

    try:
        mem_type = MemoryType.from_string(memory_type)
    except ValueError:
        return {"error": f"Invalid memory_type: {memory_type}"}

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        mem = Memory(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=mem_type,
            project=project,
            tags=tag_list,
            ttl=ttl,
        )
    except ValueError as e:
        return {"error": str(e)}

    store = _get_memory_store()
    mem_id = store.remember(mem)
    return {"id": mem_id, "status": "stored"}


def memory_search_action(query: str, limit: int, memory_type: str, tags: str) -> dict[str, Any]:
    """Search memories by semantic similarity."""
    err = validate_query(query)
    if err:
        return err

    store = _get_memory_store()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    memories = store.recall(
        query=query,
        limit=max(1, min(limit, 50)),
        memory_type=memory_type,
        tags=tag_list,
    )

    return {"query": query, "total": len(memories), "memories": [m.to_dict() for m in memories]}


def memory_delete_action(memory_id: str, tags: str, memory_type: str) -> dict[str, Any]:
    """Delete memories by ID, tags, or type."""
    store = _get_memory_store()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    deleted = store.forget(memory_id=memory_id, tags=tag_list, memory_type=memory_type)
    return {"deleted_count": deleted}


def memory(
    action: str,
    content: str = "",
    query: str = "",
    memory_id: str = "",
    memory_type: str = "",
    tags: str = "",
    ttl: str = "permanent",
    project: str = "default",
    limit: int = 5,
) -> dict[str, Any]:
    """Persist/retrieve project context across sessions. action='store'/'search'/'delete'."""
    if action == "store":
        memory_type = memory_type or "note"
        return memory_store_action(content, memory_type, tags, ttl, project)
    if action == "search":
        return memory_search_action(query, limit, memory_type, tags)
    if action == "delete":
        return memory_delete_action(memory_id, tags, memory_type)
    return {"error": "action must be 'store', 'search', or 'delete'."}
