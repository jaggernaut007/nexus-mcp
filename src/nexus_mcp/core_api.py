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
from pathlib import Path, PurePosixPath
from typing import Any, Callable, List, Optional

from nexus_mcp import __version__
from nexus_mcp.core.graph_models import NodeType, UniversalNode, UniversalRelationship

logger = logging.getLogger(__name__)

# Module-level pipeline reference (persists across calls, mirrors server.py's
# previous module-level `_pipeline`/`_pipeline_lock`).
_pipeline = None
_pipeline_lock = threading.Lock()

# Serialises `restore_session` against itself. An agent often sends `status` and `search` in
# one turn, so two calls reach restore together. The second must wait for the first and then
# find the index attached, not answer "not indexed". Restore takes under a few seconds, and it
# only ever *tries* `_pipeline_lock`, so waiting here cannot wait on a running index.
_restore_lock = threading.Lock()


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
            _rebuild_bridge(state)
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
        from nexus_mcp.config import get_settings

        workspace = get_settings().workspace
        if workspace:
            return None, {
                "error": f"Not a directory: {path}. This server runs in a container and the "
                f"project is mounted at {workspace}. Call index with path '{workspace}'."
            }
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

def _stored_root_set(metadata_path: Path) -> Optional[set]:
    """Resolved project roots named in the index metadata, or None if unreadable."""
    import json

    try:
        meta = json.loads(metadata_path.read_text())
        raw = meta.get("codebase_paths") or [meta.get("codebase_path")]
        return {str(Path(p).resolve()) for p in raw if isinstance(p, str) and p}
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _stored_roots(metadata_path: Path) -> List[Path]:
    """Project roots named in the stored index metadata, or [] if any of them is unsafe.

    The storage folder can come from a clone or an archive, so its contents are not
    trusted. A root is accepted only when it is an absolute path to a folder that is the
    server's working directory or inside it. Otherwise a `.nexus` folder could point the
    server at any directory on the machine, or at a different project than the one the
    agent is working in.
    """
    import json

    try:
        meta = json.loads(metadata_path.read_text())
        raw = meta.get("codebase_paths") or [meta.get("codebase_path")]
        paths = [Path(p) for p in raw if isinstance(p, str) and p]
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    if not paths or len(paths) != len(raw):
        return []
    cwd = Path.cwd().resolve()
    roots = []
    for path in paths:
        if not path.is_absolute() or not path.is_dir():
            return []
        resolved = path.resolve()
        if resolved != cwd and cwd not in resolved.parents:
            return []
        roots.append(path)
    return roots


def storage_base_for(root: Path) -> str:
    """The folder that a relative storage folder resolves against when `root` is indexed.

    Returns "" (the working folder, the behaviour before 2.4.3) when `root` is the working
    folder or inside it. `restore_session` looks there at the next start, so a new session
    attaches the index by itself. Otherwise it returns `root`, so the index goes to
    `<root>/.nexus`. Cline starts the server in `/`, where `/.nexus` is on a read-only
    volume, and a folder outside the project would be shared by unrelated projects.
    A filesystem root never counts as the working folder.
    """
    cwd = Path.cwd().resolve()
    resolved = root.resolve()
    if cwd.parent != cwd and (resolved == cwd or cwd in resolved.parents):
        return ""
    return str(resolved)


def restore_session() -> bool:
    """Reattach to the index that is already on disk. Returns True if a codebase is attached.

    Every new server process starts with no codebase in memory, even when the project
    was indexed in an earlier session. Without this, each session would open with
    `status` saying "not indexed" and `index` having to run before any real work. This
    loads the saved graph and opens the stored tables for the root(s) recorded in the
    index metadata. It does not reparse anything: `status` then reports staleness and
    starts a background reindex if files changed, exactly as in a session that called
    `index`. It does nothing when the index is missing, was built by another embedding
    model, or its root no longer exists, and `index` then builds it as before.
    """
    from nexus_mcp.config import get_settings
    from nexus_mcp.state import get_state

    state = get_state()
    if state.is_indexed:
        return True
    settings = get_settings()
    if not settings.auto_restore:
        return False
    metadata_path = settings.storage_path / "index_metadata.json"
    if not metadata_path.exists():
        return False
    roots = _stored_roots(metadata_path)
    if not roots:
        return False

    # Wait for a restore that is already running (see `_restore_lock`), then look again.
    with _restore_lock:
        if state.is_indexed:
            return True
        return _restore_locked(state, settings, roots)


def warm_up() -> bool:
    """Attach the stored index and load the embedding model before the first tool call.

    The server runs this in a background thread at start. Measured on a 45,000-chunk
    index: the restore takes about 2 s and the model load about 7 s, and without this the
    first `search` of every session pays both. Returns True when a search ran. It never
    raises: a failure only means the first tool call does the work, as before.
    """
    from nexus_mcp.state import get_state

    try:
        if not restore_session():
            return False
        engine = get_state().vector_engine
        if engine is None:
            return False
        engine.search("warm up", limit=1)
        return True
    except Exception as e:  # noqa: BLE001 - a warm-up must never stop the server
        logger.warning("Warm-up failed: %s", e)
        return False


def _attach_linked(state, settings) -> None:
    """Attach the linked repos of the project, read-only (see linked.py, ADR-027).

    A failure here never stops the primary project: the session then has no linked repo.
    """
    from nexus_mcp import linked

    try:
        state.linked = linked.attach_all(state.codebase_path, settings)
    except Exception as e:  # noqa: BLE001 - the primary project must keep working
        logger.warning("Could not attach the linked repos: %s", e)
        state.linked = []
    _rebuild_bridge(state)


def _rebuild_bridge(state) -> None:
    """Match the client calls and the routes of the session again (ADR-027, phase 2.4).

    The edges stay in memory: no graph holds them and none is saved with them. Run it after
    the repos are attached and after a reindex of the primary project. A failure leaves the
    session with no HTTP edge, and `graph` then answers from the CALLS edges as before.
    """
    from nexus_mcp.indexing.service_resolver import PRIMARY, resolve_services

    try:
        graphs = {PRIMARY: state.graph_engine}
        roots = {PRIMARY: list(state.codebase_paths or [state.codebase_path])}
        for repo in _attached_linked(state):
            graphs[repo.name] = repo.graph_engine
            roots[repo.name] = [repo.path]
        state.bridge = resolve_services(graphs, roots)
    except Exception as e:  # noqa: BLE001 - HTTP edges are an addition, never a failure
        logger.warning("Could not match the client calls with the routes: %s", e)
        state.bridge = None


def _restore_locked(state, settings, roots) -> bool:
    """The body of `restore_session`. The caller holds `_restore_lock`."""
    global _pipeline
    # Never wait for the index lock: if an index is running, it attaches the codebase when
    # it finishes and this call reports "not indexed" now.
    if not _pipeline_lock.acquire(blocking=False):
        return False
    try:
        if state.is_indexed:
            return True
        from nexus_mcp.indexing.pipeline import IndexingPipeline

        pipeline = _pipeline or IndexingPipeline(settings)
        if not pipeline._validate_index() or not pipeline._restore_graph():
            return False
        _pipeline = pipeline
        state.codebase_path = roots[0]
        state.codebase_paths = roots
        state.vector_engine = pipeline.vector_engine
        state.bm25_engine = pipeline.bm25_engine
        state.graph_engine = pipeline.graph_engine
        state._staleness_cache = None
        logger.info("Restored the index of %s from %s", roots[0], settings.storage_path)
        _attach_linked(state, settings)
        return True
    except Exception as e:  # noqa: BLE001 - a bad saved index must not break the tool call
        logger.warning("Could not restore the saved index: %s", e)
        return False
    finally:
        _pipeline_lock.release()


def require_indexed():
    """Check that a codebase is indexed and graph engine is available.

    Returns (state, None) on success, or (None, error_dict) on failure.
    """
    from nexus_mcp.state import get_state

    state = get_state()
    if not state.is_indexed:
        restore_session()
    if not state.is_indexed or not state.graph_engine:
        return None, {"error": "No codebase indexed. Run 'index' first."}
    # `status` and `search` already check for changed files. The graph tools did not, so
    # after a restored session they could serve an old graph. The check is throttled.
    if _get_staleness(state)["stale"]:
        _trigger_background_reindex(state.codebase_path, state.codebase_paths)
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


def _serialize_node_compact(
    node: UniversalNode, codebase_path: Optional[Path] = None
) -> dict[str, Any]:
    """The few fields that name a symbol and say where it is. Used by `graph`."""
    full = _serialize_node(node, codebase_path)
    loc = full["location"]
    return {
        "name": full["name"],
        "type": full["type"],
        "file": loc["file"],
        "start_line": loc["start_line"],
        "end_line": loc["end_line"],
    }


MAX_DEFINED_IN = 5  # a bare name with more definitions than this gets no `defined_in`


def _defined_in(state, matches: List[UniversalNode]) -> Optional[dict[str, list[str]]]:
    """Where the asked symbol itself is defined, as `file -> ["Class.method:line"]`.

    An impact answer must name the file of the symbol, not only its callers and overrides.
    On Django `graph("Field.validate")` gave the overrides, and the agent left out
    `fields/__init__.py` in 2 of 6 runs. None for a name with many definitions: the
    callers and references already cover it.
    """
    if not matches or len(matches) > MAX_DEFINED_IN:
        return None
    grouped: dict[str, list[str]] = {}
    for node in matches:
        short = _serialize_node_compact(node, state.codebase_path)
        owner = (node.metadata or {}).get("parent_class")
        label = f"{owner}.{node.name}" if owner else node.name
        grouped.setdefault(short["file"], []).append(f"{label}:{short['start_line']}")
    return grouped


