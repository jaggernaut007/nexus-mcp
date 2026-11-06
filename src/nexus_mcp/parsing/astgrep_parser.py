"""AST-grep based structural parser for code graph building.

Ported from code-graph-mcp's universal_parser.py. Uses ast-grep for
structural analysis across 25+ languages.
"""

import logging
import posixpath
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from ast_grep_py import SgRoot  # type: ignore[import-untyped]
except ImportError:
    SgRoot = None

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalGraph,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.parsing.language_registry import (
    ASTGREP_LANGUAGES,
    get_language_for_file,
)
from nexus_mcp.parsing.treesitter_parser import (
    c_declarator_name,
    is_cpp_header,
    mask_cpp_class_macros,
    split_cpp_scope,
)

logger = logging.getLogger(__name__)

# A callee field that is not one field: the first named child holds the callee
# (`new User()` in PHP has no field for `User`).
FIRST_NAMED_CHILD = "*"

# Call expressions per language: (node kind, field that holds the callee). A pair of
# fields is (receiver, name), joined with a dot: Java `obj.m()`, PHP `$obj->m()`.
# None is ("object", "name").
CALL_KINDS: Dict[str, tuple] = {
    "python": (("call", "function"),),
    "javascript": (("call_expression", "function"), ("new_expression", "constructor")),
    "typescript": (("call_expression", "function"), ("new_expression", "constructor")),
    "go": (("call_expression", "function"),),
    "java": (("method_invocation", None), ("object_creation_expression", "type")),
    "rust": (("call_expression", "function"),),
    "c": (("call_expression", "function"),),
    "cpp": (("call_expression", "function"), ("new_expression", "type")),
    "csharp": (
        ("invocation_expression", "function"), ("object_creation_expression", "type"),
    ),
    "php": (
        ("function_call_expression", "function"),
        ("member_call_expression", ("object", "name")),
        ("nullsafe_member_call_expression", ("object", "name")),
        ("scoped_call_expression", ("scope", "name")),
        ("object_creation_expression", FIRST_NAMED_CHILD),
    ),
}
# Receivers that mean the own class or its base class, per language.
RECEIVER_ALIASES: Dict[str, Dict[str, str]] = {
    "php": {"static": "self", "parent": "super"},
    "csharp": {"base": "super"},
}

# Function and class definitions per language, by node kind.
FUNCTION_KINDS: Dict[str, tuple] = {
    "python": ("function_definition",),
    "javascript": ("function_declaration", "method_definition"),
    "typescript": ("function_declaration", "method_definition"),
    "go": ("function_declaration", "method_declaration"),
    "java": ("method_declaration",),
    "rust": ("function_item",),
    "c": ("function_definition",),
    "cpp": ("function_definition",),
    "csharp": ("method_declaration", "constructor_declaration", "local_function_statement"),
    "php": ("function_definition", "method_declaration"),
}
CLASS_KINDS: Dict[str, tuple] = {
    "python": ("class_definition",),
    "javascript": ("class_declaration",),
    "typescript": ("class_declaration",),
    "java": ("class_declaration",),
    "rust": ("struct_item",),
    "c": ("struct_specifier", "union_specifier"),
    "cpp": ("class_specifier", "struct_specifier", "union_specifier"),
    "csharp": (
        "class_declaration", "struct_declaration", "interface_declaration",
        "record_declaration",
    ),
    "php": (
        "class_declaration", "interface_declaration", "trait_declaration",
        "enum_declaration",
    ),
}
_C_FAMILY = ("c", "cpp")

