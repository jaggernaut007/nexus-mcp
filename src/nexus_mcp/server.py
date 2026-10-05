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
from typing import TYPE_CHECKING, Annotated, Any, Literal, Optional

from nexus_mcp import core_api

if TYPE_CHECKING:
    from nexus_mcp.security.permissions import ToolCategory

logger = logging.getLogger(__name__)

# Sent to the client at connect time. Claude Code puts it in the model's context and,
# with Tool Search on, shows it before any tool description. It is cut at 2,048
# characters (Codex reads the first 512 first), so the opening lines stand alone.
# Do not repeat the tool descriptions here; say which tool answers which question.
SERVER_INSTRUCTIONS = """\
Nexus-MCP is a local code-intelligence index of the project you are working in. Use it \
for any question about how this codebase works: where something is, what calls what, \
what breaks if a symbol changes, how the project is laid out, and what was decided \
earlier. One call here replaces several greps and file reads.

Start of a session: call `status`. An index from an earlier session is reattached \
automatically. If `indexed` is false, call `index` once with the absolute project path. \
After that the index keeps itself fresh.

Which tool answers which question:
- "where is...", "how does...", "find the code that..." -> `search`
- a function or class you can name -> `find_symbol`; for a full briefing -> `explain`
- "who calls X", "what does X call", "what breaks if I change X" -> `graph` \
(use transitive=true before a rename, signature change or delete)
- "give me an overview", "where do I start", "how is this structured" -> `map`
- "most complex code", "dead code", "review quality" -> `analyze`
- a decision, preference or note to keep across sessions -> `memory`

Use your built-in grep or file read when you already know the exact file or string. \
Call edges are static: calls through callbacks, reflection or dynamic dispatch are not \
visible, so confirm with `search` before you delete code.
"""


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

    mcp = FastMCP("Nexus-MCP", instructions=SERVER_INSTRUCTIONS, version=_nexus_version)

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

    from mcp.types import ToolAnnotations

    # Hints for the client (for example Codex asks for approval only for tools that
    # are not marked read-only). They are hints, not a security boundary: the real
    # check is the permission policy in `_guard`.
    READ_ONLY = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    INDEXING = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    MEMORY_WRITE = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
    )

    @mcp.tool(annotations=READ_ONLY, title="Index status")
    @_audited
    async def status() -> dict[str, Any]:
        """Check whether this project is indexed and whether the index is fresh. Call
        it first in a session, before any other nexus tool. An index saved in an earlier
        session is reattached here, so `indexed` is usually already true. Returns
        `indexed` (true/false), file and symbol counts, which engines are ready, memory
        use, and a stale warning if files changed since the last index (a background
        reindex starts by itself). If `indexed` is false, call `index`."""
        guard_err = _guard("status")
        if guard_err:
            return guard_err
        # status() may reattach a stored index, which reads from disk: keep it off the
        # event loop. The file watcher needs the running loop, so start it here. A session
        # that called `index` already has one, and this does nothing then.
        result = await asyncio.to_thread(core_api.status)
        if result.get("indexed"):
            from nexus_mcp.state import get_state

            await core_api._ensure_file_watcher(get_state(), _settings.auto_watch_enabled)
        return result

    @mcp.tool(annotations=READ_ONLY, title="Server health")
    @_audited
    def health() -> dict[str, Any]:
        """Liveness check for the server process: uptime and which engines are up.
        It does not say whether the index is complete or fresh; use `status` for that.
        Needed only for monitoring, not for normal code work."""
        guard_err = _guard("health")
        if guard_err:
            return guard_err
        return core_api.health()

    @mcp.tool(annotations=INDEXING, title="Index a project")
    @_audited
    async def index(
        path: Annotated[str, "Absolute path to the codebase directory (or comma-separated paths)"],
        paths: Annotated[str, "Additional comma-separated paths to index"] = "",
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Build or refresh the code index for a project. Call it once per project
        when `status` says `indexed` is false; search, find_symbol, graph, map, analyze
        and explain all need it. Pass an absolute path, or several comma-separated
        paths for a monorepo. It is incremental when an index exists, and a file
        watcher then keeps the index fresh, so you rarely need to call it again.
        Large repositories take a while; progress is reported."""
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

    @mcp.tool(annotations=READ_ONLY, title="Search code")
    @_audited
    def search(
        query: Annotated[str, "What to find, in plain words or code terms (e.g. 'retry logic')"],
        limit: Annotated[int, "Max results (default 10, max 100)"] = 10,
        language: Annotated[str, "Filter by language (e.g. 'python')"] = "",
        symbol_type: Annotated[str, "Filter by type (e.g. 'function', 'class')"] = "",
        mode: Annotated[
            Literal["hybrid", "vector", "bm25"],
            "'hybrid' (default), 'vector' (meaning only) or 'bm25' (keywords only)",
        ] = "hybrid",
        rerank: Annotated[bool, "FlashRank reranking (default True)"] = True,
        live_grep: Annotated[bool, "Force live-grep fallback (rg/grep)"] = False,
    ) -> dict[str, Any]:
        """Find code by meaning or by keyword: "where is...", "how does... work",
        "find the code that...", "what handles...". Returns ranked snippets with file
        path and line range, usually enough to answer without opening the file.
        It combines semantic, keyword and graph search, and falls back to live text
        search when results are few. Use it before reading files to explore. For an
        exact string or a file you already know, your built-in grep or read is just as
        good. A non-null `warning` means the index was slightly stale; results still
        return."""
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

    @mcp.tool(annotations=READ_ONLY, title="Find a symbol")
    @_audited
    def find_symbol(
        symbol_name: Annotated[str, "Symbol name (e.g. 'create_server', 'TokenBudget')"],
        exact: Annotated[bool, "True for exact match, False for fuzzy substring"] = True,
    ) -> dict[str, Any]:
        """Look up a function, class or method by name and get where it is defined: file,
        line range, language, complexity, docstring and its direct relationships. Use it
        when you know the name ("show me create_order", "where is TokenBudget defined").
        Set exact=false to match part of a name when you are unsure of the spelling.
        For who calls it, use `graph`; for a full briefing, use `explain`."""
        guard_err = _guard("find_symbol")
        if guard_err:
            return guard_err
        return core_api.find_symbol(symbol_name, exact=exact)

    @mcp.tool(annotations=READ_ONLY, title="Call graph")
    @_audited
    def graph(
        symbol_name: Annotated[str, "Name of the function/symbol to trace"],
        direction: Annotated[
            Literal["callers", "callees"],
            "'callers' (who calls this) or 'callees' (what this calls)",
        ] = "callers",
        transitive: Annotated[
            bool,
            "True = everything that depends on the symbol, directly or indirectly. Use "
            "before changing a shared symbol. Only valid with direction='callers'.",
        ] = False,
        max_depth: Annotated[int, "Max traversal depth when transitive=True (default 10)"] = 10,
    ) -> dict[str, Any]:
        """Trace the call graph: who calls a function (direction='callers') or what
        it calls (direction='callees'). With transitive=true it lists everything that
        depends on the symbol, directly or indirectly; use that before you change a
        signature or rename, move or delete a shared function ("what breaks if I
        change X"). Edges are static, so calls through callbacks, reflection or
        dynamic dispatch are missing. Treat the result as a lower bound and confirm
        with `search`."""
        guard_err = _guard("graph")
        if guard_err:
            return guard_err
        return core_api.graph(
            symbol_name, direction=direction, transitive=transitive, max_depth=max_depth
        )

    @mcp.tool(annotations=READ_ONLY, title="Analyze code quality")
    @_audited
    def analyze(
        path: Annotated[
            str, "Optional relative path to filter analysis (subdirectory or file)"
        ] = ""
    ) -> dict[str, Any]:
        """Review the quality of the indexed project, or of one path: the most
        complex functions, long functions, large classes, dead code (functions with
        no static caller), module dependencies and an overall quality score. Use it
        for "what is the most complex code", "review this directory", "is there dead
        code". Pass `path` (relative) to limit it to a directory or file. Read-only.
        Complexity is cyclomatic and approximate, and dead-code entries have no
        static caller, so check them before you delete anything."""
        guard_err = _guard("analyze")
        if guard_err:
            return guard_err
        return core_api.analyze(path)

    @mcp.tool(annotations=READ_ONLY, title="Explain a symbol")
    @_audited
    def explain(
        symbol_name: Annotated[str, "Name of the symbol to explain"],
        verbosity: Annotated[
            Literal["summary", "detailed", "full"],
            "Output detail level: 'summary', 'detailed' (default) or 'full'",
        ] = "detailed",
    ) -> dict[str, Any]:
        """Get a full briefing on one function or class in a single call: its
        definition, who calls it, what it calls, related code found by meaning, and
        quality metrics. Use it to understand an unfamiliar symbol ("explain
        cancel_order", "what does this class do and where is it used") instead of
        reading several files. Use verbosity='summary' for a quick look and 'full'
        for everything."""
        guard_err = _guard("explain")
        if guard_err:
            return guard_err
        return core_api.explain(symbol_name, verbosity=verbosity)

    def _map_impl(
        detail: Annotated[
            Literal["summary", "architecture", "full"],
            "'summary' (files/languages/quality/top-modules), 'architecture' "
            "(layers/dependencies/classes/entry points/hub symbols), or 'full' (both)",
        ] = "summary",
    ) -> dict[str, Any]:
        """Get an overview of the project: "give me an overview", "how is this
        structured", "where do I start", "what are the main modules". 'summary' lists
        files, languages, top modules and quality. 'architecture' gives layers, module
        dependencies, entry points and the most connected symbols. 'full' gives both.
        Use it at the start
        of work in an unfamiliar repository, before you list directories or open
        files one by one."""
        guard_err = _guard("map")
        if guard_err:
            return guard_err
        return core_api.map_(detail)

    # `map` shadows the Python builtin, and _audited derives its audit-log tool_name
    # from fn.__name__ — rename before wrapping so both FastMCP's registered name and
    # the audit trail say "map", not "map_tool"/"_map_impl". Can't use @mcp.tool()/
    # @_audited decorator syntax here since the rename must happen between the two.
    _map_impl.__name__ = "map"
    mcp.tool(name="map", annotations=READ_ONLY, title="Project map")(_audited(_map_impl))

    @mcp.tool(annotations=MEMORY_WRITE, title="Project memory")
    @_audited
    def memory(
        action: Annotated[
            Literal["store", "search", "delete"],
            "'store' saves a note, 'search' finds notes by meaning, 'delete' removes notes",
        ],
        content: Annotated[str, "Memory content to store (action='store')"] = "",
        query: Annotated[str, "Natural language search query (action='search')"] = "",
        memory_id: Annotated[str, "Specific memory ID to delete (action='delete')"] = "",
        memory_type: Annotated[
            str, "Type/filter, e.g. 'note', 'decision' (store: type; search/delete: filter)"
        ] = "",
        tags: Annotated[str, "Comma-separated tags (all actions)"] = "",
        ttl: Annotated[
            Literal["permanent", "month", "week", "day", "session"],
            "How long a stored note lives (action='store'); default 'permanent'",
        ] = "permanent",
        project: Annotated[str, "Project name for scoping (action='store')"] = "default",
        limit: Annotated[int, "Max results (action='search', default 5)"] = 5,
    ) -> dict[str, Any]:
        """Keep and recall project knowledge across sessions: decisions, preferences,
        conventions, status. Use action='store' when the user says "remember that..."
        or a decision is made, action='search' for "what did we decide about...", and
        action='delete' to remove outdated notes by ID, tags or type. Notes are stored
        with the index of this project (the .nexus folder) and found by meaning, not
        by exact words."""
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
