"""AST-grep based structural parser for code graph building.

Ported from code-graph-mcp's universal_parser.py. Uses ast-grep for
structural analysis across 25+ languages.
"""

import logging
import re
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

logger = logging.getLogger(__name__)

# Call expressions per language: (node kind, field that holds the callee).
# A field of None means "use the whole node" (handled for Java below).
CALL_KINDS: Dict[str, tuple] = {
    "python": (("call", "function"),),
    "javascript": (("call_expression", "function"), ("new_expression", "constructor")),
    "typescript": (("call_expression", "function"), ("new_expression", "constructor")),
    "go": (("call_expression", "function"),),
    "java": (("method_invocation", None), ("object_creation_expression", "type")),
    "rust": (("call_expression", "function"),),
}

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
}

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
    "csharp": "c_sharp",
    "scala": "scala",
    "lua": "lua",
    "dart": "dart",
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

    def can_parse(self, filepath: str) -> bool:
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
        return f"{prefix}:{self._node_counter}"

    def _make_rel_id(self) -> str:
        self._rel_counter += 1
        return f"rel:{self._rel_counter}"

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
        kinds = {
            "python": ("function_definition",),
            "javascript": ("function_declaration",),
            "typescript": ("function_declaration",),
            "go": ("function_declaration", "method_declaration"),
            "java": ("method_declaration",),
            "rust": ("function_item",),
        }

        lang_kinds = kinds.get(language)
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
                name_text = match.field("name")
                func_name = name_text.text() if name_text else f"anonymous_{self._node_counter}"

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
                    metadata=self._function_metadata(match, language),
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

    def _function_metadata(self, match, language: str) -> Dict[str, Any]:
        """Raw call names and enclosing class for one function node.

        The call resolver (indexing/call_resolver.py) turns these into CALLS
        edges once every file is in the graph, because a callee can live in a
        file that is parsed later.
        """
        meta: Dict[str, Any] = {}
        calls = self._extract_calls(match, language)
        if calls:
            meta["calls"] = calls
        parent = self._enclosing_class(match, language)
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
                name = normalize_call_text(text)
                if name:
                    seen.setdefault(name, None)
                if len(seen) >= MAX_CALLS_PER_FUNCTION:
                    return list(seen)
        return list(seen)

    @staticmethod
    def _callee_text(call, field_name: Optional[str]) -> str:
        if field_name is not None:
            callee = call.field(field_name)
            return callee.text() if callee else ""
        # Java method_invocation: object (optional) + name
        name = call.field("name")
        if not name:
            return ""
        obj = call.field("object")
        return f"{obj.text()}.{name.text()}" if obj else name.text()

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
                        return " ".join(name.text().split())[:80]
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
        kinds = {
            "python": ("class_definition",),
            "javascript": ("class_declaration",),
            "typescript": ("class_declaration",),
            "java": ("class_declaration",),
            "rust": ("struct_item",),
        }

        lang_kinds = kinds.get(language)
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
                name_text = match.field("name")
                class_name = name_text.text() if name_text else f"class_{self._node_counter}"

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
        except Exception as e:
            logger.debug("Import name collection failed: %s", e)
        return names