# Enclosing type kinds per language: (node kind, field that holds the name).
CLASS_ANCESTOR_KINDS: Dict[str, tuple] = {
    "python": (("class_definition", "name"),),
    "javascript": (("class_declaration", "name"),),
    "typescript": (("class_declaration", "name"),),
    "java": (
        ("class_declaration", "name"),
        ("interface_declaration", "name"),
        ("enum_declaration", "name"),
    ),
    "rust": (("impl_item", "type"),),
    "cpp": (
        ("class_specifier", "name"),
        ("struct_specifier", "name"),
        ("union_specifier", "name"),
    ),
    "csharp": (
        ("class_declaration", "name"),
        ("struct_declaration", "name"),
        ("interface_declaration", "name"),
        ("record_declaration", "name"),
    ),
    "php": (
        ("class_declaration", "name"),
        ("interface_declaration", "name"),
        ("trait_declaration", "name"),
        ("enum_declaration", "name"),
    ),
}

# Branch points that add one to cyclomatic complexity, per language. `&&`/`||` are
# counted only for Python (`boolean_operator`); the other grammars give them no
# distinct node kind, so those branches are not counted.
COMPLEXITY_KINDS: Dict[str, tuple] = {
    "python": (
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "conditional_expression", "boolean_operator", "for_in_clause", "if_clause",
        "case_clause",
    ),
    "javascript": (
        "if_statement", "for_statement", "for_in_statement", "while_statement",
        "do_statement", "switch_case", "catch_clause", "ternary_expression",
    ),
    "typescript": (
        "if_statement", "for_statement", "for_in_statement", "while_statement",
        "do_statement", "switch_case", "catch_clause", "ternary_expression",
    ),
    "go": (
        "if_statement", "for_statement", "expression_case", "type_case",
        "communication_case",
    ),
    "java": (
        "if_statement", "for_statement", "enhanced_for_statement", "while_statement",
        "do_statement", "switch_label", "catch_clause", "ternary_expression",
    ),
    "rust": (
        "if_expression", "for_expression", "while_expression", "loop_expression",
        "match_arm",
    ),
    "c": (
        "if_statement", "for_statement", "while_statement", "do_statement",
        "case_statement", "conditional_expression",
    ),
    "cpp": (
        "if_statement", "for_statement", "for_range_loop", "while_statement",
        "do_statement", "case_statement", "catch_clause", "conditional_expression",
    ),
    "csharp": (
        "if_statement", "for_statement", "foreach_statement", "while_statement",
        "do_statement", "switch_section", "catch_clause", "conditional_expression",
    ),
    "php": (
        "if_statement", "else_if_clause", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "case_statement", "catch_clause",
        "conditional_expression",
    ),
}

