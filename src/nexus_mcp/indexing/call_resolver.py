"""Turn raw call names on function nodes into CALLS edges.

The ast-grep parser stores, on each function node, the callee names found in its
body (`metadata["calls"]`) and the enclosing class (`metadata["parent_class"]`),
and on each module node the imported module names (`metadata["imports"]`). Names
alone cannot become edges while files are still being parsed, because a callee
can sit in a file that comes later. So this module runs once after the last
batch and rebuilds every CALLS edge from the node metadata. That also keeps an
incremental reindex correct: no edge survives that points at a changed node.

The rule is precision first. A call gets an edge only when exactly one callee
fits at the first tier that has any candidate. Several fitting callees mean no
edge, because `graph(transitive=True)` is documented as a lower bound and a wrong
edge costs more than a missing one. Resolution is static and name-based: dynamic
dispatch, callbacks and reflection are invisible.

All lookups go through dictionaries built once per pass, so the pass is linear in
the number of calls and the graph lock is held for seconds, not minutes.
"""

import logging
import posixpath
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

SELF_RECEIVERS = frozenset({"self", "cls", "this", "Self"})
SUPER_RECEIVER = "super"
MAX_SUPER_CANDIDATES = 20
# Method names that built-in types also have. A call like `self._data.get(key)`
# or `os.environ.get(...)` has an unknown receiver type, so a project method with
# the same name must not receive the edge.
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
UNKNOWN_RECEIVER_FACTOR = 0.875  # weaker evidence: the receiver's type is unknown

Resolution = Tuple[List[UniversalNode], str]
NONE: Resolution = ([], "")


def _lang_group(language: str) -> str:
    return "js" if language in ("javascript", "typescript") else language


def _is_method(node: UniversalNode) -> bool:
    return bool(node.metadata.get("parent_class"))


def _module_path(file_path: str) -> str:
    """File path without extension and with forward slashes."""
    path = file_path.replace("\\", "/")
    dot = path.rfind(".")
    slash = path.rfind("/")
    return path[:dot] if dot > slash else path


def _stem(module_path: str) -> str:
    return module_path.rsplit("/", 1)[-1]


def _import_to_path(import_name: str) -> str:
    """`shop.cache` -> `shop/cache`; `crate::util` -> `crate/util`; `lib/b` stays."""
    name = import_name.strip().replace("::", "/")
    if "/" not in name:
        name = name.replace(".", "/")
    return name.strip("/")


class _Index:
    """Lookup tables built once per resolution pass."""

    def __init__(self, graph: RustworkxCodeGraph):
        self.by_name: Dict[str, List[UniversalNode]] = defaultdict(list)
        self.by_name_file: Dict[Tuple[str, str], List[UniversalNode]] = defaultdict(list)
        self.by_class_name: Dict[Tuple[str, str], List[UniversalNode]] = defaultdict(list)
        self.class_names: Set[str] = set()
        self.module_imports: Dict[str, List[str]] = {}
        self.file_by_module_path: Dict[str, str] = {}
        self.module_files_by_stem: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        self._imported_files: Dict[str, Set[str]] = {}
        self._imported_stems: Dict[str, Dict[str, Set[str]]] = {}
        for node in graph.nodes.values():
            if node.node_type in (NodeType.FUNCTION, NodeType.CLASS):
                self.by_name[node.name].append(node)
                self.by_name_file[(node.name, node.location.file_path)].append(node)
                parent = node.metadata.get("parent_class")
                if parent:
                    self.by_class_name[(parent, node.name)].append(node)
                if node.node_type == NodeType.CLASS:
                    self.class_names.add(node.name)
            elif node.node_type == NodeType.MODULE:
                path = node.location.file_path
                imports = node.metadata.get("imports")
                if imports:
                    self.module_imports[path] = list(imports)
                module_path = _module_path(path)
                self.file_by_module_path[module_path] = path
                self.module_files_by_stem[_stem(module_path)].append((path, module_path))

    def imported_files(self, caller_file: str) -> Set[str]:
        """Files that the caller's file imports, resolved once per caller file."""
        cached = self._imported_files.get(caller_file)
        if cached is not None:
            return cached
        found: Set[str] = set()
        caller_dir = posixpath.dirname(caller_file.replace("\\", "/"))
        for imp in self.module_imports.get(caller_file, ()):
            found.update(self._files_for_import(imp, caller_dir))
        self._imported_files[caller_file] = found
        return found

    def imported_by_stem(self, caller_file: str) -> Dict[str, Set[str]]:
        """Imported files of the caller's file, keyed by module name (`pricing`)."""
        cached = self._imported_stems.get(caller_file)
        if cached is None:
            cached = defaultdict(set)
            for path in self.imported_files(caller_file):
                cached[_stem(_module_path(path))].add(path)
            self._imported_stems[caller_file] = cached
        return cached

    def _files_for_import(self, imp: str, caller_dir: str) -> List[str]:
        imp = imp.strip()
        if not imp:
            return []
        if imp.startswith("."):
            # `./components` can name the package file `components/index.ts` or
            # `components/__init__.py`, so look up each form directly.
            files = []
            for target in self._relative_targets(imp, caller_dir):
                for candidate in (target, target + "/index", target + "/__init__"):
                    path = self.file_by_module_path.get(candidate)
                    if path:
                        files.append(path)
                        break
            return files
        wanted = _import_to_path(imp)
        if not wanted:
            return []
        suffix = "/" + wanted
        return [
            path for path, module_path in self.module_files_by_stem.get(_stem(wanted), ())
            if module_path.endswith(suffix) or module_path == wanted
        ]

    @staticmethod
    def _relative_targets(imp: str, caller_dir: str) -> List[str]:
        """Module paths a relative import can mean, from the caller's directory.

        Python: `.x` is the caller's package, `..x` its parent. JS/TS: `./x`, `../x`.
        """
        if imp.startswith("./") or imp.startswith("../"):
            return [posixpath.normpath(posixpath.join(caller_dir, imp))]
        dots = len(imp) - len(imp.lstrip("."))
        rest = imp.lstrip(".").replace(".", "/")
        base = caller_dir
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        return [posixpath.join(base, rest)] if rest else []


