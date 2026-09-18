"""Nexus-MCP FastMCP server with index, search, status, and graph/analysis tools.

All tool bodies here are thin wrappers: MCP-specific concerns only
(permission/rate-limit guards, audit logging, `Context` progress bridging).
The actual logic lives in `nexus_mcp.core_api`, which has no dependency on
FastMCP or the MCP wire protocol and can be imported directly by any other
Python process (see core_api's module docstring).
"""

import asyncio
import json as _json
import logging
import signal
from typing import TYPE_CHECKING, Annotated, Any, Optional

from nexus_mcp import core_api

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


def create_server():
    """Create and configure the FastMCP server."""
    from importlib.metadata import version as _pkg_version

    from fastmcp import Context, FastMCP

    try:
        _nexus_version = _pkg_version("nexus-mcp-ci")
    except Exception:
        _nexus_version = "unknown"

    mcp = FastMCP("Nexus-MCP", version=_nexus_version)

    # --- Middleware: permissions, rate limiting, audit ---

    from nexus_mcp.config import get_settings as _get_settings

    _settings = _get_settings()

    from nexus_mcp.middleware.audit import AuditLogger

    _audit = AuditLogger(enabled=_settings.audit_enabled)

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
            return {"error": "Rate limit exceeded.", "retry_after": round(retry, 2)}
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
        """Wrap a tool to emit one audit record per invocation."""
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

    # --- MCP Tools (thin wrappers over core_api) ---

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
        return core_api.status()

    @mcp.tool()
    @_audited
    def health() -> dict[str, Any]:
        """Use for liveness/readiness probes only (uptime, which engines are up)
        — not for checking whether the index is fresh or complete; use `status`
        for that."""
        guard_err = _guard("health")
        if guard_err:
            return guard_err
        return core_api.health()

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
        guard_err = _guard("index")
        if guard_err:
            return guard_err

        loop = asyncio.get_running_loop()
        progress_throttle = {"last_sent": 0.0}

        def progress_callback(stage: str, info: dict) -> None:
            # core_api.index calls this from whichever thread runs the blocking
            # pipeline work (see asyncio.to_thread there), never the event loop
            # thread, so bridge via run_coroutine_threadsafe.
            import time as _time

            if ctx is None:
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

        return await core_api.index(path, paths, progress_callback=progress_callback)

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
        return core_api.search(
            query,
            limit=limit,
            language=language,
            symbol_type=symbol_type,
            mode=mode,
            rerank=rerank,
            live_grep=live_grep,
        )

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
        return core_api.find_symbol(name, exact=exact)

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
        return core_api.graph(
            symbol_name, direction=direction, transitive=transitive, max_depth=max_depth
        )

    @mcp.tool()
    @_audited
    def analyze(
        path: Annotated[
            str, "Optional relative path to filter analysis (subdirectory or file)"
        ] = ""
    ) -> dict[str, Any]:
        """Use for code review or quality assessment — preferred over manually
        reading files to eyeball complexity, since it computes cyclomatic/
        cognitive complexity, dependency analysis, code smells (long/complex
        functions, large classes, dead code), and an overall quality score in
        one call. Read-only; requires an index (see `index`). Optionally scope
        to a subdirectory or file via `path` to keep results focused and fast
        on large codebases — omit it to analyze the whole indexed codebase."""
        guard_err = _guard("analyze")
        if guard_err:
            return guard_err
        return core_api.analyze(path)

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
        return core_api.explain(symbol_name, verbosity=verbosity)

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
        return core_api.map_(detail)

    # `map` shadows the Python builtin, and _audited derives its audit-log tool_name
    # from fn.__name__ — rename before wrapping so both FastMCP's registered name and
    # the audit trail say "map", not "map_tool"/"_map_impl". Can't use @mcp.tool()/
    # @_audited decorator syntax here since the rename must happen between the two.
    _map_impl.__name__ = "map"
    mcp.tool(name="map")(_audited(_map_impl))

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

        return core_api.memory(
            action,
            content=content,
            query=query,
            memory_id=memory_id,
            memory_type=memory_type,
            tags=tags,
            ttl=ttl,
            project=project,
            limit=limit,
        )

    return mcp


def main():
    """Entry point for nexus-mcp CLI."""
    from nexus_mcp.config import get_settings
    from nexus_mcp.state import get_state

    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.root.addHandler(handler)
        logging.root.setLevel(log_level)
    else:
        logging.basicConfig(level=log_level)

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
