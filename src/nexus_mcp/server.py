"""Nexus-MCP FastMCP server with index, search, status, and graph/analysis tools."""

import asyncio
import json as _json
import logging
import re
import resource
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

from nexus_mcp import __version__

if TYPE_CHECKING:
    from nexus_mcp.security.permissions import ToolCategory

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    """JSON structured log formatter for production use."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return _json.dumps(log_entry)

# Module-level pipeline reference (persists across tool calls)
_pipeline = None
_pipeline_lock = threading.Lock()


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
            # Runs as an asyncio.Task on the server's event loop (see
            # DebouncedFileWatcher._debounced_callback) — must stay non-blocking.
            # _trigger_background_reindex only spawns a thread and returns.
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


def create_server():
    """Create and configure the FastMCP server."""
    from fastmcp import Context, FastMCP

    from nexus_mcp.core.graph_models import UniversalNode, UniversalRelationship

    mcp = FastMCP("Nexus-MCP")

    # --- Middleware: permissions, rate limiting, audit ---

    from nexus_mcp.config import get_settings as _get_settings

    _settings = _get_settings()

    # Initialize audit logger
    from nexus_mcp.middleware.audit import AuditLogger

    _audit = AuditLogger(enabled=_settings.audit_enabled)

    # Initialize rate limiter (off by default for stdio)
    _rate_limiter = None
    if _settings.rate_limit_enabled:
        from nexus_mcp.security.rate_limiter import TokenBucketRateLimiter

        _rate_limiter = TokenBucketRateLimiter(
            default_rate=_settings.rate_limit_default_rate,
            default_burst=_settings.rate_limit_default_burst,
        )

    def _check_tool_permission(
        tool_name: str, category_override: Optional["ToolCategory"] = None
    ) -> Optional[dict]:
        """Check if tool is allowed under current permission policy.

        category_override lets a tool whose real category depends on a
        call-time parameter (e.g. `memory(action=...)`) report the correct
        category instead of a single static one for the whole tool name.

        Returns None if allowed, or error dict if denied.
        """
        from nexus_mcp.security.permissions import (
            check_permission,
            get_tool_category,
            policy_from_level,
        )

        policy = policy_from_level(_settings.default_permission_level)
        if not check_permission(tool_name, policy, category_override=category_override):
            category = get_tool_category(tool_name, category_override=category_override)
            cat_name = category.value if category else "unknown"
            return {
                "error": (
                    f"Permission denied: tool '{tool_name}' "
                    f"requires '{cat_name}' access. "
                    f"Set NEXUS_PERMISSION_LEVEL=full to enable."
                )
            }
        return None

    def _check_rate_limit(tool_name: str) -> Optional[dict]:
        """Check rate limit for a tool. Returns error dict if limited."""
        if _rate_limiter is None:
            return None
        if not _rate_limiter.try_acquire(tool_name):
            retry = _rate_limiter.get_retry_after(tool_name)
            return {
                "error": "Rate limit exceeded.",
                "retry_after": round(retry, 2),
            }
        return None

    def _guard(
        tool_name: str, category_override: Optional["ToolCategory"] = None
    ) -> Optional[dict]:
        """Run all pre-execution checks: permissions, rate limiting.

        Returns None if all checks pass, or error dict on first failure.
        Denial dicts carry an internal '_audit_status' key consumed (and
        stripped) by the _audited wrapper.
        """
        err = _check_tool_permission(tool_name, category_override=category_override)
        if err:
            err["_audit_status"] = "permission_denied"
            return err
        err = _check_rate_limit(tool_name)
        if err:
            err["_audit_status"] = "rate_limited"
            return err
        return None

    def _audited(fn):
        """Wrap a tool to emit one audit record per invocation.

        Logs the actual outcome (success/error/permission_denied/rate_limited)
        with real duration and sanitized params, after the tool completes.
        Audit failures never block the tool call. Works for both sync and
        async tool functions — FastMCP tells them apart via
        inspect.iscoroutinefunction, so the wrapper must preserve that.
        """
        import functools
        import time as _time

        from nexus_mcp.middleware.audit import generate_correlation_id

        tool_name = fn.__name__

        def _log(status: str, kwargs: dict, start: float) -> None:
            try:
                _audit.log_invocation(
                    tool_name=tool_name,
                    params=kwargs,
                    result_status=status,
                    duration_ms=(_time.monotonic() - start) * 1000,
                    correlation_id=generate_correlation_id(),
                )
            except Exception as e:
                logger.warning("Audit logging failed for %s: %s", tool_name, e)

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                start = _time.monotonic()
                status = "success"
                try:
                    result = await fn(*args, **kwargs)
                    if isinstance(result, dict):
                        status = result.pop("_audit_status", None) or (
                            "error" if "error" in result else "success"
                        )
                    return result
                except Exception:
                    status = "error"
                    raise
                finally:
                    _log(status, kwargs, start)

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = _time.monotonic()
            status = "success"
            try:
                result = fn(*args, **kwargs)
                if isinstance(result, dict):
                    status = result.pop("_audit_status", None) or (
                        "error" if "error" in result else "success"
                    )
                return result
            except Exception:
                status = "error"
                raise
            finally:
                _log(status, kwargs, start)

        return wrapper

    # --- Input validation helpers ---

    def _validate_path(path: str) -> tuple[Optional[Path], Optional[dict]]:
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

    def _validate_symbol_name(name: str) -> Optional[dict]:
        """Validate a symbol name. Returns error dict or None if valid."""
        if "\x00" in name:
            return {"error": "Symbol name contains null bytes."}
        if len(name) > 500:
            return {"error": "Symbol name too long (max 500 characters)."}
        if not name or not re.search(r"\w", name):
            return {"error": "Symbol name must contain at least one alphanumeric character."}
        return None

    def _validate_query(query: str) -> Optional[dict]:
        """Validate a search query. Returns error dict or None if valid."""
        if "\x00" in query:
            return {"error": "Query contains null bytes."}
        if len(query) > 10000:
            return {"error": "Query too long (max 10,000 characters)."}
        if not query.strip():
            return {"error": "Query must not be empty."}
        return None

    # --- Shared helpers ---

    def _require_indexed():
        """Check that a codebase is indexed and graph engine is available.

        Returns (state, None) on success, or (None, error_dict) on failure.
        """
        from nexus_mcp.state import get_state

        state = get_state()
        if not state.is_indexed or not state.graph_engine:
            return None, {"error": "No codebase indexed. Run 'index' first."}
        return state, None

    def _serialize_node(
        node: UniversalNode, codebase_path: Optional[Path] = None
    ) -> dict[str, Any]:
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
        return [
            item for item in items
            if item.get("location", "").startswith(filter_path)
        ]

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

    # --- MCP Tools ---

    @mcp.tool()
    @_audited
    def status() -> dict[str, Any]:
        """Use at the start of a session, or when unsure if search results might
        be stale. Reports whether a codebase is indexed, index size/engine
        availability, memory usage, and a stale/staleness_warning pair if files
        changed since the last index (a background reindex is auto-triggered)."""
        guard_err = _guard("status")
        if guard_err:
            return guard_err

        from nexus_mcp.state import get_state

        state = get_state()
        # Memory monitoring via peak RSS (ru_maxrss = high-water mark)
        rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS returns bytes, Linux returns KB
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
                "'find_symbol' for definitions, 'find_callers'/'find_callees' for call graph, "
                "'explain' for understanding symbols, 'impact' before refactoring."
            )
        else:
            result["hint"] = (
                "Codebase not indexed. Run 'index' first to enable "
                "semantic search, call graphs, and code analysis."
            )

        return result

    @mcp.tool()
    @_audited
    def health() -> dict[str, Any]:
        """Use for liveness/readiness probes only (uptime, which engines are up)
        — not for checking whether the index is fresh or complete; use `status`
        for that."""
        guard_err = _guard("health")
        if guard_err:
            return guard_err

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

    @mcp.tool()
    @_audited
    async def index(
        path: Annotated[str, "Absolute path to the codebase directory (or comma-separated paths)"],
        paths: Annotated[str, "Additional comma-separated paths to index"] = "",
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Use first on any new or changed codebase, before any other tool —
        everything except `status`/`health` requires an index. Supports
        comma-separated paths for multi-folder/monorepo indexing (processed
        sequentially to keep RAM low). Incremental by default once an index
        exists, and reports live progress instead of blocking silently. After
        this completes, a file watcher keeps the index fresh automatically
        (NEXUS_AUTO_WATCH) — re-running `index` manually is rarely needed."""
        from nexus_mcp.config import get_settings
        from nexus_mcp.indexing.pipeline import IndexingPipeline
        from nexus_mcp.state import get_state

        global _pipeline

        guard_err = _guard("index")
        if guard_err:
            return guard_err

        # Collect all paths from both parameters
        raw_paths = [p.strip() for p in path.split(",") if p.strip()]
        if paths:
            raw_paths.extend(p.strip() for p in paths.split(",") if p.strip())

        # Validate each path
        validated: list[Path] = []
        for raw in raw_paths:
            resolved, err = _validate_path(raw)
            if err:
                return {"error": f"Invalid path '{raw}': {err['error']}"}
            validated.append(resolved)

        if not validated:
            return {"error": "No valid paths provided."}

        settings = get_settings()
        loop = asyncio.get_running_loop()
        progress_throttle = {"last_sent": 0.0}

        def progress_callback(stage: str, info: dict) -> None:
            # parallel_parse_files (indexing/parallel_indexer.py) calls this once
            # per file with event types "file_parsed" (keys: path, symbols, index,
            # total) or "parse_error" (keys: path, error) — from whichever thread
            # is running the blocking pipeline call (see asyncio.to_thread below),
            # never the event loop thread, so bridge via run_coroutine_threadsafe.
            import time as _time

            info = info or {}
            if ctx is None:
                logger.info("[index] %s: %s", stage, info)
                return

            # Throttle: a multi-thousand-file repo would otherwise fire one MCP
            # progress notification per file. Cap to ~2/sec.
            now = _time.monotonic()
            if now - progress_throttle["last_sent"] < 0.5:
                return
            progress_throttle["last_sent"] = now

            try:
                processed = info.get("index", 0)
                total = info.get("total")
                message = f"{stage}: {info.get('path', '')}"
                asyncio.run_coroutine_threadsafe(
                    ctx.report_progress(processed, total, message), loop
                )
            except Exception as e:
                logger.debug("Progress bridge failed for %s: %s", stage, e)

        # Acquire the pipeline lock off the event loop thread — it's normally
        # uncontended, but a background reindex could briefly hold it, and this
        # keeps the wait from blocking other work if that happens.
        await asyncio.to_thread(_pipeline_lock.acquire)
        try:
            if _pipeline is None:
                _pipeline = IndexingPipeline(settings)

            # Multi-path: always use folder-by-folder indexing
            if len(validated) > 1:
                result = await asyncio.to_thread(
                    _pipeline.multi_index, validated, progress_callback
                )
                state = get_state()
                state.codebase_path = validated[0]
                state.codebase_paths = validated
            else:
                # Single path: determine full vs incremental
                codebase_path = validated[0]
                metadata_path = settings.storage_path / "index_metadata.json"
                if metadata_path.exists():
                    result = await asyncio.to_thread(
                        _pipeline.incremental_index, codebase_path, progress_callback
                    )
                else:
                    result = await asyncio.to_thread(
                        _pipeline.index, codebase_path, progress_callback
                    )
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

    @mcp.tool()
    @_audited
    def search(
        query: Annotated[str, "Natural language or code query (e.g. 'retry logic')"],
        limit: Annotated[int, "Max results (default 10, max 100)"] = 10,
        language: Annotated[str, "Filter by language (e.g. 'python')"] = "",
        symbol_type: Annotated[str, "Filter by type (e.g. 'function', 'class')"] = "",
        mode: Annotated[str, "Search mode: 'hybrid', 'vector', or 'bm25'"] = "hybrid",
        rerank: Annotated[bool, "FlashRank reranking (default True)"] = True,
        live_grep: Annotated[bool, "Force live-grep fallback (rg/grep)"] = False,
    ) -> dict[str, Any]:
        """Use for any "where is/how does/find" code question — preferred over
        Grep/Glob, and usually answerable from the returned code_snippet without
        a follow-up Read. Falls back to live grep automatically when hybrid
        results are sparse. Returns a non-null `warning` if the index looked
        stale (a background reindex is triggered; results still return now)."""
        guard_err = _guard("search")
        if guard_err:
            return guard_err

        from nexus_mcp.config import get_settings
        from nexus_mcp.engines.fusion import ReciprocalRankFusion, graph_relevance_search
        from nexus_mcp.engines.reranker import FlashReranker
        from nexus_mcp.state import get_state

        err = _validate_query(query)
        if err:
            return err

        state = get_state()
        if not state.is_indexed or not state.vector_engine:
            return {"error": "No codebase indexed. Run 'index' first."}

        staleness = _get_staleness(state)
        search_warning = None
        if staleness["stale"]:
            search_warning = (
                f"Index may be out of date ({staleness['reason']}); "
                "refreshing in background."
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

        # Vector search
        if mode in ("hybrid", "vector"):
            try:
                vector_results = state.vector_engine.search(
                    query, limit=overfetch, **kwargs
                )
                ranked_lists["vector"] = vector_results
                engines_used.append("vector")
            except Exception as e:
                logger.warning("Vector search failed: %s", e)

        # BM25 search
        if mode in ("hybrid", "bm25") and state.bm25_engine:
            try:
                bm25_results = state.bm25_engine.search(
                    query, limit=overfetch, **kwargs
                )
                if bm25_results:
                    ranked_lists["bm25"] = bm25_results
                    engines_used.append("bm25")
            except Exception as e:
                logger.warning("BM25 search failed: %s", e)

        # Graph relevance
        if mode == "hybrid" and state.graph_engine:
            try:
                graph_results = graph_relevance_search(
                    state.graph_engine, query, limit=limit * 2
                )
                if graph_results:
                    # Apply filters to graph results if needed
                    if language or symbol_type:
                        graph_results = [
                            r for r in graph_results
                            if (not language or r.get("language") == language) and
                               (not symbol_type or r.get("symbol_type") == symbol_type)
                        ]
                    if graph_results:
                        ranked_lists["graph"] = graph_results
                        engines_used.append("graph")
            except Exception as e:
                logger.warning("Graph relevance search failed: %s", e)

        # Fuse results or use single engine
        if len(ranked_lists) > 1:
            fusion = ReciprocalRankFusion(weights={
                "vector": settings.fusion_weight_vector,
                "bm25": settings.fusion_weight_bm25,
                "graph": settings.fusion_weight_graph,
            })
            results = fusion.fuse(ranked_lists)
        elif ranked_lists:
            results = list(ranked_lists.values())[0]
        else:
            results = []

        # Rerank if enabled (reranker cached on state for reuse)
        if rerank and results:
            if not hasattr(state, '_reranker') or state._reranker is None:
                state._reranker = FlashReranker(model_name=settings.reranker_model)
            results = state._reranker.rerank(query, results, limit=limit)
        else:
            results = results[:limit]

        # Live Grep (Fallback or Explicit). Skipped entirely when a
        # symbol_type filter is set: grep matches carry no symbol_type,
        # so none of them could pass the filter.
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

                        # Detect language for live-grep results to apply filter
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

        # Clean up results: strip vectors, add absolute paths, code_snippet
        roots = getattr(state, "codebase_paths", [])
        if not roots and state.codebase_path:
            roots = [state.codebase_path]
        for r in results:
            # Remove raw embedding vector (wastes tokens, not useful to LLM)
            r.pop("vector", None)

            # Rename 'text' to 'code_snippet' with truncation marker
            if "text" in r:
                code = r.pop("text")
                if len(code) > 2000:
                    r["code_snippet"] = code[:2000] + "\n... (truncated)"
                else:
                    r["code_snippet"] = code

            # Standardize path fields: results from engines have 'filepath',
            # live-grep has 'absolute_path'.
            abs_path = r.get("absolute_path") or r.get("filepath")
            if not abs_path:
                continue

            r["absolute_path"] = str(abs_path)

            # Make filepath relative for the LLM; try each indexed root for multi-folder
            for root in roots:
                try:
                    r["filepath"] = str(Path(abs_path).relative_to(root))
                    break
                except ValueError:
                    # Not under this root, or abs_path already relative
                    r["filepath"] = str(abs_path)

            # Ensure language field is present for filtering tests
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
                "Results include code_snippet — you can often "
                "answer without needing to Read the file."
            ),
        }

    @mcp.tool()
    @_audited
    def find_symbol(
        name: Annotated[str, "Symbol name (e.g. 'create_server', 'TokenBudget')"],
        exact: Annotated[bool, "True for exact match, False for fuzzy substring"] = True,
    ) -> dict[str, Any]:
        """Use to look up a specific function/class/symbol by name — preferred
        over Grep since it returns the definition plus its call-graph
        relationships in one call. Set exact=False for fuzzy substring matching
        when unsure of the exact name."""
        guard_err = _guard("find_symbol")
        if guard_err:
            return guard_err

        err = _validate_symbol_name(name)
        if err:
            return err

        state, err = _require_indexed()
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
        """Immediate callers or callees of a symbol (was find_callers/find_callees)."""
        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
        if not matches:
            return {"error": f"Symbol '{symbol_name}' not found."}

        get_related = (
            state.graph_engine.get_callers
            if direction == "callers"
            else state.graph_engine.get_callees
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
        """Transitive caller closure for change-impact analysis (was impact())."""
        max_depth = max(1, min(max_depth, 50))

        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
        if not matches:
            return {"error": f"Symbol '{symbol_name}' not found."}

        all_impacted = []
        seen: set[str] = set()
        for node in matches:
            for caller in state.graph_engine.get_transitive_callers(
                node.id, max_depth=max_depth
            ):
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

    @mcp.tool()
    @_audited
    def graph(
        symbol_name: Annotated[str, "Name of the function/symbol to trace"],
        direction: Annotated[
            str, "'callers' (who calls this) or 'callees' (what this calls)"
        ] = "callers",
        transitive: Annotated[
            bool,
            "True = full transitive closure for change-impact analysis (MUST use "
            "before refactoring a shared symbol). Only valid with direction='callers'.",
        ] = False,
        max_depth: Annotated[int, "Max traversal depth when transitive=True (default 10)"] = 10,
    ) -> dict[str, Any]:
        """Use to trace who calls a function (direction='callers'), what it calls
        (direction='callees'), or — with transitive=True — the full transitive
        blast radius of changing it. MUST use transitive=True before refactoring
        or editing a widely-shared symbol; grep can't show transitive impact."""
        guard_err = _guard("graph")
        if guard_err:
            return guard_err

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

        err = _validate_symbol_name(symbol_name)
        if err:
            return err

        state, err = _require_indexed()
        if err:
            return err

        if transitive:
            return _graph_transitive_impact(state, symbol_name, max_depth)
        return _graph_immediate(state, symbol_name, direction)

    @mcp.tool()
    @_audited
    def analyze(
        path: Annotated[
            str, "Optional relative path to filter analysis (subdirectory or file)"
        ] = ""
    ) -> dict[str, Any]:
        """Use for code review or quality assessment — cyclomatic/cognitive
        complexity, dependency analysis, code smells (long/complex functions,
        large classes, dead code), and an overall quality score. Optionally
        scope to a subdirectory or file via `path`."""
        guard_err = _guard("analyze")
        if guard_err:
            return guard_err

        state, err = _require_indexed()
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

        # Filter by path if specified
        if path and root:
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                return {"error": f"Path '{path}' is outside codebase root."}
            filter_path = str(candidate)

            # Filter complexity high_complexity_functions
            if "high_complexity_functions" in result["complexity"]:
                result["complexity"]["high_complexity_functions"] = _filter_location_list(
                    result["complexity"]["high_complexity_functions"], filter_path
                )

            # Filter code smells
            for smell_key in ["long_functions", "complex_functions", "large_classes", "dead_code"]:
                if smell_key in result["code_smells"]:
                    result["code_smells"][smell_key] = _filter_location_list(
                        result["code_smells"][smell_key], filter_path
                    )

        # Relativize paths in results
        if root:
            # Complexity
            for item in result["complexity"].get("high_complexity_functions", []):
                if "location" in item:
                    item["location"] = _relativize_location_str(item["location"], root)

            # Code smells
            for smell_key in ["long_functions", "complex_functions", "large_classes", "dead_code"]:
                for item in result["code_smells"].get(smell_key, []):
                    if "location" in item:
                        item["location"] = _relativize_location_str(item["location"], root)

        return result

    @mcp.tool()
    @_audited
    def explain(
        symbol_name: Annotated[str, "Name of the symbol to explain"],
        verbosity: Annotated[
            str, "Output detail level: 'summary', 'detailed', or 'full'"
        ] = "detailed",
    ) -> dict[str, Any]:
        """Use for onboarding to an unfamiliar symbol — combines its call-graph
        relationships, related code found via semantic search, and quality
        metrics in one call, so Read is often unnecessary. Use verbosity='summary'
        for a quick look, 'full' when you need everything."""
        guard_err = _guard("explain")
        if guard_err:
            return guard_err

        from nexus_mcp.formatting.response_builder import ResponseBuilder
        from nexus_mcp.formatting.token_budget import TokenBudget

        err = _validate_symbol_name(symbol_name)
        if err:
            return err

        if verbosity not in TokenBudget.BUDGETS:
            valid = list(TokenBudget.BUDGETS.keys())
            return {"error": f"Invalid verbosity: {verbosity}. Must be one of {valid}"}

        state, err = _require_indexed()
        if err:
            return err

        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
        if not matches:
            matches = _resolve_symbol(state.graph_engine, symbol_name, exact=False)
        if not matches:
            return {"error": f"Symbol '{symbol_name}' not found."}

        node = matches[0]
        symbol_data = _serialize_node(node, state.codebase_path)

        # Relationships
        rels_from = state.graph_engine.get_relationships_from(node.id)
        rels_to = state.graph_engine.get_relationships_to(node.id)
        symbol_data["callers"] = [
            _serialize_node(c, state.codebase_path)
            for c in state.graph_engine.get_callers(node.id)
        ]
        symbol_data["callees"] = [
            _serialize_node(c, state.codebase_path)
            for c in state.graph_engine.get_callees(node.id)
        ]
        symbol_data["relationships_out"] = [_serialize_relationship(r) for r in rels_from]
        symbol_data["relationships_in"] = [_serialize_relationship(r) for r in rels_to]

        # Vector search for related code
        search_results = []
        if state.vector_engine:
            search_text = node.name
            if node.docstring:
                search_text += " " + node.docstring
            try:
                search_results = state.vector_engine.search(search_text, limit=10)
            except Exception as e:
                logger.debug("Vector search failed in explain: %s", e)
                pass

        # Code analysis
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
            pass

        builder = ResponseBuilder(verbosity)
        return builder.build_explain_response(symbol_data, search_results, analysis)

    def _build_overview(state) -> dict[str, Any]:
        """Project-level summary (was overview())."""
        from nexus_mcp.analysis.code_analyzer import CodeAnalyzer
        from nexus_mcp.core.graph_models import NodeType

        graph = state.graph_engine
        stats = graph.get_statistics()

        # Language breakdown with file counts
        languages = {}
        for lang, count in stats.get("nodes_by_language", {}).items():
            languages[lang] = count

        # File listing grouped by directory
        directories: dict[str, int] = {}
        root = state.codebase_path
        for fp in graph._file_nodes:
            try:
                rel = str(Path(fp).relative_to(root).parent)
            except ValueError:
                rel = "."
            directories[rel] = directories.get(rel, 0) + 1

        # Top-level modules (sorted by symbol count descending)
        modules = graph.get_nodes_by_type(NodeType.MODULE)
        module_summaries = []
        for mod in modules:
            rels = graph.get_relationships_from(mod.id)
            child_count = len(rels)
            mod_path = mod.location.file_path
            if root:
                try:
                    mod_path = str(Path(mod_path).relative_to(root))
                except ValueError:
                    pass
            module_summaries.append({
                "name": mod.name,
                "file": mod_path,
                "symbols": child_count,
                "lines": mod.line_count,
            })
        module_summaries.sort(key=lambda m: m["symbols"], reverse=True)

        # Quality summary
        analyzer = CodeAnalyzer(graph)
        quality = analyzer.calculate_quality_metrics()

        # Chunk count
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
        """Architectural analysis: layers, dependencies, classes, hubs (was architecture())."""
        from nexus_mcp.analysis.code_analyzer import CodeAnalyzer
        from nexus_mcp.core.graph_models import NodeType, RelationshipType

        graph = state.graph_engine
        root = state.codebase_path
        analyzer = CodeAnalyzer(graph)

        # Module dependency analysis
        dep_analysis = analyzer.analyze_dependencies()

        # Class hierarchy: find inheritance relationships
        classes = graph.get_nodes_by_type(NodeType.CLASS)
        class_info = []
        for cls in classes:
            rels_out = graph.get_relationships_from(cls.id)
            rels_in = graph.get_relationships_to(cls.id)
            methods = sum(
                1 for r in rels_out
                if r.relationship_type == RelationshipType.CONTAINS
            )
            parents = [
                graph.get_node(r.source_id)
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

            class_info.append({
                "name": cls.name,
                "file": cls_path,
                "methods": methods,
                "lines": cls.line_count,
                "parent_module": parent_name,
                "visibility": cls.visibility,
            })
        class_info.sort(key=lambda c: c["methods"], reverse=True)

        # Identify architectural layers by directory
        layers: dict[str, dict[str, Any]] = {}
        modules = graph.get_nodes_by_type(NodeType.MODULE)
        for mod in modules:
            mod_path = mod.location.file_path
            if root:
                try:
                    mod_path = str(Path(mod_path).relative_to(root))
                except ValueError:
                    pass
            parts = Path(mod_path).parts
            # Use top two directory levels as the layer key
            if len(parts) >= 2:
                layer = str(Path(parts[0]) / parts[1]) if len(parts) > 2 else parts[0]
            else:
                layer = parts[0] if parts else "root"

            if layer not in layers:
                layers[layer] = {"modules": [], "total_symbols": 0}
            rels = graph.get_relationships_from(mod.id)
            symbol_count = len(rels)
            layers[layer]["modules"].append(mod.name)
            layers[layer]["total_symbols"] += symbol_count

        # Entry points: functions named main, run, start, handler, create_server
        entry_patterns = {"main", "run", "start", "handler", "create_server", "app"}
        functions = graph.get_nodes_by_type(NodeType.FUNCTION)
        entry_points = []
        for func in functions:
            if func.name in entry_patterns:
                fp = func.location.file_path
                if root:
                    try:
                        fp = str(Path(fp).relative_to(root))
                    except ValueError:
                        pass
                entry_points.append({
                    "name": func.name,
                    "file": fp,
                    "line": func.location.start_line,
                })

        # Hub symbols: highest degree (most connections)
        hub_symbols = []
        for node in graph.nodes.values():
            in_deg, out_deg = graph.get_node_degree(node.id)
            total_deg = in_deg + out_deg
            if total_deg >= 5:
                fp = node.location.file_path
                if root:
                    try:
                        fp = str(Path(fp).relative_to(root))
                    except ValueError:
                        pass
                hub_symbols.append({
                    "name": node.name,
                    "type": node.node_type.value,
                    "file": fp,
                    "in_degree": in_deg,
                    "out_degree": out_deg,
                    "total_connections": total_deg,
                })
        hub_symbols.sort(key=lambda h: h["total_connections"], reverse=True)

        # Complexity hotspots
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

    def _map_impl(
        detail: Annotated[
            str,
            "'summary' (files/languages/quality/top-modules), 'architecture' "
            "(layers/dependencies/classes/entry points/hub symbols), or 'full' (both)",
        ] = "summary",
    ) -> dict[str, Any]:
        """PREFERRED over Glob/ls/manual browsing for project understanding.
        Use 'summary' for a quick project orientation, 'architecture' for
        design/dependency structure, 'full' for both in one call."""
        guard_err = _guard("map")
        if guard_err:
            return guard_err

        if detail not in ("summary", "architecture", "full"):
            return {"error": "detail must be 'summary', 'architecture', or 'full'."}

        state, err = _require_indexed()
        if err:
            return err

        if detail == "summary":
            return _build_overview(state)
        if detail == "architecture":
            return _build_architecture(state)
        return {**_build_overview(state), **_build_architecture(state)}

    # `map` shadows the Python builtin, and _audited derives its audit-log tool_name
    # from fn.__name__ — rename before wrapping so both FastMCP's registered name and
    # the audit trail say "map", not "map_tool"/"_map_impl". Can't use @mcp.tool()/
    # @_audited decorator syntax here since the rename must happen between the two.
    _map_impl.__name__ = "map"
    mcp.tool(name="map")(_audited(_map_impl))

    # --- Memory helpers ---

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

    def _memory_store_action(
        content: str, memory_type: str, tags: str, ttl: str, project: str
    ) -> dict[str, Any]:
        """Store a semantic memory for later recall (was remember())."""
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

    def _memory_search_action(
        query: str, limit: int, memory_type: str, tags: str
    ) -> dict[str, Any]:
        """Search memories by semantic similarity (was recall())."""
        err = _validate_query(query)
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

        return {
            "query": query,
            "total": len(memories),
            "memories": [m.to_dict() for m in memories],
        }

    def _memory_delete_action(memory_id: str, tags: str, memory_type: str) -> dict[str, Any]:
        """Delete memories by ID, tags, or type (was forget())."""
        store = _get_memory_store()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

        deleted = store.forget(
            memory_id=memory_id,
            tags=tag_list,
            memory_type=memory_type,
        )

        return {"deleted_count": deleted}

    @mcp.tool()
    @_audited
    def memory(
        action: Annotated[
            str, "'store' (was remember), 'search' (was recall), or 'delete' (was forget)"
        ],
        content: Annotated[str, "Memory content to store (action='store')"] = "",
        query: Annotated[str, "Natural language search query (action='search')"] = "",
        memory_id: Annotated[str, "Specific memory ID to delete (action='delete')"] = "",
        memory_type: Annotated[
            str, "Type/filter, e.g. 'note', 'decision' (store: type; search/delete: filter)"
        ] = "",
        tags: Annotated[str, "Comma-separated tags (all actions)"] = "",
        ttl: Annotated[
            str, "Time-to-live for action='store': 'permanent', 'month', 'week', 'day', 'session'"
        ] = "permanent",
        project: Annotated[str, "Project name for scoping (action='store')"] = "default",
        limit: Annotated[int, "Max results (action='search', default 5)"] = 5,
    ) -> dict[str, Any]:
        """Persist and retrieve project context across sessions. Use action='store'
        to save a decision/note, action='search' to find memories by semantic
        similarity, action='delete' to clean up by ID, tags, or type."""
        from nexus_mcp.security.permissions import ToolCategory

        category_override = {
            "store": ToolCategory.WRITE,
            "search": ToolCategory.READ,
            "delete": ToolCategory.WRITE,
        }.get(action)

        guard_err = _guard("memory", category_override=category_override)
        if guard_err:
            return guard_err

        if action == "store":
            memory_type = memory_type or "note"
            return _memory_store_action(content, memory_type, tags, ttl, project)
        if action == "search":
            return _memory_search_action(query, limit, memory_type, tags)
        if action == "delete":
            return _memory_delete_action(memory_id, tags, memory_type)
        return {"error": "action must be 'store', 'search', or 'delete'."}

    return mcp


def main():
    """Entry point for nexus-mcp CLI."""
    from nexus_mcp.config import get_settings
    from nexus_mcp.state import get_state

    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Configure logging (JSON or text)
    if settings.log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.root.addHandler(handler)
        logging.root.setLevel(log_level)
    else:
        logging.basicConfig(level=log_level)

    # Graceful shutdown handler — set flag only, let finally block do cleanup
    def _shutdown_handler(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    server = create_server()
    try:
        server.run()
    finally:
        get_state().shutdown()


if __name__ == "__main__":
    main()