MAX_DOCSTRING_CHARS = 500
MAX_CALLS_PER_FUNCTION = 200
MAX_CALL_TEXT = 120
_PAREN_GROUP = re.compile(r"\([^()]*\)")
_GENERIC_GROUP = re.compile(r"<[^<>]*>")
_DOTTED_NAME = re.compile(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*")


def normalize_call_text(text: str) -> str:
    """Reduce a callee expression to dotted names.

    `PaymentGateway().charge` -> `PaymentGateway.charge`; `self.db.execute`
    stays as is; `a?.b` and `a::b` become `a.b`. Returns "" when nothing usable
    remains (for example a call on a subscript or a lambda).
    """
    text = " ".join(text.split())
    for _ in range(4):  # nested argument lists, for example a(b(c)).d
        reduced = _PAREN_GROUP.sub("", text)
        if reduced == text:
            break
        text = reduced
    text = _GENERIC_GROUP.sub("", text).replace("?.", ".").replace("::", ".")
    text = text.replace("->", ".")
    if len(text) > MAX_CALL_TEXT or not _DOTTED_NAME.fullmatch(text):
        return ""
    return text


class _SgView:
    """The tree-sitter node API over an ast-grep node, for `c_declarator_name`."""

    __slots__ = ("node",)

    def __init__(self, node):
        self.node = node

    @property
    def type(self) -> str:
        return self.node.kind()

    def child_by_field_name(self, name: str) -> Optional["_SgView"]:
        child = self.node.field(name)
        return _SgView(child) if child else None

    @property
    def named_children(self) -> List["_SgView"]:
        return [_SgView(c) for c in self.node.children() if c.is_named()]


def _sg_text(view: "_SgView") -> str:
    return view.node.text()


def _language_callee(text: str, language: str) -> str:
    """Callee text in the common form before normalize_call_text().

    PHP: `$this->m` is `this.m`, `\\App\\fmt` is `fmt`, `static::m` is `self.m`,
    `parent::m` is `super.m`. C#: `base.M` is `super.M`. C++: `::g` is `g`.
    """
    if language == "php":
        text = text.replace("$", "")
        if "(" not in text:  # a namespace prefix, not a backslash in an argument
            text = text.rsplit("\\", 1)[-1]
    elif language == "cpp":
        text = text.lstrip(":").strip()
    aliases = RECEIVER_ALIASES.get(language)
    if aliases:
        head, sep, rest = text.partition(".")
        if sep and head in aliases:
            text = f"{aliases[head]}.{rest}"
    return text


@dataclass(frozen=True)
class AstGrepLanguageConfig:
    """Configuration for ast-grep language parsing."""

    name: str
    extensions: tuple
    ast_grep_id: str
    function_patterns: tuple
    class_patterns: tuple
    variable_patterns: tuple
    import_patterns: tuple


# ast-grep language ID mapping
ASTGREP_LANG_IDS: Dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "c": "c",
    "cpp": "cpp",
    "go": "go",
    "java": "java",
    "kotlin": "kotlin",
    "rust": "rust",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "csharp": "csharp",  # ast-grep has no "c_sharp"; that id panics past `except Exception`
    "scala": "scala",
    "lua": "lua",
    # No "dart": ast-grep-py ships no Dart grammar, and an unknown id panics past
    # `except Exception`. parse_file() returns 0 for a language with no id.
}


