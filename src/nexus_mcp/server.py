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
import threading
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

No setup call is needed: the index of an earlier session attaches at start and keeps \
itself fresh. Only if a tool answers "No codebase indexed", call `index` once with the \
absolute project path.

Which tool answers which question:
- "where is...", "how does...", "find the code that..." -> `search`
- functions, classes or constants you can name -> `find_symbol` (all names in one call)
- "who calls X", "what breaks if I change X" -> `graph` (transitive=true before a change)
- "give me an overview", "where do I start" -> `map`
- "most complex code", "dead code" -> `analyze`
- a decision or note to keep across sessions -> `memory`

`search` and `find_symbol` return the whole code, so do not follow them with a grep or \
file read for the same name. Each call costs one more read of the whole conversation. \
Use your built-in grep or file read when you know the exact file or string. Results can \
be a little old: read the file before you edit it.
"""

# The instruction line that names each optional tool. It is removed when the tool is off.
_OPTIONAL_TOOL_LINES = {
    "analyze": '- "most complex code", "dead code" -> `analyze`\n',
    "memory": "- a decision or note to keep across sessions -> `memory`\n",
}


def instructions_for(hidden_tools: set[str]) -> str:
    """Return SERVER_INSTRUCTIONS without the lines for tools that the server hides."""
    text = SERVER_INSTRUCTIONS
    for name in hidden_tools:
        text = text.replace(_OPTIONAL_TOOL_LINES.get(name, ""), "")
    if "memory" in hidden_tools:
        text = text.replace(
            ", how the project is laid out, and what was decided earlier",
            ", and how the project is laid out",
        )
    return text


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

    from nexus_mcp.config import OPTIONAL_TOOLS
    from nexus_mcp.config import get_settings as _get_settings

    _settings = _get_settings()
    _hidden_tools = set(OPTIONAL_TOOLS) - _settings.enabled_optional_tools()

    mcp = FastMCP(
        "Nexus-MCP",
        instructions=instructions_for(_hidden_tools),
        version=_nexus_version,
    )

    # --- Middleware: permissions, rate limiting, audit ---

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

    # --- Audit calls that the schema rejects -------------------------------------------
    # Enum and type validation runs before `_audited`, so a call with an invalid argument
    # would leave no audit record. This middleware records it, then re-raises.

    from fastmcp.exceptions import ValidationError as _SchemaError
    from fastmcp.server.middleware import Middleware

    class _AuditRejectedCalls(Middleware):
        async def on_call_tool(self, context, call_next):
            import time as _time

            from nexus_mcp.middleware.audit import generate_correlation_id

            start = _time.monotonic()
            try:
                return await call_next(context)
            except _SchemaError:
                try:
                    _audit.log_invocation(
                        tool_name=context.message.name,
                        params=dict(context.message.arguments or {}),
                        result_status="invalid_arguments",
                        duration_ms=(_time.monotonic() - start) * 1000,
                        correlation_id=generate_correlation_id(),
                    )
                except Exception as e:  # noqa: BLE001 - auditing must never hide the real error
                    logger.warning("Audit logging failed for a rejected call: %s", e)
                raise

    mcp.add_middleware(_AuditRejectedCalls())

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
        """Check whether this project is indexed and the index is fresh. Not needed
        before other nexus tools: the index of an earlier session attaches at start. Use
        it after "No codebase indexed", or to see index size and a stale warning. If
        `indexed` is false, call `index`."""
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

    @mcp.tool(annotations=INDEXING, title="Index a project")
    @_audited
    async def index(
        path: Annotated[str, "Absolute path of the project"],
        paths: Annotated[str, "More paths, comma-separated"] = "",
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Build or refresh the code index. Call it once per project, when a tool says
        "No codebase indexed". Pass an absolute path,
        or comma-separated paths for a monorepo. A file watcher then keeps the index
        fresh. Large repositories take a while."""
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
        query: Annotated[str, "What to find, in plain words or code terms"],
        limit: Annotated[int, "Max results"] = 10,
    ) -> dict[str, Any]:
        """Find code by meaning or keyword: "where is...", "how does... work",
        "find the code that...". Returns ranked results with file and line range; the
        first three carry the whole function or class. Exact names in the query
        (`parse_config RetryPolicy`) bring their definitions first. For the full code of
        a cut result, call `find_symbol`. For a known file or exact string, grep or read
        is as good."""
        guard_err = _guard("search")
        if guard_err:
            return guard_err
        return core_api.search(query, limit=limit)

    @mcp.tool(annotations=READ_ONLY, title="Find a symbol")
    @_audited
    def find_symbol(
        symbol_name: Annotated[
            str,
            "One name, or several with commas",
        ],
        exact: Annotated[bool, "False matches part of a name"] = True,
    ) -> dict[str, Any]:
        """Get the whole code of functions, classes, methods or constants by name ("show
        me create_order"), with file, line range, callers and callees. Use it instead of a
        file read or grep for a definition. Pass several names in one call. A class over
        2,000 characters gives an outline; ask for "Class.method" for one method. Code
        over 6,000 characters is cut and marked `(truncated)`. exact=false matches part
        of a name. For every use of a name, use `graph`."""
        guard_err = _guard("find_symbol")
        if guard_err:
            return guard_err
        return core_api.find_symbol(symbol_name, exact=exact)

    @mcp.tool(annotations=READ_ONLY, title="Call graph")
    @_audited
    def graph(
        symbol_name: Annotated[str, "Function or symbol to trace"],
        direction: Annotated[
            Literal["callers", "callees"],
            "'callers' or 'callees'",
        ] = "callers",
        transitive: Annotated[
            bool,
            "All direct and indirect dependents. Only with direction='callers'.",
        ] = False,
    ) -> dict[str, Any]:
        """Trace the call graph: who calls a function (direction='callers') or what it
        calls ('callees'). transitive=true lists everything that depends on it; use it
        before you change, rename or delete a shared function ("what breaks if I change
        X"). A callers result also has `references`, every whole-word use of the name,
        so no grep is needed. Edges are static: calls through callbacks, reflection or
        dynamic dispatch show only in `references`."""
        guard_err = _guard("graph")
        if guard_err:
            return guard_err
        return core_api.graph(symbol_name, direction=direction, transitive=transitive)

    @mcp.tool(annotations=READ_ONLY, title="Analyze code quality")
    @_audited
    def analyze(
        path: Annotated[
            str, "Relative directory or file to limit it to"
        ] = ""
    ) -> dict[str, Any]:
        """Review code quality of the project or one `path`: the most complex functions,
        long functions, large classes, dead code (no static caller), module dependencies
        and a quality score. Use it for "what is the most complex code" or "is there dead
        code". Results are approximate; check dead code before you delete it."""
        guard_err = _guard("analyze")
        if guard_err:
            return guard_err
        return core_api.analyze(path)

    def _map_impl(
        detail: Annotated[
            Literal["summary", "architecture", "full"],
            "'summary', 'architecture' or 'full' (both)",
        ] = "summary",
    ) -> dict[str, Any]:
        """Get an overview of the project: "give me an overview", "how is this
        structured", "where do I start". 'summary' lists files, languages, top modules
        and quality. 'architecture' gives layers, dependencies, entry points and hub
        symbols. 'full' gives both. Use it first in an unfamiliar repository."""
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
            "'store', 'search' or 'delete'",
        ],
        content: Annotated[str, "Note to store"] = "",
        query: Annotated[str, "What to look for"] = "",
        tags: Annotated[str, "Comma-separated tags"] = "",
        memory_id: Annotated[str, "Note ID to delete"] = "",
    ) -> dict[str, Any]:
        """Keep and recall project knowledge across sessions: decisions, preferences,
        conventions. Use action='store' when the user says "remember that..." or a
        decision is made, 'search' for "what did we decide about...", and 'delete' to
        remove old notes by ID or tags. Notes are found by meaning."""
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
            action, content=content, query=query, tags=tags, memory_id=memory_id
        )

    # Each tool description is sent on every turn. The optional tools stay off unless
    # NEXUS_EXTRA_TOOLS names them (config.OPTIONAL_TOOLS gives the reason).
    if _hidden_tools:
        mcp.disable(names=_hidden_tools, components={"tool"})

    return mcp


def main():
    """Entry point for nexus-mcp CLI.

    ``eval`` and ``data`` run a command and exit. With no arguments the stdio server
    starts, as MCP clients expect.
    """
    import sys

    from nexus_mcp.config import get_settings
    from nexus_mcp.privacy import apply_privacy_defaults
    from nexus_mcp.state import get_state

    if len(sys.argv) > 1:
        from nexus_mcp.evaluate.cli import dispatch

        code = dispatch(sys.argv[1:])
        if code is not None:
            raise SystemExit(code)
    apply_privacy_defaults()

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
    if settings.warm_start and settings.auto_restore:
        # Not a tool call: the first `search` then finds the index and the model loaded.
        threading.Thread(target=core_api.warm_up, name="nexus-warm-up", daemon=True).start()
    try:
        server.run()
    finally:
        get_state().shutdown()


if __name__ == "__main__":
    main()