def _split_call(text: str) -> Tuple[Optional[str], str]:
    """`self.db.execute` -> (`self`, `execute`); `foo` -> (None, `foo`)."""
    parts = text.split(".")
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[-1]


def _single(
    candidates: List[UniversalNode], caller: UniversalNode, tier: str
) -> Resolution:
    """Accept a tier only when exactly one callee of the caller's language fits."""
    group = _lang_group(caller.language)
    fit = [c for c in candidates if c.id != caller.id and _lang_group(c.language) == group]
    return (fit, tier) if len(fit) == 1 else NONE


def _in_files(
    index: _Index, name: str, files: Set[str], *, methods: Optional[bool]
) -> List[UniversalNode]:
    """Callables with this name in the given files. methods: True, False, or None for any."""
    out: List[UniversalNode] = []
    for path in files:
        for node in index.by_name_file.get((name, path), ()):
            if methods is None or _is_method(node) == methods or (
                methods is False and node.node_type == NodeType.CLASS
            ):
                out.append(node)
    return out


def _resolve_plain(name: str, caller: UniversalNode, index: _Index) -> Resolution:
    """`foo()`: a free function or a class constructor."""
    caller_file = caller.location.file_path
    own_class = caller.metadata.get("parent_class")

    def keep(node: UniversalNode) -> bool:
        if node.node_type == NodeType.CLASS or not _is_method(node):
            return True
        # Java calls its own methods without a receiver (`helper()`).
        return caller.language == "java" and node.metadata.get("parent_class") == own_class

    same = [n for n in index.by_name_file.get((name, caller_file), ()) if keep(n)]
    if same:
        return _single(same, caller, TIER_SAME_FILE)
    imported = [
        n for f in index.imported_files(caller_file)
        for n in index.by_name_file.get((name, f), ()) if keep(n)
    ]
    if imported:
        return _single(imported, caller, TIER_IMPORTED)
    if caller.language in IMPORT_SCOPED_LANGUAGES:
        return NONE
    global_free = [n for n in index.by_name.get(name, ()) if keep(n)]
    return _single(global_free, caller, TIER_UNIQUE)


def _resolve_self(name: str, caller: UniversalNode, index: _Index) -> Resolution:
    """`self.helper()`: a method of the caller's own class."""
    own_class = caller.metadata.get("parent_class")
    caller_file = caller.location.file_path
    if own_class:
        methods = [
            n for n in index.by_class_name.get((own_class, name), ())
            if n.location.file_path == caller_file
        ]
    else:
        methods = [n for n in index.by_name_file.get((name, caller_file), ()) if _is_method(n)]
    return _single(methods, caller, TIER_SAME_FILE)


def _resolve_super(name: str, caller: UniversalNode, index: _Index) -> Resolution:
    """`super().save()`: a method of some other class, never the caller's own."""
    own_class = caller.metadata.get("parent_class")
    pool = index.by_name.get(name, ())
    if len(pool) > MAX_SUPER_CANDIDATES:
        return NONE
    others = [n for n in pool if _is_method(n) and n.metadata.get("parent_class") != own_class]
    caller_file = caller.location.file_path
    same = [n for n in others if n.location.file_path == caller_file]
    if same:
        return _single(same, caller, TIER_SAME_FILE)
    imported_files = index.imported_files(caller_file)
    imported = [n for n in others if n.location.file_path in imported_files]
    if imported:
        return _single(imported, caller, TIER_IMPORTED)
    return NONE