class AstGrepParser:
    """Structural code parser using ast-grep for graph building."""

    def __init__(self):
        if SgRoot is None:
            raise ImportError(
                "ast-grep-py is required. Install with: pip install ast-grep-py"
            )
        self._node_counter = 0
        self._rel_counter = 0
        # Node ids must be unique across processes: a graph restored from disk holds
        # ids from an earlier parser, and a new parser that restarted its counter would
        # reuse them, so add_node() would silently drop the new nodes.
        self._id_token = uuid.uuid4().hex[:8]

    def can_parse(self, filepath: str) -> bool:
        """True when ast-grep has structure rules for the language of this file."""
        lang = get_language_for_file(filepath)
        return lang is not None and lang in ASTGREP_LANGUAGES

    def parse_file(self, filepath: str, graph: UniversalGraph) -> int:
        """Parse a file and add nodes/relationships to the graph.

        Returns the number of nodes added.
        """
        file_path = Path(filepath)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        language = get_language_for_file(filepath)
        if language is None or language not in ASTGREP_LANGUAGES:
            return 0

        ast_grep_id = ASTGREP_LANG_IDS.get(language)
        if not ast_grep_id:
            return 0

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return 0

        if language in _C_FAMILY:
            raw = content.encode("utf-8")
            if language == "c" and is_cpp_header(filepath, raw):
                language = ast_grep_id = "cpp"
            if language == "cpp":
                # Same mask as the tree-sitter parser: `class BASE_EXPORT Foo {` is a class.
                content = mask_cpp_class_macros(raw).decode("utf-8", errors="replace")

        # Create module node
        module_id = self._make_id("module")
        lines = content.count("\n") + 1
        module_node = UniversalNode(
            id=module_id,
            name=file_path.stem,
            node_type=NodeType.MODULE,
            location=UniversalLocation(
                file_path=str(filepath),
                start_line=1,
                end_line=lines,
                language=language,
            ),
            language=language,
            line_count=lines,
        )
        graph.add_node(module_node)
        nodes_added = 1

        try:
            root = SgRoot(content, ast_grep_id)
            sg_node = root.root()
        except Exception as e:
            logger.warning("ast-grep parse failed for %s: %s", filepath, e)
            return nodes_added

        # Extract functions
        nodes_added += self._extract_functions(
            sg_node, filepath, language, module_id, graph
        )
        # Extract classes
        nodes_added += self._extract_classes(
            sg_node, filepath, language, module_id, graph
        )
        # Extract imports
        self._extract_imports(sg_node, filepath, language, module_id, graph)

        return nodes_added

    def parse_directory(
        self, directory: Path, graph: UniversalGraph, recursive: bool = True
    ) -> int:
        """Parse all supported files in a directory."""
        files_parsed = 0
        pattern = "**/*" if recursive else "*"

        for file_path in directory.glob(pattern):
            if not file_path.is_file():
                continue
            if not self.can_parse(str(file_path)):
                continue
            # Skip hidden/temp files
            if any(part.startswith(".") for part in file_path.parts):
                continue
            try:
                self.parse_file(str(file_path), graph)
                files_parsed += 1
            except Exception as e:
                logger.warning("Failed to parse %s: %s", file_path, e)

        return files_parsed

    def _make_id(self, prefix: str) -> str:
        self._node_counter += 1
        return f"{prefix}:{self._id_token}-{self._node_counter}"

    def _make_rel_id(self) -> str:
        self._rel_counter += 1
        return f"rel:{self._id_token}-{self._rel_counter}"

    def _extract_functions(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph,
    ) -> int:
        """Extract function definitions from ast-grep node.

        Matches by tree-sitter node *kind* rather than a source pattern: a
        pattern like "def $NAME($$$PARAMS): $$$BODY" silently fails to match
        any function carrying a return annotation (`def f() -> int:`), which
        is most of a typed codebase. Kind matching is annotation-agnostic.
        """
        count = 0
        lang_kinds = FUNCTION_KINDS.get(language)
        if not lang_kinds:
            return 0

        matches = []
        for kind in lang_kinds:
            try:
                matches.extend(sg_node.find_all(kind=kind))
            except Exception as e:
                logger.debug("ast-grep find_all failed for kind %s: %s", kind, e)

        for match in matches:
            try:
                rng = match.range()
                scope: List[str] = []
                if language in _C_FAMILY:
                    resolved = c_declarator_name(_SgView(match), _sg_text)
                    if resolved is None:
                        continue  # a name from the return type is worse than none
                    scope, func_name = resolved
                else:
                    name_text = match.field("name")
                    func_name = (
                        name_text.text() if name_text else f"anonymous_{self._node_counter}"
                    )

                func_id = self._make_id("func")
                func_node = UniversalNode(
                    id=func_id,
                    name=func_name,
                    node_type=NodeType.FUNCTION,
                    location=UniversalLocation(
                        file_path=filepath,
                        start_line=rng.start.line + 1,
                        end_line=rng.end.line + 1,
                        start_column=rng.start.column,
                        end_column=rng.end.column,
                        language=language,
                    ),
                    language=language,
                    line_count=rng.end.line - rng.start.line + 1,
                    complexity=self._complexity(match, language),
                    docstring=self._docstring(match, language),
                    metadata=self._function_metadata(match, language, scope),
                )
                graph.add_node(func_node)

                # Add CONTAINS relationship from module
                graph.add_relationship(UniversalRelationship(
                    id=self._make_rel_id(),
                    source_id=module_id,
                    target_id=func_id,
                    relationship_type=RelationshipType.CONTAINS,
                ))
                count += 1
            except Exception as e:
                logger.debug("Failed to extract function at %s: %s", filepath, e)
                continue

        return count

    @staticmethod
    def _complexity(match, language: str) -> int:
        """Cyclomatic complexity: one plus the number of branch points in the body."""
        kinds = COMPLEXITY_KINDS.get(language)
        if not kinds:
            return 0
        try:
            found = match.find_all({"rule": {"any": [{"kind": k} for k in kinds]}})
        except Exception as e:
            logger.debug("Complexity search failed: %s", e)
            return 0
        return 1 + len(found)

    @staticmethod
    def _docstring(match, language: str) -> Optional[str]:
        """The leading string literal of a Python function body, without its quotes."""
        if language != "python":
            return None
        try:
            body = match.field("body")
            children = body.children() if body else []
            first = children[0] if children else None
            if first is None or first.kind() != "expression_statement":
                return None
            inner = first.children()
            if len(inner) != 1 or inner[0].kind() != "string":
                return None
            text = inner[0].text().strip()
        except Exception as e:
            logger.debug("Docstring read failed: %s", e)
            return None
        # f-strings and bytes literals are not docstrings: Python sets __doc__ to None.
        if text[:1] in "fFbB" or text[:2].lower() in ("rf", "fr", "rb", "br"):
            return None
        text = text.lstrip("rRuU")
        for quote in ('"""', "'''", '"', "'"):
            if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
                text = text[len(quote):-len(quote)]
                break
        text = "\n".join(line.strip() for line in text.strip().splitlines())
        return text[:MAX_DOCSTRING_CHARS] or None

    def _function_metadata(
        self, match, language: str, scope: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Raw call names and enclosing class for one function node.

        The call resolver (indexing/call_resolver.py) turns these into CALLS
        edges once every file is in the graph, because a callee can live in a
        file that is parsed later.
        """
        meta: Dict[str, Any] = {}
        calls = self._extract_calls(match, language)
        if calls:
            meta["calls"] = calls
            if language == "cpp":
                scoped = self._scoped_calls(match)
                if scoped:
                    meta["scoped_calls"] = scoped
        parent = self._enclosing_class(match, language)
        if language == "go" and match.kind() == "method_declaration":
            parent = self._go_receiver_type(match)
        if scope and not parent:
            # `void Widget::Paint() {` out of line: a method of `Widget`. The parser
            # cannot tell a class scope from a namespace scope.
            parent = _GENERIC_GROUP.sub("", scope[-1]).strip() or None
        if parent:
            meta["parent_class"] = parent
        return meta

    def _extract_calls(self, match, language: str) -> List[str]:
        """Distinct normalised callee names inside a function, in source order."""
        seen: Dict[str, None] = {}
        for kind, field_name in CALL_KINDS.get(language, ()):
            try:
                found = match.find_all(kind=kind)
            except Exception as e:
                logger.debug("ast-grep call search failed for kind %s: %s", kind, e)
                continue
            for call in found:
                try:
                    text = self._callee_text(call, field_name)
                except Exception as e:
                    logger.debug("Failed to read callee: %s", e)
                    continue
                name = normalize_call_text(_language_callee(text, language))
                if name:
                    seen.setdefault(name, None)
                if len(seen) >= MAX_CALLS_PER_FUNCTION:
                    return list(seen)
        return list(seen)

    @staticmethod
    def _scoped_calls(match) -> List[str]:
        """C++ calls through `::` (`base::Bind()`, `Widget::Create()`), normalised.

        normalize_call_text() turns `::`, `.` and `->` into dots. The resolver needs to
        know which calls were `ns::f()`: such a call names a free function or a static
        method, never a method on an object of unknown type.
        """
        seen: Dict[str, None] = {}
        try:
            for call in match.find_all(kind="call_expression"):
                callee = call.field("function")
                if callee and callee.kind() == "qualified_identifier":
                    name = normalize_call_text(_language_callee(callee.text(), "cpp"))
                    if name:
                        seen.setdefault(name, None)
                if len(seen) >= MAX_CALLS_PER_FUNCTION:
                    break
        except Exception as e:
            logger.debug("Scoped call search failed: %s", e)
        return list(seen)

    @staticmethod
    def _callee_text(call, field_name) -> str:
        if field_name == FIRST_NAMED_CHILD:
            named = [c for c in call.children() if c.is_named()]
            return named[0].text() if named else ""
        if isinstance(field_name, str):
            callee = call.field(field_name)
            if not callee:
                return ""
            # A long call chain (`a.m0().m1()...`) makes every callee text the whole
            # prefix, so reading it is quadratic. Such a callee is dropped anyway.
            span = callee.range()
            if span.end.index - span.start.index > 4 * MAX_CALL_TEXT:
                return ""
            return callee.text()
        # Java method_invocation, PHP member and scoped calls: receiver (optional) + name
        receiver_field, name_field = field_name or ("object", "name")
        name = call.field(name_field)
        if not name:
            return ""
        obj = call.field(receiver_field)
        return f"{obj.text()}.{name.text()}" if obj else name.text()

    @staticmethod
    def _go_receiver_type(match) -> Optional[str]:
        """`func (s *Server) Run()` -> `Server`."""
        receiver = match.field("receiver")
        if not receiver:
            return None
        text = re.sub(r"\[[^\[\]]*\]", "", receiver.text().strip())  # `Box[T]` -> `Box`
        found = re.findall(r"([A-Za-z_]\w*)\s*\)\s*$", text)
        return found[0] if found else None

    @staticmethod
    def _enclosing_class(match, language: str) -> Optional[str]:
        """Name of the nearest class-like ancestor, or None for a free function."""
        kinds = dict(CLASS_ANCESTOR_KINDS.get(language, ()))
        if not kinds:
            return None
        try:
            for ancestor in match.ancestors():
                field_name = kinds.get(ancestor.kind())
                if field_name:
                    name = ancestor.field(field_name)
                    if name:
                        text = _GENERIC_GROUP.sub("", " ".join(name.text().split()))
                        if language == "cpp":  # `class Outer::Inner {` is `Inner`
                            text = split_cpp_scope(text)[-1].strip()
                        return text[:80]
        except Exception as e:
            logger.debug("Failed to read enclosing class: %s", e)
        return None

    def _extract_classes(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph,
    ) -> int:
        """Extract class definitions from ast-grep node.

        Kind-based for the same reason as _extract_functions: the old
        "class $NAME: $$$BODY" pattern matched only base-less classes, so
        every `class Foo(Base):` was missing from the graph.
        """
        count = 0
        lang_kinds = CLASS_KINDS.get(language)
        if not lang_kinds:
            return 0

        matches = []
        for kind in lang_kinds:
            try:
                matches.extend(sg_node.find_all(kind=kind))
            except Exception as e:
                logger.debug("ast-grep find_all failed for kind %s: %s", kind, e)

        for match in matches:
            try:
                rng = match.range()
                if language in _C_FAMILY:
                    class_name = self._c_class_name(match)
                    if not class_name:
                        continue
                else:
                    name_text = match.field("name")
                    class_name = (
                        name_text.text() if name_text else f"class_{self._node_counter}"
                    )

                class_id = self._make_id("class")
                class_node = UniversalNode(
                    id=class_id,
                    name=class_name,
                    node_type=NodeType.CLASS,
                    location=UniversalLocation(
                        file_path=filepath,
                        start_line=rng.start.line + 1,
                        end_line=rng.end.line + 1,
                        language=language,
                    ),
                    language=language,
                    line_count=rng.end.line - rng.start.line + 1,
                )
                graph.add_node(class_node)

                graph.add_relationship(UniversalRelationship(
                    id=self._make_rel_id(),
                    source_id=module_id,
                    target_id=class_id,
                    relationship_type=RelationshipType.CONTAINS,
                ))
                count += 1
            except Exception as e:
                logger.debug("Failed to extract class at %s: %s", filepath, e)
                continue

        return count

    @staticmethod
    def _c_class_name(match) -> Optional[str]:
        """The name of a C or C++ struct, union or class definition, or None.

        `struct point p;` and `class Foo;` name a type but do not define it: no body, no
        node. `typedef struct { ... } anon_t;` takes the typedef name. `class Outer::Inner`
        is `Inner`, and template arguments are dropped, as for the parent of a method.
        """
        if not match.field("body"):
            return None
        name = match.field("name")
        if name:
            text = split_cpp_scope(" ".join(name.text().split()))[-1]
            return _GENERIC_GROUP.sub("", text).strip() or None
        parent = match.parent()
        if parent is not None and parent.kind() == "type_definition":
            declarator = parent.field("declarator")
            if declarator and declarator.kind() == "type_identifier":
                return declarator.text()
        return None

    def _extract_imports(
        self, sg_node, filepath: str, language: str, module_id: str,
        graph: UniversalGraph,
    ) -> None:
        """Extract import statements and add relationships."""
        patterns = {
            "python": ["import $NAME", "from $NAME import $$$ITEMS"],
            "javascript": ["import $$$ITEMS from '$NAME'"],
            "typescript": ["import $$$ITEMS from '$NAME'"],
            "go": ['import "$NAME"'],
            "java": ["import $NAME"],
            "rust": ["use $NAME"],
        }

        imported = self._collect_import_names(sg_node, language)

        lang_patterns = patterns.get(language, [])
        for pattern in lang_patterns:
            try:
                matches = sg_node.find_all(pattern=pattern)
                for match in matches:
                    name_text = match.get_match("NAME")
                    if name_text:
                        import_name = name_text.text()
                        imported.setdefault(import_name.strip("'\""), None)
                        target_id = f"module:{import_name}"
                        graph.add_relationship(UniversalRelationship(
                            id=self._make_rel_id(),
                            source_id=module_id,
                            target_id=target_id,
                            relationship_type=RelationshipType.IMPORTS,
                        ))
            except Exception as e:
                logger.debug("Import match failed in %s: %s", filepath, e)
                continue

        module = graph.nodes.get(module_id)
        if module is not None and imported:
            module.metadata["imports"] = list(imported)

    @staticmethod
    def _collect_import_names(sg_node, language: str) -> Dict[str, None]:
        """Imported module names by node kind, for the call resolver.

        Kind-based because the string patterns above miss double-quoted
        TypeScript imports and relative Python imports. Order is kept.
        """
        names: Dict[str, None] = {}
        try:
            if language == "python":
                for node in sg_node.find_all(kind="import_statement"):
                    for dotted in node.find_all(kind="dotted_name"):
                        names.setdefault(dotted.text(), None)
                for node in sg_node.find_all(kind="import_from_statement"):
                    module = node.field("module_name")
                    if module:
                        names.setdefault(module.text(), None)
            elif language in ("javascript", "typescript"):
                for node in sg_node.find_all(kind="import_statement"):
                    source = node.field("source")
                    if source:
                        names.setdefault(source.text().strip("'\"`"), None)
            elif language in _C_FAMILY:
                # `#include "base/util.h"` imports the module path `base/util`: the
                # header and its `.c` or `.cc` file. `<stdio.h>` is a system header.
                for node in sg_node.find_all(kind="preproc_include"):
                    path = node.field("path")
                    if path and path.kind() == "string_literal":
                        target = posixpath.splitext(path.text().strip('"'))[0]
                        if target:
                            names.setdefault(target, None)
            elif language == "php":
                # `use App\Support\Helper;` -> `Support/Helper`: the PSR-4 root (`App`) is
                # often not a folder name, so only the last two parts must match the path.
                for clause in sg_node.find_all(kind="namespace_use_clause"):
                    named = [c for c in clause.children() if c.is_named()]
                    if not named:
                        continue
                    parts = [p for p in named[0].text().split("\\") if p]
                    if parts:
                        names.setdefault("/".join(parts[-2:]), None)
        except Exception as e:
            logger.debug("Import name collection failed: %s", e)
        return names