def _group_by_file(
    nodes: List[UniversalNode], codebase_path: Optional[Path] = None
) -> dict[str, list[str]]:
    """Symbols as `file -> ["name:line", ...]`, source files before test files.

    A list of one dict for each symbol repeats the file path and the key names for every
    symbol. This shape holds the same facts in about a third of the characters. A test
    file keeps its first few symbols and a count of the rest: in the benchmark one file
    held 40 test functions that call one symbol, and an agent needs the file, not the 40.
    """
    return _collapse_test_symbols(_group_names(nodes, codebase_path))


def _group_names(
    nodes: List[UniversalNode], codebase_path: Optional[Path] = None
) -> dict[str, list[str]]:
    """Symbols as `file -> ["name:line", ...]`, in the order given, with no file cut."""
    grouped: dict[str, list[str]] = {}
    for node in nodes:
        short = _serialize_node_compact(node, codebase_path)
        grouped.setdefault(short["file"], []).append(f"{short['name']}:{short['start_line']}")
    return grouped


# Languages whose class nodes carry base classes (`AstGrepParser._class_bases`), so that
# `graph` can list the overrides of a method. Keep the two in step.
_LANGUAGES_WITH_BASES = frozenset({"python", "javascript", "typescript", "tsx"})

MAX_GRAPH_SOURCE_FILES = 60  # source files of callers listed by `graph`; the rest are counts
MAX_GRAPH_TEST_FILES = 10  # test files of callers listed by `graph`; the rest are counts
MAX_OMITTED_DIRS = 10  # folders named in the count of the files that are not listed


def _cap_files(
    grouped: dict[str, list[str]],
) -> tuple[dict[str, list[str]], Optional[dict[str, Any]]]:
    """Keep the first source and test files of a `graph` result. Count the rest by folder.

    On Home Assistant `graph("async_setup_component")` named 4,979 callers in about 3,000
    files, mostly tests: 222,303 characters, about 55k tokens. Claude Code does not give
    the agent an MCP result above 25k tokens in full, so the agent grepped again and lost
    `defined_in`. The files are kept in the order given: a transitive walk gives the
    nearest callers first, and a direct caller must not lose its place to a far one.
    Returns the listed files (test symbols collapsed) and the count of the rest, or None
    when every file is listed.
    """
    from collections import Counter

    from nexus_mcp.engines.fusion import is_test_path

    ordered = list(grouped)
    source = [f for f in ordered if not is_test_path(f)]
    tests = [f for f in ordered if is_test_path(f)]
    kept = source[:MAX_GRAPH_SOURCE_FILES] + tests[:MAX_GRAPH_TEST_FILES]
    dropped = source[MAX_GRAPH_SOURCE_FILES:] + tests[MAX_GRAPH_TEST_FILES:]
    listed = _collapse_test_symbols({f: grouped[f] for f in kept})
    if not dropped:
        return listed, None
    dirs = Counter("/".join(PurePosixPath(f).parts[:-1][:2]) or "." for f in dropped)
    omitted: dict[str, Any] = {
        "files": len(dropped),
        "test_files": len(dropped) - max(0, len(source) - MAX_GRAPH_SOURCE_FILES),
        "symbols": sum(len(grouped[f]) for f in dropped),
        "by_folder": dict(dirs.most_common(MAX_OMITTED_DIRS)),
    }
    return listed, omitted


def _collapse_test_symbols(grouped: dict[str, list[str]]) -> dict[str, list[str]]:
    """Source files first. A test file with many symbols keeps a few and a count."""
    from nexus_mcp.engines.fusion import is_test_path

    out = {}
    for path in sorted(grouped, key=lambda f: (is_test_path(f), f)):
        entries = grouped[path]
        if is_test_path(path) and len(entries) > MAX_TEST_SYMBOLS_PER_FILE:
            rest = len(entries) - MAX_TEST_SYMBOLS_PER_FILE
            entries = entries[:MAX_TEST_SYMBOLS_PER_FILE] + [f"+{rest} more"]
        out[path] = entries
    return out


def _symbol_compact(node: UniversalNode, state) -> dict[str, Any]:
    """One `find_symbol` entry: where the symbol is, and its callers and callees by name."""
    entry: dict[str, Any] = _serialize_node_compact(node, state.codebase_path)
    full = _serialize_node(node, state.codebase_path)
    if full.get("docstring"):
        entry["docstring"] = _trim_snippet(full["docstring"], 300)
    if full.get("complexity"):
        entry["complexity"] = full["complexity"]
    for key, related in (
        ("callers", state.graph_engine.get_callers(node.id)),
        ("callees", state.graph_engine.get_callees(node.id)),
    ):
        if not related:
            continue
        entry[key] = _group_by_file(related[:MAX_SYMBOL_RELATIONS], state.codebase_path)
        if len(related) > MAX_SYMBOL_RELATIONS:
            entry[f"{key}_total"] = len(related)
    return entry


def _serialize_relationship(rel: UniversalRelationship) -> dict[str, Any]:
    """Convert a UniversalRelationship to a JSON-serializable dict."""
    return {
        "type": rel.relationship_type.value,
        "source_id": rel.source_id,
        "target_id": rel.target_id,
        "strength": rel.strength,
    }


def _resolve_symbol(graph, name: str, exact: bool = True) -> List[UniversalNode]:
    """Find nodes by name in the graph.

    A method is in the graph by its bare name, with its class in ``parent_class``. So a
    name with no node of its own, such as `Field.validate` or `SQLCompiler::as_sql`, gives
    the methods of that name whose class has the name of the part before it.
    """
    nodes = graph.find_nodes_by_name(name, exact=exact)
    if nodes or not exact:
        return nodes
    return _qualified_methods(graph, name)


def _qualified_methods(graph, name: str) -> List[UniversalNode]:
    """The methods that a `Class.method` or `Class::method` name points at, or []."""
    parts = [p for p in re.split(r"\.|::", name) if p]
    if len(parts) < 2:
        return []
    owner, method = parts[-2], parts[-1]
    return [
        n for n in graph.find_nodes_by_name(method, exact=True)
        if n.node_type == NodeType.FUNCTION and (n.metadata or {}).get("parent_class") == owner
    ]


def _subclasses(graph, class_name: str) -> List[UniversalNode]:
    """Every class that inherits from ``class_name``, directly or through other classes.

    Bases are names, as the parser records them (`metadata["bases"]`), so two classes of the
    same name in different modules are one class here. The answer is a lower bound: a base
    given by an expression (`extends mix(A)`) is not seen.
    """
    children: dict[str, list[UniversalNode]] = {}
    for node in graph.get_nodes_by_type(NodeType.CLASS):
        for base in (node.metadata or {}).get("bases", ()):
            children.setdefault(base, []).append(node)
    found: dict[str, UniversalNode] = {}
    todo = [class_name]
    seen_names = {class_name}
    while todo:
        for child in children.get(todo.pop(), ()):
            if child.id in found:
                continue
            found[child.id] = child
            if child.name not in seen_names:
                seen_names.add(child.name)
                todo.append(child.name)
    return list(found.values())


def _overrides(state, methods: List[UniversalNode]) -> Optional[dict[str, Any]]:
    """The methods of subclasses that override one of ``methods``, by file.

    The answer to "which subclasses override X.m and must change with it". Each entry is
    `Class.method:line`. Returns None when no method has a class, or no subclass overrides it.
    """
    graph = state.graph_engine
    by_file: dict[str, list[str]] = {}
    seen: set[str] = set()
    subclass_ids: set[str] = set()
    family: dict[str, list[UniversalNode]] = {}  # one closure per owner name
    for method in methods:
        owner = (method.metadata or {}).get("parent_class")
        if not owner:
            continue
        if owner not in family:
            family[owner] = _subclasses(graph, owner)
        subclasses = family[owner]
        # Matches in `fields.py` and `fields.pyi` share one owner: count each class once.
        subclass_ids.update(c.id for c in subclasses)
        places = {(c.name, c.location.file_path) for c in subclasses}
        for node in graph.find_nodes_by_name(method.name, exact=True):
            cls = (node.metadata or {}).get("parent_class")
            if node.id in seen or (cls, node.location.file_path) not in places:
                continue
            seen.add(node.id)
            short = _serialize_node_compact(node, state.codebase_path)
            by_file.setdefault(short["file"], []).append(
                f"{cls}.{node.name}:{node.location.start_line}"
            )
    if not by_file:
        return None
    return {
        "total": len(seen),
        "subclasses": len(subclass_ids),
        "files": _collapse_test_symbols(by_file),
    }


