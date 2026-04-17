"""Nexus-MCP FastMCP server with index, search, status, and graph/analysis tools."""

import json as _json
import logging
import re
import resource
import signal
import sys
import threading
from pathlib import Path
from typing import Annotated, Any, List, Optional

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


def create_server():
    """Create and configure the FastMCP server."""
    from fastmcp import FastMCP

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

    def _check_tool_permission(tool_name: str) -> Optional[dict]:
        """Check if tool is allowed under current permission policy.

        Returns None if allowed, or error dict if denied.
        """
        from nexus_mcp.security.permissions import (
            check_permission,
            get_tool_category,
            policy_from_level,
        )

        policy = policy_from_level(_settings.default_permission_level)
        if not check_permission(tool_name, policy):
            category = get_tool_category(tool_name)
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

    def _guard(tool_name: str) -> Optional[dict]:
        """Run all pre-execution checks: permissions, rate limiting.

        Returns None if all checks pass, or error dict on first failure.
        """
        err = _check_tool_permission(tool_name)
        if err:
            return err
        err = _check_rate_limit(tool_name)
        if err:
            return err
        return None

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
    def status() -> dict[str, Any]:
        """Get Nexus-MCP server status including indexing stats."""
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
            "version": "0.1.1",
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
    def health() -> dict[str, Any]:
        """Health check for readiness/liveness probes.

        Returns:
            Server health status including uptime and engine availability.
        """
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
    def index(
        path: Annotated[str, "Absolute path to the codebase directory (or comma-separated paths)"],
        paths: Annotated[str, "Additional comma-separated paths to index"] = "",
    ) -> dict[str, Any]:
        """Index a codebase into vector + graph engines.

        Supports indexing multiple directories folder-by-folder. Each folder
        is processed sequentially (discover → parse → embed → store) and
        results are merged into shared engines.
        """
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

        with _pipeline_lock:
            if _pipeline is None:
                _pipeline = IndexingPipeline(settings)

        # Multi-path: always use folder-by-folder indexing
        if len(validated) > 1:
            result = _pipeline.multi_index(validated)
            state = get_state()
            state.codebase_path = validated[0]
            state.codebase_paths = validated
            state.vector_engine = _pipeline.vector_engine
            state.bm25_engine = _pipeline.bm25_engine
            state.graph_engine = _pipeline.graph_engine
            return result.to_dict()

        # Single path: determine full vs incremental
        codebase_path = validated[0]
        metadata_path = settings.storage_path / "index_metadata.json"
        if metadata_path.exists():
            result = _pipeline.incremental_index(codebase_path)
        else:
            result = _pipeline.index(codebase_path)

        # Wire engines into session state
        state = get_state()
        state.codebase_path = codebase_path
        state.codebase_paths = [codebase_path]
        state.vector_engine = _pipeline.vector_engine
        state.bm25_engine = _pipeline.bm25_engine
        state.graph_engine = _pipeline.graph_engine

        return result.to_dict()

    @mcp.tool()
    def search(
        query: Annotated[str, "Natural language or code query (e.g. 'retry logic')"],
        limit: Annotated[int, "Max results (default 10, max 100)"] = 10,
        language: Annotated[str, "Filter by language (e.g. 'python')"] = "",
        symbol_type: Annotated[str, "Filter by type (e.g. 'function', 'class')"] = "",
        mode: Annotated[str, "Search mode: 'hybrid', 'vector', or 'bm25'"] = "hybrid",
        rerank: Annotated[bool, "FlashRank reranking (default True)"] = True,
        live_grep: Annotated[bool, "Force live-grep fallback (rg/grep)"] = False,
    ) -> dict[str, Any]:
        """PREFERRED over Grep/Glob. Semantic search with code snippets."""
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

        # Live Grep (Fallback or Explicit)
        if live_grep or (mode == "hybrid" and len(results) < limit):
            try:
                from nexus_mcp.engines.live_grep import LiveGrepEngine
                live_engine = LiveGrepEngine(str(state.codebase_path))
                live_results = live_engine.search(query, limit=limit)

                if live_results:
                    # Deduplicate: use (path, line) as key
                    seen = {
                        (r.get("absolute_path"), r.get("line_start")) for r in results
                    }
                    for lr in live_results:
                        key = (lr["absolute_path"], lr["line_start"])
                        if key not in seen:
                            results.append(lr)
                            seen.add(key)
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
            # Make path relative; try each indexed root for multi-folder
            fp = r.get("filepath", "")
            r["absolute_path"] = fp
            for root in roots:
                try:
                    r["filepath"] = str(Path(fp).relative_to(root))
                    break
                except ValueError:
                    continue
            # Rename 'text' to 'code_snippet' with truncation marker
            if "text" in r:
                code = r.pop("text")
                if len(code) > 2000:
                    r["code_snippet"] = code[:2000] + "\n... (truncated)"
                else:
                    r["code_snippet"] = code

        return {
            "query": query,
            "total": len(results),
            "search_mode": mode,
            "engines_used": engines_used,
            "results": results,
            "hint": (
                "Results include code_snippet — you can often "
                "answer without needing to Read the file."
            ),
        }

    @mcp.tool()
    def find_symbol(
        name: Annotated[str, "Symbol name (e.g. 'create_server', 'TokenBudget')"],
        exact: Annotated[bool, "True for exact match, False for fuzzy substring"] = True,
    ) -> dict[str, Any]:
        """PREFERRED over Grep for finding symbol definitions."""
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

    @mcp.tool()
    def find_callers(
        symbol_name: Annotated[str, "Name of the function to find callers for"]
    ) -> dict[str, Any]:
        """Find all functions that call a given symbol. More accurate than Grep."""
        guard_err = _guard("find_callers")
        if guard_err:
            return guard_err

        err = _validate_symbol_name(symbol_name)
        if err:
            return err

        state, err = _require_indexed()
        if err:
            return err

        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
        if not matches:
            return {"error": f"Symbol '{symbol_name}' not found."}

        all_callers = []
        seen: set[str] = set()
        for node in matches:
            for caller in state.graph_engine.get_callers(node.id):
                if caller.id not in seen:
                    seen.add(caller.id)
                    all_callers.append(_serialize_node(caller, state.codebase_path))

        return {
            "symbol": symbol_name,
            "total": len(all_callers),
            "callers": all_callers,
        }

    @mcp.tool()
    def find_callees(
        symbol_name: Annotated[str, "Name of the function to find callees for"]
    ) -> dict[str, Any]:
        """Find all functions called by a given function. Traces execution flow."""
        guard_err = _guard("find_callees")
        if guard_err:
            return guard_err

        err = _validate_symbol_name(symbol_name)
        if err:
            return err

        state, err = _require_indexed()
        if err:
            return err

        matches = _resolve_symbol(state.graph_engine, symbol_name, exact=True)
        if not matches:
            return {"error": f"Symbol '{symbol_name}' not found."}

        all_callees = []
        seen: set[str] = set()
        for node in matches:
            for callee in state.graph_engine.get_callees(node.id):
                if callee.id not in seen:
                    seen.add(callee.id)
                    all_callees.append(_serialize_node(callee, state.codebase_path))

        return {
            "symbol": symbol_name,
            "total": len(all_callees),
            "callees": all_callees,
        }

    @mcp.tool()
    def analyze(
        path: Annotated[str, "Optional relative path to filter analysis (subdirectory or file)"] = ""
    ) -> dict[str, Any]:
        """Run code analysis: complexity, dependencies, smells, quality score."""
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
            filter_path = str(root / path)

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
    def impact(
        symbol_name: Annotated[str, "Name of the function to analyze impact for"],
        max_depth: Annotated[int, "Max depth of transitive caller traversal (default 10)"] = 10,
    ) -> dict[str, Any]:
        """MUST use before refactoring. Change impact analysis."""
        guard_err = _guard("impact")
        if guard_err:
            return guard_err

        err = _validate_symbol_name(symbol_name)
        if err:
            return err

        state, err = _require_indexed()
        if err:
            return err

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

        # Group by file for readability
        by_file: dict[str, list[str]] = {}
        for item in all_impacted:
            fp = item["location"]["file"]
            if fp not in by_file:
                by_file[fp] = []
            by_file[fp].append(item["name"])

        return {
            "symbol": symbol_name,
            "max_depth": max_depth,
            "total_impacted": len(all_impacted),
            "impacted_symbols": all_impacted,
            "impacted_files": by_file,
        }

    @mcp.tool()
    def explain(
        symbol_name: Annotated[str, "Name of the symbol to explain"],
        verbosity: Annotated[str, "Output detail level: 'summary', 'detailed', or 'full'"] = "detailed",
    ) -> dict[str, Any]:
        """PREFERRED over Read for understanding code symbols."""
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

    @mcp.tool()
    def overview() -> dict[str, Any]:
        """PREFERRED over Glob/ls for project exploration.

        Returns language breakdown, file count, symbol counts by type,
        code quality summary, top-level modules, and key statistics.

        Returns:
            Project overview with structure, languages, quality, and key symbols.
        """
        guard_err = _guard("overview")
        if guard_err:
            return guard_err

        state, err = _require_indexed()
        if err:
            return err

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

    @mcp.tool()
    def architecture() -> dict[str, Any]:
        """PREFERRED over manual browsing for project design.

        Analyzes module dependencies, class hierarchies, key abstractions,
        coupling metrics, circular dependencies, and entry points to
        produce an architectural summary.

        Returns:
            Architectural documentation with layers, dependencies,
            key abstractions, and structural insights.
        """
        guard_err = _guard("architecture")
        if guard_err:
            return guard_err

        state, err = _require_indexed()
        if err:
            return err

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

    @mcp.tool()
    def remember(
        content: Annotated[str, "Memory content to store"],
        memory_type: Annotated[str, "Type of memory (e.g. 'note', 'decision')"] = "note",
        tags: Annotated[str, "Comma-separated tags for organization"] = "",
        ttl: Annotated[str, "Time-to-live: 'permanent', 'month', 'week', 'day', 'session'"] = "permanent",
        project: Annotated[str, "Project name for scoping memories"] = "default",
    ) -> dict[str, Any]:
        """Store a semantic memory for later recall."""
        import uuid

        from nexus_mcp.core.models import Memory, MemoryType

        guard_err = _guard("remember")
        if guard_err:
            return guard_err

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

    @mcp.tool()
    def recall(
        query: Annotated[str, "Natural language search query"],
        limit: Annotated[int, "Maximum number of results (default 5)"] = 5,
        memory_type: Annotated[str, "Filter by memory type (e.g. 'note', 'decision')"] = "",
        tags: Annotated[str, "Comma-separated tags to filter by"] = "",
    ) -> dict[str, Any]:
        """Search memories by semantic similarity."""
        guard_err = _guard("recall")
        if guard_err:
            return guard_err

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

    @mcp.tool()
    def forget(
        memory_id: Annotated[str, "Specific memory ID to delete"] = "",
        tags: Annotated[str, "Delete memories matching any of these comma-separated tags"] = "",
        memory_type: Annotated[str, "Delete all memories of this type"] = "",
    ) -> dict[str, Any]:
        """Delete memories by ID, tags, or type."""
        guard_err = _guard("forget")
        if guard_err:
            return guard_err

        store = _get_memory_store()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

        deleted = store.forget(
            memory_id=memory_id,
            tags=tag_list,
            memory_type=memory_type,
        )

        return {"deleted_count": deleted}

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