def _resolve_class_qualified(
    receiver: str, name: str, caller: UniversalNode, index: _Index
) -> Resolution:
    """`Gateway().charge()` or `Gateway.charge()`: a method of that class."""
    methods = index.by_class_name.get((receiver, name), [])
    caller_file = caller.location.file_path
    if len(methods) > 1:
        same = [n for n in methods if n.location.file_path == caller_file]
        if same:
            methods = same
        else:
            imported_files = index.imported_files(caller_file)
            methods = [n for n in methods if n.location.file_path in imported_files]
    resolved, _tier = _single(methods, caller, TIER_IMPORTED)
    if not resolved:
        return NONE
    same_file = resolved[0].location.file_path == caller_file
    return resolved, TIER_SAME_FILE if same_file else TIER_IMPORTED


def _resolve_module_qualified(
    receiver: str, name: str, caller: UniversalNode, index: _Index
) -> Optional[Resolution]:
    """`pricing.total()` after `import shop.pricing`: a function in that module.

    Returns None when the receiver is not an imported module, so the caller can
    try the general method path.
    """
    in_module = index.imported_by_stem(caller.location.file_path).get(receiver)
    if not in_module:
        return None
    return _single(_in_files(index, name, in_module, methods=False), caller, TIER_IMPORTED)


def _resolve_method_unknown_receiver(
    name: str, caller: UniversalNode, index: _Index
) -> Resolution:
    """`obj.method()` where obj's type is unknown: weakest evidence, methods only."""
    if name in COMMON_METHOD_NAMES:
        return NONE
    caller_file = caller.location.file_path
    same = _in_files(index, name, {caller_file}, methods=True)
    if same:
        return _single(same, caller, TIER_SAME_FILE)
    imported = _in_files(index, name, index.imported_files(caller_file), methods=True)
    if imported:
        return _single(imported, caller, TIER_IMPORTED)
    pool = index.by_name.get(name, ())
    only_method = [n for n in pool if _is_method(n)] if len(pool) == 1 else []
    return _single(only_method, caller, TIER_UNIQUE)


def _resolve_one(text: str, caller: UniversalNode, index: _Index) -> Tuple[Resolution, bool]:
    """Resolve one call expression. Returns (resolution, receiver_type_unknown)."""
    receiver, name = _split_call(text)
    if name.startswith("__") and name.endswith("__"):
        return NONE, False  # dunder methods: constructors, operators, super().__init__
    if receiver is None:
        return _resolve_plain(name, caller, index), False
    if text.count(".") == 1:
        if receiver in SELF_RECEIVERS:
            return _resolve_self(name, caller, index), False
        if receiver == SUPER_RECEIVER:
            return _resolve_super(name, caller, index), False
        if receiver in index.class_names:
            result = _resolve_class_qualified(receiver, name, caller, index)
            if result[0]:
                return result, False
        module_result = _resolve_module_qualified(receiver, name, caller, index)
        if module_result is not None:
            return module_result, False
    return _resolve_method_unknown_receiver(name, caller, index), True


def resolve_calls(graph: RustworkxCodeGraph) -> int:
    """Rebuild all CALLS edges from node metadata. Returns the number of edges.

    Safe to call after any index or reindex: it first removes every existing
    CALLS edge, then adds one edge for each (caller, callee) pair it can resolve.
    """
    with graph._lock:
        graph.remove_relationships_by_type(RelationshipType.CALLS)
        index = _Index(graph)
        added = 0
        callers = [
            n for n in graph.nodes.values()
            if n.node_type == NodeType.FUNCTION and n.metadata.get("calls")
        ]
        for caller in callers:
            linked: Set[str] = set()
            for text in caller.metadata["calls"]:
                (targets, tier), unknown_receiver = _resolve_one(text, caller, index)
                for target in targets:
                    if target.id in linked:
                        continue
                    linked.add(target.id)
                    strength = _STRENGTH[tier]
                    if unknown_receiver:
                        strength *= UNKNOWN_RECEIVER_FACTOR
                    rel = UniversalRelationship(
                        id=f"call:{caller.id}->{target.id}",
                        source_id=caller.id,
                        target_id=target.id,
                        relationship_type=RelationshipType.CALLS,
                        metadata={"resolution": tier, "call": text},
                        strength=strength,
                    )
                    if graph.add_relationship(rel) is not None:
                        added += 1
    logger.info("Resolved %d call edges from %d callers", added, len(callers))
    return added