def _add_overrides(result: dict[str, Any], state, matches: List[UniversalNode]) -> None:
    """Add the subclass overrides of the matched methods to a `graph` callers result.

    Only when every match is a method of one class (`Field.validate`, or a bare name with
    one owner). A bare name such as `validate` with many owners would list the overrides of
    unrelated classes.
    """
    owners = {(n.metadata or {}).get("parent_class") for n in matches}
    if len(owners) != 1 or None in owners:
        return
    overrides = _overrides(state, matches)
    if overrides:
        result["overrides"] = overrides


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
    restore_session()
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

        if state.linked:
            from nexus_mcp import linked

            interval = state.settings.staleness_check_interval_s
            result["linked"] = [linked.describe(repo, interval) for repo in state.linked]
            old = [entry["name"] for entry in result["linked"] if entry["stale"]]
            if old:
                result["linked_warning"] = (
                    f"Linked repos out of date: {', '.join(old)}. "
                    "Run `nexus-mcp-ci link --refresh` to index them again."
                )

        result["hint"] = (
            "Codebase is indexed. Use 'search' to find code you cannot name, "
            "'find_symbol' for definitions, 'graph' for who calls what, "
            "'explain' to understand a symbol, 'map' for the project layout, "
            "and 'graph(transitive=True)' before changing a shared function."
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

    def _locked_index():
        # Acquire, index and release in one worker thread. A cancelled `await` then
        # cannot separate the lock from the work: it neither leaks the lock (cancel while
        # waiting for it) nor frees it while the thread still writes (cancel mid-index).
        global _pipeline
        with _pipeline_lock:
            old_storage = settings.storage_path
            settings.storage_base = storage_base_for(validated[0])
            if _pipeline is None or settings.storage_path != old_storage:
                # The pipeline opens its tables when it is made, so a new storage folder
                # needs a new pipeline. The memory store is in the same folder.
                _pipeline = IndexingPipeline(settings)
                get_state().memory_store = None

            if len(validated) > 1:
                result = _pipeline.multi_index(validated, _cb)
                state = get_state()
                state.codebase_path = validated[0]
                state.codebase_paths = validated
            else:
                codebase_path = validated[0]
                metadata_path = settings.storage_path / "index_metadata.json"
                # Incremental only if the stored index belongs to this same project. A
                # shared storage folder can hold another project's index; diffing against
                # it would mix the two, so build from scratch instead.
                if metadata_path.exists() and _stored_root_set(metadata_path) == {
                    str(codebase_path.resolve())
                }:
                    result = _pipeline.incremental_index(codebase_path, _cb)
                else:
                    result = _pipeline.index(codebase_path, _cb)
                state = get_state()
                state.codebase_path = codebase_path
                state.codebase_paths = [codebase_path]

            state.vector_engine = _pipeline.vector_engine
            state.bm25_engine = _pipeline.bm25_engine
            state.graph_engine = _pipeline.graph_engine
            state._staleness_cache = None
            _attach_linked(state, settings)
            return result, state

    # shield: a cancelled caller stops waiting, but the thread finishes and releases the lock.
    result, state = await asyncio.shield(asyncio.to_thread(_locked_index))

    await _ensure_file_watcher(state, settings.auto_watch_enabled)

    # The server version: `status` is hidden by default, and an agent must be able to see
    # which build answers (an old build in another config looks the same from the client).
    return {**result.to_dict(), "version": __version__}


# --- Search -----------------------------------------------------------------

# The live benchmark measured a median 16,000 characters per `search` result, 62% of it
# snippet text, and 18,500 per `graph` result. These limits and the compact shape cut that.
DETAIL_LEVELS = ("compact", "full")
# The top results carry the whole symbol in most cases (the 90th percentile chunk of a
# large project is about 1,600 characters), so the agent does not need a file read after
# the search. In the benchmark traces a read of the top hit followed 9 of 12 searches.
COMPACT_SNIPPET_CHARS = 2000  # the top COMPACT_TOP_RESULTS results
COMPACT_TAIL_SNIPPET_CHARS = 160  # the rest: the signature and one docstring line
COMPACT_TOP_RESULTS = 3
FULL_SNIPPET_CHARS = 2000
# Each engine returns this many times `limit`, so that enough source results are left
# after test files move down (see `fusion.demote_test_files`). Fusion gets 2 x `limit`.
SEARCH_OVERFETCH = 4
MAX_REFERENCE_LINES = 60  # source lines with their text; test files are counts only
# Files that can hold a C or C++ prototype (a one-line declaration of a function).
_C_FAMILY_SUFFIXES = frozenset({".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"})
MAX_REFERENCE_LINES_PER_FILE = 8
MAX_TEST_SYMBOLS_PER_FILE = 3  # a test file with more callers shows a count of the rest
# Fields that help ranking or debugging but not an agent's answer. `signature` and
# `docstring` are also the first lines of the chunk text, so the snippet carries them.
# `parent` is part of the qualified `symbol_name`. The extension of `filepath` says the
# `language`.
_SEARCH_INTERNAL_FIELDS = (
    "id", "score", "rrf_score", "_fusion_sources", "absolute_path",
    "signature", "docstring", "parent", "language",
)
MAX_SYMBOL_RELATIONS = 20
# `find_symbol` returns the code of a symbol, so that no file read follows. In the live
# benchmark the agent read 190 to 270 lines of a file to see one function of 130 lines.
FIND_SYMBOL_MAX_NAMES = 8
SYMBOL_CODE_CHARS = 6000  # one symbol
SYMBOL_CODE_BUDGET = 12000  # all symbols of one call
MAX_SYMBOL_SOURCE_LINES = 1000  # lines read from a file for one symbol
MIN_CODE_BUDGET = 200  # a smaller leftover gives a stub, so the symbol gets no code
MAX_MATCHES_PER_NAME = 10  # a fuzzy name can match hundreds of symbols
NAME_CHECK_LINES = 12  # the symbol name must be in the first lines of its code
MAX_SYMBOL_MATCHES_WITH_CODE = 3  # a name with more definitions shows the place of the rest
# A class longer than this gets an outline (head and method lines), not its body. In the
# django benchmark (2026-10-27) a class body was larger than the read that it replaced.
CLASS_OUTLINE_MIN_CHARS = 2000
CLASS_OUTLINE_HEAD_LINES = 25
CLASS_OUTLINE_HEAD_CHARS = 1200
MAX_OUTLINE_METHODS = 60
MAX_OUTLINE_SOURCE_LINES = 5000
# `detail="full"` of `graph` and `verbosity="full"` of `explain` list at most this many
# symbols with all their fields. One `graph` call returned 20,000 characters without it.
MAX_FULL_NODES = 25


def _chunk_code(text: str, signature: str = "", docstring: str = "") -> str:
    """The code of a chunk text, without the parts that repeat it.

    A chunk text is built for the embedding model: a `# path:line` line, a
    `type: name` line, the signature, the docstring, the code, then `Imports:` and
    `Calls:` lines. The code already starts with the signature and the docstring, so
    an agent that gets the whole text reads them twice. Returns the text without its
    header when no code part is found.
    """
    without_header = _strip_chunk_header(text)
    rest = without_header
    first, sep, tail = rest.partition("\n\n")
    if sep and "\n" not in first and ": " in first:
        rest = tail
    for lead in (signature or "", (docstring or "")[:500]):
        if lead and rest.startswith(lead):
            rest = rest[len(lead):].lstrip("\n")
    lines = rest.rstrip().split("\n")
    while lines and lines[-1].startswith(("Imports: ", "Calls: ")):
        lines.pop()
    code = "\n".join(lines).rstrip()
    return code or without_header


def _strip_chunk_header(code: str) -> str:
    """Drop the `# path:line` first line of a chunk text. `filepath` and `line_start` say it."""
    first, sep, rest = code.partition("\n")
    return rest if sep and first.startswith("# ") and ":" in first else code


def _trim_snippet(code: str, limit: int) -> str:
    """Cut ``code`` to at most ``limit`` characters, at a line boundary when one exists."""
    if len(code) <= limit:
        return code
    cut = code[:limit]
    newline = cut.rfind("\n")
    if newline > limit // 2:
        cut = cut[:newline]
    return cut + "\n... (truncated)"


def _tail_snippet(code: str, signature: str = "", docstring: str = "") -> str:
    """The snippet of a result below the top: the signature and the first docstring line.

    It tells the agent what the symbol is. It has no `(truncated)` mark, because each
    result below the top is cut and the hint of the response says so.
    """
    head = signature.strip() or code.strip().split("\n", 1)[0]
    doc = docstring.strip().split("\n", 1)[0]
    text = f"{head}\n{doc}" if doc and doc not in head else head
    text = text[:COMPACT_TAIL_SNIPPET_CHARS].rstrip()
    # A module chunk: the names that the file defines say more than a second line.
    for line in code.split("\n"):
        if line.startswith("Defines: "):
            return f"{text}\n{line[:COMPACT_TAIL_SNIPPET_CHARS]}"
    return text


def _compact_search_result(r: dict[str, Any]) -> dict[str, Any]:
    """Drop internal fields and empty values from one search result."""
    return {
        k: v
        for k, v in r.items()
        if k not in _SEARCH_INTERNAL_FIELDS
        and v is not None
        and not (isinstance(v, (str, list, dict)) and not v)
    }


def _drop_declarations_of_defined_names(results: list[dict]) -> list[dict]:
    """Drop a one-line declaration whose name has a body elsewhere in the results.

    A C or C++ prototype (`removeExpire(redisDb *db, robj *key)` in `server.h`) is a chunk of
    its own. Next to the definition it repeats the signature and takes a result slot. Only
    C and C++ files have such prototypes: a one-line `init` in `a.js` is a definition of
    its own and stays.
    """

    def one_line(r: dict) -> bool:
        return r.get("line_start") is not None and r.get("line_start") == r.get("line_end")

    def c_family(r: dict) -> bool:
        if r.get("language") in ("c", "cpp"):
            return True
        path = str(r.get("filepath") or r.get("file_path") or "")
        return PurePosixPath(path).suffix.lower() in _C_FAMILY_SUFFIXES

    defined = {r.get("symbol_name") for r in results if r.get("symbol_name") and not one_line(r)}
    return [
        r for r in results
        if not (one_line(r) and c_family(r) and r.get("symbol_name") in defined)
    ]


def _attached_linked(state) -> list:
    """The linked repos of the session whose stores are open. [] when there is no link."""
    return [repo for repo in (getattr(state, "linked", None) or []) if repo.attached]


def _tag_repo(found: list[dict], repo) -> list[dict]:
    """Mark the results of a linked repo with its name (`_repo`, removed before output)."""
    for r in found:
        r["_repo"] = repo.name
    return found


def _merge_by_score(lists: list[list[dict]]) -> list[dict]:
    """One list from the lists of several stores, best score first.

    The score is the same kind in every store: the vector score is 1 / (1 + distance) of
    one model, and the BM25 score is divided by the best score of its store, so the best
    keyword result of each store has the score 1. The sort is stable: on a tie the
    primary project comes first. One list is returned as it is.
    """
    if len(lists) == 1:
        return lists[0]
    merged = [r for found in lists for r in found]
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    # A folder that two stores both hold (a link to a sub-folder of the project) gives the
    # same chunk id in both. Keep the first copy, so that fusion counts the chunk one time.
    seen: set = set()
    unique = []
    for r in merged:
        rid = r.get("id")
        if rid is not None:
            if rid in seen:
                continue
            seen.add(rid)
        unique.append(r)
    return unique


def _search_linked(
    repos: list, query: str, limit: int, kwargs: dict, mode: str, query_vector
) -> tuple[list[list[dict]], list[list[dict]]]:
    """Vector and BM25 results of each linked repo, as (vector lists, BM25 lists).

    A store that fails is left out with a warning. Nothing is written to a linked store:
    its BM25 engine is read-only (ADR-027).
    """
    vector_lists: list[list[dict]] = []
    bm25_lists: list[list[dict]] = []
    for repo in repos:
        if mode in ("hybrid", "vector") and query_vector is not None:
            try:
                found = repo.vector_engine.search(
                    query, limit=limit, query_vector=query_vector, **kwargs
                )
                vector_lists.append(_tag_repo(found, repo))
            except Exception as e:  # noqa: BLE001 - one linked store must not stop the search
                logger.warning("Vector search of linked repo %r failed: %s", repo.name, e)
        if mode in ("hybrid", "bm25") and repo.bm25_engine:
            try:
                found = repo.bm25_engine.search(query, limit=limit, **kwargs)
                if found:
                    bm25_lists.append(_tag_repo(found, repo))
            except Exception as e:  # noqa: BLE001
                logger.warning("BM25 search of linked repo %r failed: %s", repo.name, e)
    return vector_lists, bm25_lists


def _linked_definitions(repos: list, names: list[str]) -> list[dict]:
    """The chunks that define one of ``names`` in the linked repos (no embedding is run)."""
    found: list[dict] = []
    for repo in repos:
        try:
            found += _tag_repo(repo.vector_engine.find_by_symbol_names(names), repo)
        except Exception as e:  # noqa: BLE001 - an addition, never a failure
            logger.debug("Exact name lookup in linked repo %r failed: %s", repo.name, e)
    return found


def _resolve_in_scope(state, scope, name: str, exact: bool) -> list:
    """The graph nodes of `name` in one scope. A linked repo that fails gives no node and a
    warning: it must not stop the answer of the primary project."""
    if scope is state:
        return _resolve_symbol(scope.graph_engine, name, exact=exact)
    try:
        return _resolve_symbol(scope.graph_engine, name, exact=exact)
    except Exception as e:  # noqa: BLE001 - one bad linked repo must not stop the call
        logger.warning("find_symbol: linked repo %r failed: %s", scope.name, e)
        return []


def _linked_stale_warning(names: list[str]) -> Optional[str]:
    """The `search` warning for linked repos that changed after their last index."""
    if not names:
        return None
    from nexus_mcp import linked

    label = "repo" if len(names) == 1 else "repos"
    quoted = ", ".join(repr(n) for n in names)
    return (
        f"Linked {label} {quoted} changed after indexing. Results in the changed files have "
        f"no snippet. {linked.REFRESH_HINT}"
    )


def search(
    query: str,
    limit: int = 10,
    language: str = "",
    symbol_type: str = "",
    mode: str = "hybrid",
    live_grep: bool = False,
    detail: str = "compact",
) -> dict[str, Any]:
    """Primary code discovery: hybrid vector + BM25 search with RRF fusion, exact names
    first, and an automatic live-grep fallback when results are sparse.

    ``detail="compact"`` (default) drops internal fields, gives the whole symbol (up to
    ``COMPACT_SNIPPET_CHARS``) for the first ``COMPACT_TOP_RESULTS`` results and the
    signature for the others, and returns only ``total``, ``results``, ``hint`` and a
    ``warning`` when the index is stale. ``detail="full"`` keeps scores, ids, absolute
    paths, the query, the engine names and snippets up to ``FULL_SNIPPET_CHARS``.

    Linked repos (ADR-027): each attached linked repo is searched too. The query is embedded
    one time. The vector lists and the BM25 lists of all stores merge by score before the RRF.
    A path of a linked repo starts with the repo name. A changed file of a linked repo gives
    ``changed_since_index`` and no snippet, and the ``warning`` names the repo. A search never
    writes to a linked store. The live-grep fallback reads the primary roots only."""
    from nexus_mcp.config import get_settings
    from nexus_mcp.engines.fusion import (
        ReciprocalRankFusion,
        demote_test_files,
        exact_names_first,
        query_identifiers,
    )
    from nexus_mcp.state import get_state

    err = validate_query(query)
    if err:
        return err
    if detail not in DETAIL_LEVELS:
        return {"error": f"Invalid detail: {detail}. Must be one of {list(DETAIL_LEVELS)}"}

    state = get_state()
    if not state.is_indexed:
        restore_session()
    if not state.is_indexed or not state.vector_engine:
        return {"error": "No codebase indexed. Run 'index' first."}

    staleness = _get_staleness(state)
    search_warning = None
    if staleness["stale"]:
        search_warning = (
            f"Index may be out of date ({staleness['reason']}); refreshing in background. "
            "Results in changed files have no snippet."
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
    overfetch = limit * SEARCH_OVERFETCH
    linked_repos = _attached_linked(state)

    vector_lists: list[list[dict]] = []
    query_vector = None
    if mode in ("hybrid", "vector"):
        try:
            search_args = {}
            if linked_repos:
                # One embedding for every store (ADR-027).
                query_vector = state.vector_engine.embed_query(query)
                search_args["query_vector"] = query_vector
            vector_lists.append(
                state.vector_engine.search(query, limit=overfetch, **search_args, **kwargs)
            )
        except Exception as e:
            logger.warning("Vector search failed: %s", e)

    bm25_lists: list[list[dict]] = []
    if mode in ("hybrid", "bm25") and state.bm25_engine:
        try:
            bm25_results = state.bm25_engine.search(query, limit=overfetch, **kwargs)
            if bm25_results:
                bm25_lists.append(bm25_results)
        except Exception as e:
            logger.warning("BM25 search failed: %s", e)

    if linked_repos:
        more_vector, more_bm25 = _search_linked(
            linked_repos, query, overfetch, kwargs, mode, query_vector
        )
        vector_lists += more_vector
        bm25_lists += more_bm25

    if vector_lists:
        ranked_lists["vector"] = _merge_by_score(vector_lists)[:overfetch]
        engines_used.append("vector")
    if bm25_lists:
        ranked_lists["bm25"] = _merge_by_score(bm25_lists)[:overfetch]
        engines_used.append("bm25")

    # Each list: source before tests, then the same length that fusion always got. A
    # wider list into fusion lowered hit@1 in the eval; a list of tests only is worse.
    ranked_lists = {
        name: demote_test_files(query, found)[: limit * 2] for name, found in ranked_lists.items()
    }

    if len(ranked_lists) > 1:
        fusion = ReciprocalRankFusion(
            weights={
                "vector": settings.fusion_weight_vector,
                "bm25": settings.fusion_weight_bm25,
            }
        )
        results = fusion.fuse(ranked_lists)
    elif ranked_lists:
        results = list(ranked_lists.values())[0]
    else:
        results = []

    # Source before tests, before the cut to `limit`. There is no reranker: with two
    # FlashRank models it lowered hit@1 on two of four suites (`nexus_mcp` 0.76 to 0.52) and
    # added 0.1 to 7 s to a query, so it was removed on 2026-10-08.
    results = demote_test_files(query, results)
    # A name in the query (`parse_config`, `RetryPolicy`) is a request for its definition.
    # In the live benchmark the agent sent such names to grep.
    names = query_identifiers(query) if mode == "hybrid" else []
    if names:
        try:
            definitions = [
                d
                for d in state.vector_engine.find_by_symbol_names(names)
                if (not language or d.get("language") == language)
                and (not symbol_type or d.get("symbol_type") == symbol_type)
            ]
            definitions += [
                d
                for d in _linked_definitions(linked_repos, names)
                if (not language or d.get("language") == language)
                and (not symbol_type or d.get("symbol_type") == symbol_type)
            ]
            if definitions:
                seen_ids: set = set()
                definitions = [
                    d
                    for d in definitions
                    if d.get("id") is None or not (d["id"] in seen_ids or seen_ids.add(d["id"]))
                ]
                results = exact_names_first(query, results, definitions)
                engines_used.append("exact_name")
        except Exception as e:  # noqa: BLE001 - an addition, never a failure
            logger.debug("Exact name lookup failed: %s", e)
    results = _drop_declarations_of_defined_names(results)[:limit]

    roots = _index_roots(state)
    if not symbol_type and (live_grep or (mode == "hybrid" and len(results) < limit)):
        try:
            from nexus_mcp.engines.live_grep import LiveGrepEngine
            from nexus_mcp.parsing.language_registry import get_language_for_file

            live_results = []
            for root in roots:  # every root of a multi-root index, not only the first
                live_results += LiveGrepEngine(str(root)).search(query, limit=limit)
                if len(live_results) >= limit:
                    break

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

    changed_paths = staleness.get("changed_paths") or set()
    compact = detail == "compact"
    stale_linked: list[str] = []
    repo_by_name = {repo.name: repo for repo in linked_repos}
    linked_changed: dict[str, set] = {}  # a linked repo is checked once, if it has a result
    for rank, r in enumerate(results):
        r.pop("vector", None)
        repo = repo_by_name.get(r.pop("_repo", None))
        if repo is not None and repo.name not in linked_changed:
            from nexus_mcp import linked

            repo_staleness = linked.staleness(repo, settings.staleness_check_interval_s)
            linked_changed[repo.name] = (
                set(repo_staleness.get("changed_paths") or ()) if repo_staleness["stale"] else set()
            )
            if repo_staleness["stale"]:
                stale_linked.append(repo.name)

        signature, docstring = r.get("signature") or "", r.get("docstring") or ""
        tail = compact and rank >= COMPACT_TOP_RESULTS
        snippet_limit = COMPACT_SNIPPET_CHARS if compact else FULL_SNIPPET_CHARS
        if "text" in r:
            code = r.pop("text")
            if compact:
                code = _chunk_code(code, signature, docstring)
            r["code_snippet"] = (
                _tail_snippet(code, signature, docstring)
                if tail
                else _trim_snippet(code, snippet_limit)
            )
        elif compact and r.get("code_snippet"):
            r["code_snippet"] = (
                _tail_snippet(r["code_snippet"], signature, docstring)
                if tail
                else _trim_snippet(r["code_snippet"], snippet_limit)
            )

        abs_path = r.get("absolute_path") or r.get("filepath")
        if not abs_path:
            continue

        r["absolute_path"] = str(abs_path)
        # A stored snippet of a changed file is not the code on disk. After a restore the
        # stored index can even come from the repository itself (a shipped `.nexus`).
        # Live-grep results are read from disk now, so they keep theirs.
        own_changed = linked_changed[repo.name] if repo is not None else changed_paths
        if r["absolute_path"] in own_changed and not str(r.get("search_mode", "")).startswith(
            "live_grep"
        ):
            r.pop("code_snippet", None)
            r["changed_since_index"] = True

        if repo is not None:
            # The repo name comes first. A multi-root index has no root prefix (ADR-027).
            try:
                r["filepath"] = f"{repo.name}/{Path(abs_path).relative_to(repo.path)}"
            except ValueError:
                r["filepath"] = str(abs_path)
        else:
            for root in roots:
                try:
                    r["filepath"] = str(Path(abs_path).relative_to(root))
                    break
                except ValueError:
                    r["filepath"] = str(abs_path)

        if not r.get("language") or r.get("language") == "unknown":
            from nexus_mcp.parsing.language_registry import get_language_for_file

            r["language"] = get_language_for_file(r["absolute_path"]) or "unknown"

    linked_warning = _linked_stale_warning(stale_linked)
    if linked_warning:
        search_warning = f"{search_warning} {linked_warning}" if search_warning else linked_warning

    if compact:
        # No echo of the query and no engine names: the agent sent the first and does
        # not use the second. `detail="full"` keeps them.
        response: dict[str, Any] = {
            "total": len(results),
            "results": [_compact_search_result(r) for r in results],
        }
        if search_warning:
            response["warning"] = search_warning
        if len(results) > COMPACT_TOP_RESULTS:
            response["hint"] = (
                f"The first {COMPACT_TOP_RESULTS} hold the whole code; the others the "
                "signature only. Get more code with one find_symbol call, not a read or grep."
            )
        return response

    return {
        "query": query,
        "total": len(results),
        "search_mode": mode,
        "engines_used": engines_used,
        "results": results,
        "warning": search_warning,
        "hint": "Results include code_snippet — you can often answer without a file read.",
    }


# --- Graph analysis -----------------------------------------------------------

def _split_symbol_names(symbol_name: str) -> List[str]:
    """The names of a `find_symbol` call: one name, or several with commas, spaces or `|`."""
    names: List[str] = []
    for name in re.split(r"[\s,|]+", symbol_name.strip()):
        if re.search(r"\w", name) and name not in names:
            names.append(name)
    return names[:FIND_SYMBOL_MAX_NAMES]


def _index_roots(state) -> list[Path]:
    """The folders that the index was built from."""
    roots = list(getattr(state, "codebase_paths", None) or [])
    if not roots and getattr(state, "codebase_path", None):
        roots = [state.codebase_path]
    return roots


def _source_lines(
    path: str, start_line: int, end_line: int, roots: list[Path],
    max_lines: int = MAX_SYMBOL_SOURCE_LINES,
) -> list[str]:
    """Lines ``start_line`` to ``end_line`` of a file (at most ``max_lines``).

    The path comes from the index, and the index is data from disk: a repository can ship its
    own `.nexus` folder. So the file is read only when its real path (symlinks followed) is a
    regular file inside one of ``roots`` and not larger than ``max_file_size_mb``. Returns []
    in every other case, and when the file is gone (deleted after the index was built).
    """
    import itertools

    from nexus_mcp.config import get_settings

    try:
        real = Path(path).resolve(strict=True)
        if not any(real.is_relative_to(Path(root).resolve()) for root in roots):
            return []
        if not real.is_file():
            return []
        if real.stat().st_size > get_settings().max_file_size_mb * 1024 * 1024:
            return []
        first = max(1, int(start_line or 1))
        last = min(max(first, int(end_line or first)), first + max_lines - 1)
        with open(real, encoding="utf-8", errors="replace") as f:
            return list(itertools.islice(f, first - 1, last))
    except (OSError, ValueError, RuntimeError):  # RuntimeError: a symlink loop
        return []


def _symbol_source(
    path: str, start_line: int, end_line: int, limit: int, roots: list[Path]
) -> str:
    """Lines ``start_line`` to ``end_line`` of a file, without the common indent.

    The file is read only inside ``roots`` (see ``_source_lines``), at most
    ``MAX_SYMBOL_SOURCE_LINES`` lines. Returns "" when it cannot be read.
    """
    import textwrap

    lines = _source_lines(path, start_line, end_line, roots)
    if not lines:
        return ""
    return _trim_snippet(textwrap.dedent("".join(lines)).rstrip(), limit)


def _class_methods(state, node: UniversalNode) -> list[UniversalNode]:
    """The methods of a class node, in source order.

    A method is a function node of the same file whose lines are inside the class and whose
    ``parent_class`` is the class (the parser records it). The graph links functions to their
    module, not to their class, so the module node is the way in.
    """
    graph = state.graph_engine
    loc = node.location
    modules = [
        n for n in graph.get_predecessors(node.id)
        if n.node_type == NodeType.MODULE and n.location.file_path == loc.file_path
    ]
    methods: dict[str, UniversalNode] = {}
    for module in modules:
        for n in graph.get_successors(module.id):
            nl = n.location
            if (
                n.node_type == NodeType.FUNCTION
                and nl.file_path == loc.file_path
                and loc.start_line < nl.start_line <= loc.end_line
                and (n.metadata or {}).get("parent_class", node.name) == node.name
            ):
                methods[n.id] = n
    # A function nested in a method has the same parent class; leave it out.
    direct: list[UniversalNode] = []
    for n in sorted(methods.values(), key=lambda n: (n.location.start_line, -n.location.end_line)):
        if direct and n.location.end_line <= direct[-1].location.end_line:
            continue
        direct.append(n)
    return direct


def _is_long_class(node: UniversalNode, code: str) -> bool:
    """True for a class whose code is longer than ``CLASS_OUTLINE_MIN_CHARS``."""
    return node.node_type == NodeType.CLASS and len(code) > CLASS_OUTLINE_MIN_CHARS


def _class_outline(state, node: UniversalNode, roots: list[Path]) -> str:
    """A short view of a long class: its head (decorators, class line, docstring, class
    attributes, up to the first method) and one line for each method with its lines.

    In the django benchmark `find_symbol` sent up to 6,000 characters of a class body, which
    is more than the short line range that the agent would read; most of those answers needed
    only the place, the parent class and the method names. Returns "" when the class has no
    methods in the graph or the file cannot be read; the caller then shows the code.
    """
    import textwrap

    methods = _class_methods(state, node)
    if not methods:
        return ""
    loc = node.location
    lines = _source_lines(
        loc.file_path, loc.start_line, loc.end_line, roots, max_lines=MAX_OUTLINE_SOURCE_LINES
    )
    if not lines:
        return ""
    head_end = methods[0].location.start_line - loc.start_line  # lines before the first method
    head_lines = textwrap.dedent("".join(lines[:head_end])).rstrip().split("\n")
    # In Python the node of a method starts after its decorators, so they end the head.
    while len(head_lines) > 1 and head_lines[-1].lstrip().startswith("@"):
        head_lines.pop()
    head = "\n".join(head_lines[:CLASS_OUTLINE_HEAD_LINES]).rstrip()
    head = _trim_snippet(head, CLASS_OUTLINE_HEAD_CHARS)
    out = [head, f"# {len(methods)} methods:"]
    for m in methods[:MAX_OUTLINE_METHODS]:
        offset = m.location.start_line - loc.start_line
        first = lines[offset].strip() if 0 <= offset < len(lines) else m.name
        if len(first) > 120:
            first = first[:117] + "..."
        out.append(f"  {m.location.start_line}-{m.location.end_line}: {first}")
    if len(methods) > MAX_OUTLINE_METHODS:
        out.append(f"  ... {len(methods) - MAX_OUTLINE_METHODS} more")
    return "\n".join(out)


def _slice_names_symbol(code: str, name: str) -> bool:
    """True when the name of the symbol is in the first lines of its code.

    The line numbers in the index are from the time of indexing. After an edit that moves
    lines, the same numbers point at other code. This check finds that: the code that
    `find_symbol` would show must hold the name of the symbol.
    """
    short = str(name).rsplit(".", 1)[-1]
    if not short:
        return True
    return short in "\n".join(code.split("\n")[:NAME_CHECK_LINES])


def _chunk_lookup_names(name: str) -> list[str]:
    """The names to look up in the chunk table for one `find_symbol` name, best first."""
    if "::" not in name:
        return [name]
    parts = [p for p in name.split("::") if p]
    return [".".join(parts[i:]) for i in range(len(parts))]


def _chunk_symbol_entries(state, name: str) -> list[tuple[dict[str, Any], tuple]]:
    """`find_symbol` entries from the chunk table, for a name with no graph node.

    The graph holds functions, classes and methods of the ten graph languages. A constant, a module
    docstring or a symbol of another language is only in the chunk table.

    A C++ name with `::` is looked up as the index stores it, with `.`
    (`WeakReference::Flag` is `WeakReference.Flag`). The index has no namespaces, so
    when the whole name has no match, the leading parts are dropped one at a time:
    `base::internal::WeakReference` finds `WeakReference`.
    """
    rows: list[dict[str, Any]] = []
    for candidate in _chunk_lookup_names(name):
        try:
            rows = state.vector_engine.find_by_symbol_names([candidate])
        except Exception as e:  # noqa: BLE001 - a second source, never a failure
            logger.debug("Chunk lookup failed for %s: %s", candidate, e)
            return []
        if rows:
            break
    out = []
    for r in rows:
        path = str(r.get("filepath", ""))
        rel = path
        if state.codebase_path:
            try:
                rel = str(Path(path).relative_to(state.codebase_path))
            except ValueError:
                pass
        entry = {
            "name": r.get("symbol_name", name),
            "type": r.get("symbol_type", ""),
            "file": rel,
            "start_line": r.get("line_start"),
            "end_line": r.get("line_end"),
        }
        out.append((entry, (path, r.get("line_start"), r.get("line_end"))))
    return out


def _prefix_repo(entry: dict[str, Any], repo, detail: str) -> None:
    """Put the repo name before each path of a `find_symbol` entry of a linked repo.

    The same rule as in `search`: `backend/src/app.py`. A path that is not relative to the
    repo (the index gave an absolute one) is left as it is. `detail="full"` also names the
    repo in a `repo` field.
    """
    prefix = f"{repo.name}/"

    def joined(path: str) -> str:
        return path if path.startswith("/") else prefix + path

    if isinstance(entry.get("file"), str):
        entry["file"] = joined(entry["file"])
    location = entry.get("location")
    if isinstance(location, dict) and isinstance(location.get("file"), str):
        location["file"] = joined(location["file"])
    for key in ("callers", "callees"):
        if isinstance(entry.get(key), dict):
            entry[key] = {joined(f): names for f, names in entry[key].items()}
    if detail == "full":
        entry["repo"] = repo.name


def find_symbol(symbol_name: str, exact: bool = True, detail: str = "compact") -> dict[str, Any]:
    """Look up one or more symbols by name: place, code and call-graph relationships.

    ``symbol_name`` holds one name, or several names with commas or spaces between them
    (at most ``FIND_SYMBOL_MAX_NAMES``). Each symbol comes with its ``code`` (at most
    ``SYMBOL_CODE_CHARS`` for one symbol, ``SYMBOL_CODE_BUDGET`` for the call, and only
    the first ``MAX_SYMBOL_MATCHES_WITH_CODE`` definitions of one name; none for the
    node of a whole file, and none when the file changed after indexing, with a
    ``warning``). A class longer than ``CLASS_OUTLINE_MIN_CHARS`` gets an ``outline`` (its
    head and one line for each method) in place of ``code``, and the result a ``hint`` to ask
    for ``Class.method``. A name has at most ``MAX_MATCHES_PER_NAME`` entries; ``more_matches``
    counts the rest. A name with no graph node (a constant, for example) is looked up in
    the chunk table.

    ``detail="compact"`` (default) gives file, lines and the callers and callees by name
    (`file -> ["name:line"]`, at most ``MAX_SYMBOL_RELATIONS`` each). ``detail="full"``
    gives every node field and the raw relationship records.

    Linked repos (ADR-027): the graph of each attached linked repo is searched after the
    primary project. A path of a linked repo starts with the repo name. With
    ``detail="full"`` an entry of a linked repo has a ``repo`` field. All matches of a name
    share the ``MAX_MATCHES_PER_NAME`` cap."""
    from nexus_mcp.engines.fusion import is_test_path

    err = validate_symbol_name(symbol_name)
    if err:
        return err
    if detail not in DETAIL_LEVELS:
        return {"error": f"Invalid detail: {detail}. Must be one of {list(DETAIL_LEVELS)}"}

    state, err = require_indexed()
    if err:
        return err

    symbols: list[dict[str, Any]] = []
    not_found: list[str] = []
    more_matches: dict[str, int] = {}
    stale_code = 0
    stale_linked: set[str] = set()
    outlined = False
    budget = SYMBOL_CODE_BUDGET
    seen_places: set[tuple] = set()  # (file, start, end) of each symbol already in the result
    # The primary project first, then each linked repo (ADR-027). Each symbol is read in the
    # scope that holds it: its graph, its chunk table and its root.
    scopes = [state, *_attached_linked(state)]
    for name in _split_symbol_names(symbol_name) or [symbol_name]:
        scope_nodes = [
            (scope, node)
            for scope in scopes
            for node in _resolve_in_scope(state, scope, name, exact)
        ]
        # Functions and classes first, then tests, then the node of a whole file (the graph
        # has one for each file, named like it: `main` is also `main.py`). A C++ or C#
        # constructor has the name of its class, so it comes after the class. The sort is
        # stable: of equal rank the primary project comes first.
        scope_nodes = sorted(
            scope_nodes,
            key=lambda sn: (
                sn[1].node_type == NodeType.MODULE,
                is_test_path(sn[1].location.file_path),
                sn[1].node_type == NodeType.FUNCTION
                and sn[1].metadata.get("parent_class") == sn[1].name,
            ),
        )
        if len(scope_nodes) > MAX_MATCHES_PER_NAME:
            more_matches[name] = len(scope_nodes) - MAX_MATCHES_PER_NAME
            scope_nodes = scope_nodes[:MAX_MATCHES_PER_NAME]
        found: list[tuple[dict[str, Any], tuple]] = []
        node_of: dict[int, UniversalNode] = {}  # id(entry) -> graph node
        scope_of: dict[int, Any] = {}  # id(entry) -> state or linked repo
        for scope, node in scope_nodes:
            if detail == "compact":
                entry = _symbol_compact(node, scope)
            else:
                entry = _serialize_node(node, scope.codebase_path)
                rels_from = scope.graph_engine.get_relationships_from(node.id)
                rels_to = scope.graph_engine.get_relationships_to(node.id)
                entry["relationships_out"] = [_serialize_relationship(r) for r in rels_from]
                entry["relationships_in"] = [_serialize_relationship(r) for r in rels_to]
            if scope is not state:
                _prefix_repo(entry, scope, detail)
            loc = node.location
            found.append((entry, (loc.file_path, loc.start_line, loc.end_line)))
            node_of[id(entry)] = node
            scope_of[id(entry)] = scope
        # Also with exact=False: a method is in the graph by its bare name, so `Class.method`
        # is only in the chunk table, and an exact match is a fuzzy match too.
        if not found:
            for scope in scopes:
                for entry, place in _chunk_symbol_entries(scope, name):
                    if scope is not state:
                        _prefix_repo(entry, scope, detail)
                    found.append((entry, place))
                    scope_of[id(entry)] = scope
        if not found:
            not_found.append(name)
            continue
        # Names of one call can match the same symbol (`Exec, exec` with exact=False): send
        # each symbol once. A repeat cost tokens on every later turn and told the agent nothing.
        found = [(e, p) for e, p in found if p not in seen_places]
        seen_places.update(p for _, p in found)
        for i, (entry, place) in enumerate(found):
            wants_code = (
                i < MAX_SYMBOL_MATCHES_WITH_CODE
                and budget >= MIN_CODE_BUDGET
                and entry.get("type") != "module"  # a whole file: read it with a file read
            )
            if wants_code:
                # Decide "long class" from the source before the budget cut: a class cut
                # to a small leftover budget would look short and send a cut body.
                scope = scope_of.get(id(entry), state)
                roots = _index_roots(scope)
                source = _symbol_source(*place, SYMBOL_CODE_CHARS, roots)
                code = source if budget >= SYMBOL_CODE_CHARS else _trim_snippet(source, budget)
                node = node_of.get(id(entry))
                if code and not _slice_names_symbol(code, entry.get("name", "")):
                    stale_code += 1  # the file changed after it was indexed
                    if scope is not state:
                        stale_linked.add(scope.name)
                elif code and node is not None and _is_long_class(node, source):
                    outline = _class_outline(scope, node, roots)
                    if outline:
                        entry["outline"] = _trim_snippet(outline, budget)
                        budget -= len(entry["outline"])
                        outlined = True
                    else:
                        entry["code"] = code
                        budget -= len(code)
                elif code:
                    entry["code"] = code
                    budget -= len(code)
                if detail == "compact" and ("code" in entry or "outline" in entry):
                    entry.pop("docstring", None)  # the code or the outline starts with it
            symbols.append(entry)

    if not symbols:
        msg = f"Symbol '{symbol_name}' not found."
        if exact:
            msg += " Try exact=False for fuzzy matching."
        return {"error": msg}

    result: dict[str, Any] = {"total": len(symbols), "symbols": symbols}
    if not_found:
        result["not_found"] = not_found
    if more_matches:
        result["more_matches"] = more_matches
    if outlined:
        result["hint"] = (
            "A long class shows an outline, not its body. For the code of a method, call "
            "find_symbol with 'Class.method' (several names in one call)."
        )
    if stale_code:
        result["warning"] = (
            f"A file changed after it was indexed, so the code of {stale_code} symbol(s) is "
            "left out. The index refreshes in the background: call again, or read the file."
        )
        if stale_linked:
            from nexus_mcp import linked

            result["warning"] += (
                f" A linked repo is never reindexed by a call: {', '.join(sorted(stale_linked))}. "
                f"{linked.REFRESH_HINT}"
            )
    return result


def _graph_scopes(state) -> list[tuple[str, Any]]:
    """(repo key, scope) of the primary project and of each attached linked repo.

    The key of the primary project is `service_resolver.PRIMARY`. A scope has a
    `graph_engine` and a `codebase_path`: the session state, or a `LinkedRepo`.
    """
    from nexus_mcp.indexing.service_resolver import PRIMARY

    return [(PRIMARY, state), *((repo.name, repo) for repo in _attached_linked(state))]


def _scope_of(state, key: str):
    for scope_key, scope in _graph_scopes(state):
        if scope_key == key:
            return scope
    return None


def _graph_starts(state, symbol_name: str) -> list[tuple[str, UniversalNode]]:
    """The graph nodes that a `graph` call starts from, as (repo key, node).

    The primary project first, then each linked repo, as for `find_symbol`. With no linked
    repo this is the nodes of the primary graph, as it was before linked repos.
    """
    return [
        (key, node)
        for key, scope in _graph_scopes(state)
        for node in _resolve_in_scope(state, scope, symbol_name, True)
    ]


def _neighbours(state, key: str, node_id: str, direction: str) -> list[tuple[str, Any, Any]]:
    """The nodes next to a node, as (repo key, node, edge).

    First its CALLS neighbours in its own graph (edge None), then the nodes at the other
    end of its HTTP_CALLS edges (ADR-027, phase 2.4): the client nodes for `callers`, the
    route handlers for `callees`. An HTTP edge can end in another repo.
    """
    scope = _scope_of(state, key)
    if scope is None:
        return []
    graph = scope.graph_engine
    related = graph.get_callers(node_id) if direction == "callers" else graph.get_callees(node_id)
    hops: list[tuple[str, Any, Any]] = [(key, n, None) for n in related]
    bridge = getattr(state, "bridge", None)
    if bridge:
        if direction == "callers":
            edges = [(e.client_repo, e.client_id, e) for e in bridge.callers_of(key, node_id)]
        else:
            edges = [(e.route_repo, e.route_id, e) for e in bridge.callees_of(key, node_id)]
        for other_key, other_id, edge in edges:
            other_scope = _scope_of(state, other_key)
            other = other_scope.graph_engine.get_node(other_id) if other_scope else None
            if other is not None:
                hops.append((other_key, other, edge))
    return hops


def _root_of(state, key: str) -> Optional[Path]:
    """The root of a repo of the session, or None when it is not attached any more.

    A parallel `index()` replaces `state.linked`, so a hop that was found a moment ago can
    name a repo that is gone. Its paths are then shown as the graph holds them.
    """
    scope = _scope_of(state, key)
    return scope.codebase_path if scope is not None else None


def _repo_path(key: str, path: str) -> str:
    """A path of a linked repo starts with the repo name, as in `search` (`backend/api.py`)."""
    from nexus_mcp.indexing.service_resolver import PRIMARY

    return path if key == PRIMARY or path.startswith("/") else f"{key}/{path}"


def _compact_hop(state, hop: tuple[str, Any, Any], line: bool = True) -> tuple[str, str]:
    """(file, `name:line`) of a hop, or (file, `name`) with line=False (the transitive list).

    A hop over an HTTP edge ends with `via METHOD /path`.
    """
    key, node, edge = hop
    short = _serialize_node_compact(node, _root_of(state, key))
    label = f"{short['name']}:{short['start_line']}" if line else short["name"]
    if edge is not None:
        label += f" via {edge.rel.metadata['via']}"
    return _repo_path(key, short["file"]), label


def _group_hops(state, hops: list[tuple[str, Any, Any]]) -> dict[str, list[str]]:
    """Hops as `file -> ["name:line", ...]`, in the order given, with no file cut."""
    grouped: dict[str, list[str]] = {}
    for hop in hops:
        file, label = _compact_hop(state, hop)
        grouped.setdefault(file, []).append(label)
    return grouped


def _serialize_hop(state, hop: tuple[str, Any, Any]) -> dict[str, Any]:
    """The `detail="full"` entry of a hop: the node, and the repo and edge when they matter."""
    from nexus_mcp.indexing.service_resolver import PRIMARY

    key, node, edge = hop
    out = _serialize_node(node, _root_of(state, key))
    if key != PRIMARY:
        out["location"]["file"] = _repo_path(key, out["location"]["file"])
        out["repo"] = key
    if edge is not None:
        out["via"] = edge.rel.metadata["via"]
        out["resolution"] = edge.rel.metadata["resolution"]
    return out


def _defined_in_starts(state, starts: list[tuple[str, UniversalNode]]) -> Optional[dict]:
    """`defined_in` for the start nodes of a `graph` call, a linked repo's files with its name."""
    from nexus_mcp.indexing.service_resolver import PRIMARY

    if all(key == PRIMARY for key, _ in starts):
        return _defined_in(state, [n for _, n in starts])
    if len(starts) > MAX_DEFINED_IN:
        return None
    grouped: dict[str, list[str]] = {}
    for key, node in starts:
        file, label = _compact_hop(state, (key, node, None))
        owner = (node.metadata or {}).get("parent_class")
        name = f"{owner}.{label}" if owner else label
        grouped.setdefault(file, []).append(name)
    return grouped


def _graph_immediate(
    state, symbol_name: str, direction: str, detail: str = "compact"
) -> dict[str, Any]:
    from nexus_mcp.indexing.service_resolver import PRIMARY

    starts = _graph_starts(state, symbol_name)
    if not starts:
        return _references_only(state, symbol_name, direction)
    matches = [node for key, node in starts if key == PRIMARY]

    related: list[tuple[str, Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, node in starts:
        for hop in _neighbours(state, key, node.id, direction):
            if (hop[0], hop[1].id) not in seen:
                seen.add((hop[0], hop[1].id))
                related.append(hop)

    result: dict[str, Any] = {"symbol": symbol_name}
    defined_in = _defined_in_starts(state, starts)
    if defined_in:
        result["defined_in"] = defined_in
    omitted = None
    if detail == "compact":
        listed, omitted = _cap_files(_group_hops(state, related))
    else:
        listed = [_serialize_hop(state, hop) for hop in related[:MAX_FULL_NODES]]
    result |= {"direction": direction, "total": len(related), direction: listed}
    if omitted:
        result[f"{direction}_not_listed"] = omitted
    if detail != "compact" and len(related) > MAX_FULL_NODES:
        result["truncated"] = (
            f"The first {MAX_FULL_NODES} of {len(related)} are shown. "
            'detail="compact" lists them by file, with a count of the files past its cap.'
        )
    # Overrides and text references are those of the primary project: a symbol that only a
    # linked repo defines has none of them here.
    if direction == "callers" and matches:
        _add_overrides(result, state, matches)
        _add_references(result, state, symbol_name, matches)
    return result


def _references_only(state, symbol_name: str, direction: str) -> dict[str, Any]:
    """The `graph` answer for a name with no graph node: its text references, or an error.

    A constant, a symbol in a language without graph support, or a name the parser missed
    still has uses in the text. An error here would send the agent to grep.
    """
    not_found = {"error": f"Symbol '{symbol_name}' not found."}
    if direction != "callers":
        return not_found
    result: dict[str, Any] = {
        "symbol": symbol_name, "direction": direction, "total": 0, direction: {},
    }
    _add_references(result, state, symbol_name)
    if not result.get("references", {}).get("total_lines"):
        return not_found
    result["note"] = (
        "No graph node has this name, so there are no call edges. `references` lists "
        "every line where the name appears as a whole word (file -> `line: code`)."
    )
    return result


def _merge_references(found: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    """One `references` result from the results of several roots, paths as `root/path`."""
    merged: dict[str, Any] = {"total_files": 0, "total_lines": 0, "files": {}, "truncated": False}
    for root, refs in found:
        merged["total_files"] += refs.get("total_files", 0)
        merged["total_lines"] += refs.get("total_lines", 0)
        merged["truncated"] = merged["truncated"] or refs.get("truncated", False)
        if refs.get("definitions"):
            merged["definitions"] = merged.get("definitions", 0) + refs["definitions"]
        if refs.get("non_code"):
            nc = merged.setdefault("non_code", {"files": 0, "lines": 0})
            nc["files"] += refs["non_code"]["files"]
            nc["lines"] += refs["non_code"]["lines"]
        for key in ("files", "more_files", "test_files"):
            for path, value in refs.get(key, {}).items():
                merged.setdefault(key, {})[f"{root.name}/{path}"] = value
        for key in ("more_files_not_listed", "test_files_not_listed"):
            if refs.get(key):
                rest = merged.setdefault(key, {"files": 0, "lines": 0})
                rest["files"] += refs[key]["files"]
                rest["lines"] += refs[key]["lines"]
    return merged


def _class_family(state, matches: List[UniversalNode]) -> List[str]:
    """The class of the matched methods and its subclasses, when all matches share a class."""
    owners = {(n.metadata or {}).get("parent_class") for n in matches}
    if len(owners) != 1 or None in owners:
        return []
    owner = owners.pop()
    return [owner] + sorted({c.name for c in _subclasses(state.graph_engine, owner)} - {owner})


def _add_references(
    result: dict[str, Any],
    state,
    symbol_name: str,
    matches: Optional[List[UniversalNode]] = None,
) -> None:
    """Add every whole-word text use of the symbol name to a `graph` callers result.

    Call edges are a lower bound. In the benchmark traces the agent ran its own grep
    after `graph` in 5 of 6 impact tasks to check them; this list makes that unnecessary.

    When all graph ``matches`` are methods of one class, `def` lines of other methods with
    the same name only add to a count, and files that name the class or a subclass come
    first. On Django `SQLCompiler.as_sql` listed `contrib/gis` first and
    the agent then ran 22 turns of grep and reads (issue #31).
    """
    from nexus_mcp.engines.live_grep import LiveGrepEngine

    related = _class_family(state, matches) if matches else []
    roots = _index_roots(state)
    # Each root of a multi-root index gets a share of the line budget, and its paths get
    # the root's folder name in front, so that `src/a.py` of two roots stays apart.
    max_lines = max(MAX_REFERENCE_LINES // max(len(roots), 1), MAX_REFERENCE_LINES_PER_FILE)
    found = []
    for root in roots:
        try:
            refs = LiveGrepEngine(str(root)).references(
                symbol_name,
                max_lines=max_lines if len(roots) > 1 else MAX_REFERENCE_LINES,
                max_lines_per_file=MAX_REFERENCE_LINES_PER_FILE,
                related_names=related,
                # Only for a method of one class whose overrides are listed apart. For a
                # bare name of several classes, or a language without recorded base
                # classes, the `def` lines are the only list of them.
                skip_definitions=bool(related)
                and all(m.language in _LANGUAGES_WITH_BASES for m in matches),
            )
        except Exception as e:  # noqa: BLE001 - references are an addition, never a failure
            logger.warning("Reference search failed: %s", e)
            continue
        if refs is not None:
            found.append((Path(root), refs))
    if not found:
        return
    result["references"] = found[0][1] if len(roots) == 1 else _merge_references(found)
    result["note"] = (
        "references: every whole-word use of the name. Source files show `line: code`; "
        "test files show a line count. No grep is needed. Callers are the statically "
        "resolved calls only."
    )


def _walk_callers(
    state, key: str, node_id: str, max_depth: int
) -> list[tuple[str, Any, Any]]:
    """The callers of a node, level by level, over CALLS edges and HTTP_CALLS edges.

    The same walk as `RustworkxCodeGraph.get_transitive_callers`, but it can leave a graph
    over an HTTP edge (ADR-027, phase 2.4). A hop is (repo key, node, edge).
    """
    visited: set[tuple[str, str]] = set()
    found: list[tuple[str, Any, Any]] = []
    queue = [(key, node_id)]
    depth = 0
    while queue and depth < max_depth:
        next_queue = []
        for current in queue:
            if current in visited:
                continue
            visited.add(current)
            for hop in _neighbours(state, current[0], current[1], "callers"):
                if (hop[0], hop[1].id) not in visited:
                    found.append(hop)
                    next_queue.append((hop[0], hop[1].id))
        queue = next_queue
        depth += 1
    return found


def _graph_transitive_impact(
    state, symbol_name: str, max_depth: int, detail: str = "compact"
) -> dict[str, Any]:
    from nexus_mcp.indexing.service_resolver import PRIMARY

    max_depth = max(1, min(max_depth, 50))

    starts = _graph_starts(state, symbol_name)
    if not starts:
        return _references_only(state, symbol_name, "callers")
    matches = [node for key, node in starts if key == PRIMARY]

    compact = detail == "compact"
    all_impacted = []
    by_file: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for key, node in starts:
        for hop in _walk_callers(state, key, node.id, max_depth):
            if (hop[0], hop[1].id) not in seen:
                seen.add((hop[0], hop[1].id))
                all_impacted.append(hop)
                file, label = _compact_hop(state, hop, line=False)
                by_file.setdefault(file, []).append(label)

    result: dict[str, Any] = {"symbol": symbol_name}
    defined_in = _defined_in_starts(state, starts)
    if defined_in:
        result["defined_in"] = defined_in
    # `impacted_files` already names every symbol, so a compact answer builds no per-symbol list.
    impacted = [] if compact else [_serialize_hop(state, hop) for hop in all_impacted]
    result |= {
        "direction": "callers",
        "transitive": True,
        "max_depth": max_depth,
        "total_impacted": len(all_impacted),
        "impacted_symbols": impacted,
        "impacted_files": by_file,
    }
    # `impacted_files` already names every symbol, so the compact result leaves the
    # per-symbol list out. A 46,000-character result was measured on a widely used symbol.
    if compact:
        del result["impacted_symbols"]
        result["impacted_files"], omitted = _cap_files(by_file)
        if omitted:
            result["impacted_not_listed"] = omitted
    elif len(all_impacted) > MAX_FULL_NODES:
        result["impacted_symbols"] = impacted[:MAX_FULL_NODES]
        result["truncated"] = (
            f"impacted_symbols shows the first {MAX_FULL_NODES} of {len(all_impacted)}. "
            "impacted_files names all of them."
        )
    if matches:
        _add_overrides(result, state, matches)
        _add_references(result, state, symbol_name, matches)
    return result


def graph(
    symbol_name: str,
    direction: str = "callers",
    transitive: bool = False,
    max_depth: int = 10,
    detail: str = "compact",
) -> dict[str, Any]:
    """Trace callers/callees of a symbol, or (transitive=True, direction='callers')
    the full transitive change-impact blast radius.

    ``detail="compact"`` (default) returns name, type, file and lines for each symbol;
    ``detail="full"`` adds docstring, complexity, signature types and the node id."""
    if direction not in ("callers", "callees"):
        return {"error": "direction must be 'callers' or 'callees'."}
    if detail not in DETAIL_LEVELS:
        return {"error": f"Invalid detail: {detail}. Must be one of {list(DETAIL_LEVELS)}"}
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
        return _graph_transitive_impact(state, symbol_name, max_depth, detail)
    return _graph_immediate(state, symbol_name, direction, detail)


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

    from nexus_mcp.engines.fusion import is_test_path

    node = sorted(matches, key=lambda n: is_test_path(n.location.file_path))[0]
    full = verbosity == "full"
    # A briefing is about one symbol. Before 2026-10-08 it also carried every field of
    # each caller and callee, the raw relationship records, raw search rows and the
    # complexity report of the whole project: 73,000 characters for a busy symbol.
    if full:
        symbol_data = _serialize_node(node, state.codebase_path)
        for key, related in (
            ("callers", state.graph_engine.get_callers(node.id)),
            ("callees", state.graph_engine.get_callees(node.id)),
        ):
            symbol_data[key] = [
                _serialize_node(c, state.codebase_path) for c in related[:MAX_FULL_NODES]
            ]
            if len(related) > MAX_FULL_NODES:
                symbol_data[f"{key}_total"] = len(related)
    else:
        symbol_data = _symbol_compact(node, state)
    if verbosity != "summary" and node.node_type != NodeType.MODULE:
        loc = node.location
        code = _symbol_source(
            loc.file_path, loc.start_line, loc.end_line, SYMBOL_CODE_CHARS, _index_roots(state)
        )
        if code and not _slice_names_symbol(code, node.name):
            symbol_data["warning"] = (
                "The file changed after it was indexed, so the code is left out. "
                "The index refreshes in the background: call again, or read the file."
            )
        elif code:
            symbol_data["code"] = code
            if not full:
                symbol_data.pop("docstring", None)  # the code starts with it

    search_results = []
    if state.vector_engine:
        search_text = node.name
        if node.docstring:
            search_text += " " + node.docstring
        try:
            search_results = state.vector_engine.search(search_text, limit=11)
        except Exception as e:
            logger.debug("Vector search failed in explain: %s", e)
    related_code = []
    for r in search_results:
        path = str(r.get("filepath", ""))
        if path == node.location.file_path and r.get("line_start") == node.location.start_line:
            continue  # the symbol itself
        if state.codebase_path:
            try:
                path = str(Path(path).relative_to(state.codebase_path))
            except ValueError:
                pass
        row = {
            "symbol_name": r.get("symbol_name", ""),
            "filepath": path,
            "line_start": r.get("line_start"),
            "score": r.get("score", 0.0),
        }
        if full:
            row["code_snippet"] = _trim_snippet(
                _chunk_code(r.get("text", ""), r.get("signature") or "", r.get("docstring") or ""),
                COMPACT_TAIL_SNIPPET_CHARS * 3,
            )
        related_code.append(row)

    analysis = {}
    if verbosity != "detailed":
        try:
            from nexus_mcp.analysis.code_analyzer import CodeAnalyzer

            analysis = {"quality": CodeAnalyzer(state.graph_engine).calculate_quality_metrics()}
        except Exception as e:
            logger.debug("Code analysis failed in explain: %s", e)

    builder = ResponseBuilder(verbosity)
    return builder.build_explain_response(symbol_data, related_code[:10], analysis)


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
    """Project overview. 'summary' for a quick look, 'architecture' for
    design/dependency structure, 'full' for both."""
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
        from nexus_mcp.indexing.embedding_service import model_dimensions
        from nexus_mcp.memory.memory_store import MemoryStore

        state.memory_store = MemoryStore(
            db_path=str(settings.lancedb_path),
            embedding_service=embedding_svc,
            vector_dims=model_dimensions(settings.embedding_model),
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
