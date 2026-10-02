"""Turn raw call names on function nodes into CALLS edges.

The ast-grep parser stores, on each function node, the callee names found in its
body (`metadata["calls"]`), the enclosing class (`metadata["parent_class"]`) and,
on each module node, the imported module names (`metadata["imports"]`). Names
alone cannot become edges while files are still being parsed, because a callee
can sit in a file that comes later. So this module runs once after the last
batch and rebuilds every CALLS edge from the node metadata. That also keeps an
incremental reindex correct: no edge survives that points at a changed node.

Resolution is static and name-based. It does not see dynamic dispatch, callbacks
or reflection, so treat the result as a lower bound on who calls what.
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

logger = logging.getLogger(__name__)

SELF_RECEIVERS = frozenset({"self", "cls", "this", "super"})
# Method names that built-in types also have. A call like `os.environ.get(...)`
# must not link to an unrelated `get` in the project, so these resolve only when
# the callee is in the same file or in a file the caller imports.
COMMON_METHOD_NAMES = frozenset({
    "get", "set", "add", "append", "extend", "insert", "pop", "remove", "clear",
    "copy", "update", "items", "keys", "values", "join", "split", "strip",
    "format", "replace", "find", "read", "write", "close", "open", "count",
    "index", "sort", "run", "start", "stop", "send", "load", "save", "init",
    "exists", "is_file", "is_dir", "mkdir", "unlink", "glob", "rglob", "iterdir",
    "stat", "lower", "upper", "startswith", "endswith", "encode", "decode", "dumps",
    "loads", "acquire", "release", "submit", "result", "flush", "info", "debug",
    "warning", "error", "exception", "log", "cancel", "wait", "put", "sleep", "delete",
})
# A plain call such as `foo()` in these languages must name something that is
# defined in the same file or imported. A match anywhere else is almost always a
# parameter or local variable that shares a name with an unrelated function.
IMPORT_SCOPED_LANGUAGES = frozenset({"python", "javascript", "typescript"})

TIER_SAME_FILE = "same_file"
TIER_IMPORTED = "imported"
TIER_UNIQUE = "unique"
_STRENGTH = {TIER_SAME_FILE: 1.0, TIER_IMPORTED: 1.0, TIER_UNIQUE: 0.8}


def _is_method(node: UniversalNode) -> bool:
    return bool(node.metadata.get("parent_class"))


def _module_path(file_path: str) -> str:
    """File path without extension and with forward slashes."""
    path = file_path.replace("\\", "/")
    dot = path.rfind(".")
    slash = path.rfind("/")
    return path[:dot] if dot > slash else path


def _import_to_path(import_name: str) -> str:
    """`shop.cache`, `.cache`, `./cart` or `../lib/b` -> `shop/cache`, `cache`, `cart`, `lib/b`."""
    name = import_name.strip()
    if name.startswith("."):
        name = name.lstrip("./")
    name = name.replace("::", "/")
    if "/" not in name:
        name = name.replace(".", "/")
    return name.strip("/")


def _imports_file(imports: List[str], file_path: str) -> bool:
    """True when any imported module name points at this file."""
    target = _module_path(file_path)
    for imp in imports:
        wanted = _import_to_path(imp)
        if wanted and (target == wanted or target.endswith("/" + wanted)):
            return True
    return False


def _split_call(text: str) -> Tuple[Optional[str], str]:
    """`self.db.execute` -> (`self`, `execute`); `foo` -> (None, `foo`)."""
    parts = text.split(".")
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[-1]


class _Index:
    """Lookup tables built once per resolution pass."""

    def __init__(self, graph: RustworkxCodeGraph):
        self.by_name: Dict[str, List[UniversalNode]] = defaultdict(list)
        self.class_names: Set[str] = set()
        self.module_imports: Dict[str, List[str]] = {}
        self.module_stems: Set[str] = set()
        for node in graph.nodes.values():
            if node.node_type in (NodeType.FUNCTION, NodeType.CLASS):
                self.by_name[node.name].append(node)
                if node.node_type == NodeType.CLASS:
                    self.class_names.add(node.name)
            elif node.node_type == NodeType.MODULE:
                imports = node.metadata.get("imports")
                if imports:
                    self.module_imports[node.location.file_path] = list(imports)
                self.module_stems.add(node.name)


def _resolve_one(
    text: str, caller: UniversalNode, index: _Index
) -> Tuple[List[UniversalNode], str]:
    """Candidates for one call expression and the tier that produced them."""
    receiver, name = _split_call(text)
    candidates = [c for c in index.by_name.get(name, ()) if c.id != caller.id]
    if not candidates:
        return [], ""

    caller_file = caller.location.file_path
    imports = index.module_imports.get(caller_file, [])

    if receiver is None:
        # Plain call: a free function, or a class constructor.
        candidates = [
            c for c in candidates if c.node_type == NodeType.CLASS or not _is_method(c)
        ]
    elif receiver in SELF_RECEIVERS and text.count(".") == 1:
        # `self.helper()`: a method of the caller's own class. A longer chain such
        # as `self.db.execute()` calls into another object and takes the general path.
        own_class = caller.metadata.get("parent_class")
        methods = [
            c for c in candidates
            if _is_method(c) and c.location.file_path == caller_file
            and (not own_class or c.metadata.get("parent_class") == own_class)
        ]
        return (methods, TIER_SAME_FILE) if methods else ([], "")
    elif receiver in index.class_names:
        methods = [c for c in candidates if c.metadata.get("parent_class") == receiver]
        if methods:
            same_file = methods[0].location.file_path == caller_file
            return methods, TIER_SAME_FILE if same_file else TIER_IMPORTED
        candidates = [c for c in candidates if _is_method(c)]
    elif receiver in index.module_stems:
        # `pricing.calculate_total(...)` after `import shop.pricing as pricing`
        in_module = [
            c for c in candidates
            if not _is_method(c) and _module_path(c.location.file_path).endswith("/" + receiver)
        ]
        if in_module:
            return in_module, TIER_IMPORTED
        candidates = [c for c in candidates if _is_method(c)]
    else:
        candidates = [c for c in candidates if _is_method(c)]

    same_file = [c for c in candidates if c.location.file_path == caller_file]
    if same_file:
        return same_file, TIER_SAME_FILE
    imported = [c for c in candidates if _imports_file(imports, c.location.file_path)]
    if imported:
        return imported, TIER_IMPORTED
    if receiver is None and caller.language in IMPORT_SCOPED_LANGUAGES:
        return [], ""
    if name in COMMON_METHOD_NAMES:
        return [], ""
    # Several candidates with no tie-breaker: skip the edge. A missing edge is
    # safer than a wrong one, because graph() is documented as a lower bound.
    if len(candidates) == 1:
        return candidates, TIER_UNIQUE
    return [], ""


def resolve_calls(graph: RustworkxCodeGraph) -> int:
    """Rebuild all CALLS edges from node metadata. Returns the number of edges.

    Safe to call after any index or reindex: it first removes every existing
    CALLS edge, then adds one edge for each (caller, callee) pair it can resolve.
    """
    with graph._lock:
        graph.remove_relationships_by_type(RelationshipType.CALLS)
        index = _Index(graph)
        seen: Set[Tuple[str, str]] = set()
        added = 0
        callers = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION and n.metadata.get("calls")
        ]
        for caller in callers:
            for text in caller.metadata["calls"]:
                targets, tier = _resolve_one(text, caller, index)
                for target in targets:
                    pair = (caller.id, target.id)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    rel = UniversalRelationship(
                        id=f"call:{caller.id}->{target.id}",
                        source_id=caller.id,
                        target_id=target.id,
                        relationship_type=RelationshipType.CALLS,
                        metadata={"resolution": tier, "call": text},
                        strength=_STRENGTH[tier],
                    )
                    if graph.add_relationship(rel) is not None:
                        added += 1
    logger.info("Resolved %d call edges from %d callers", added, len(callers))
    return added
