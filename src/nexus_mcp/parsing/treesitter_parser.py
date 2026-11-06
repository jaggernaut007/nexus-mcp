"""Tree-sitter based multi-language code parser.

Ported from CodeGrok MCP. Extracts symbols, imports, and calls from source files.
Supports 12 languages via tree-sitter grammars.
"""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from tree_sitter_languages import get_parser

from nexus_mcp.core.interfaces import IParser
from nexus_mcp.core.models import ParsedFile, Symbol, SymbolType
from nexus_mcp.parsing.language_registry import (
    TREESITTER_GRAMMARS,
    get_config_for_language,
    get_language_for_file,
    get_treesitter_extensions,
)

logger = logging.getLogger(__name__)

# A `.h` file with one of these is a C++ header. Chromium and most C++ projects use `.h`.
_CPP_HEADER_MARKERS = re.compile(
    rb"^[ \t]*(?:namespace\b|template\s*<|class\s+\w|(?:public|private|protected)\s*:)"
    rb"|\w::~?\w",
    re.MULTILINE,
)
# ALL_CAPS macros between `class`/`struct`/`union` and the class name:
# `class BASE_EXPORT TRIVIAL_ABI WeakReference {`, `class COMPONENT_EXPORT(X) Foo final : Bar {`.
# tree-sitter-cpp cannot expand them and reads such a class as a function definition. The
# lookahead needs a name after the macros and then `{`, `:`, `final` or template arguments,
# so an ALL_CAPS class name (`class URL_PARSER : public Parser {`) is not taken for a macro.
# `[[...]]` attributes can come before, between or after the macros
# (`class [[nodiscard]] SCOPED_LOCKABLE Foo {`, `class BASE_EXPORT [[nodiscard]] Foo {`). They
# are masked with the macros; the code of the symbol still comes from the original text.
_CPP_CLASS_HEAD_MACROS = re.compile(
    rb"\b(?:class|struct|union)\s+"
    rb"((?:\[\[[^\[\]]*\]\]\s*|[A-Z][A-Z0-9_]+\b(?:\s*\([^()\n]*\))?\s+)+)"
    rb"(?=[A-Za-z_][\w:]*\s*(?:<[^;{}]*>\s*)?(?:final\b\s*)?[:{])"
)
# ALL_CAPS annotation macros between the parameter list and the body of a function:
# `void Acquire() EXCLUSIVE_LOCK_FUNCTION() {`. Without the mask the function is named after
# the macro. Only a body (`{`, `= default`, `override`, a constructor's `: member_(x)`, ...)
# follows; a declaration that ends
# in `;` gives no symbol, and `if (x) DCHECK(y);` is left as it is.
# The `\b` after each name keeps a name whole. Without it `DEFINE_CHECK_OP_IMPL` can split
# into many shorter names, and a run of macro lines (base/check_op.h) backtracks for minutes.
_CPP_FUNCTION_TAIL_MACROS = re.compile(
    rb"\)\s*(?:(?:const|volatile|noexcept|override|final)\b\s*)*"
    rb"((?:[A-Z][A-Z0-9_]{2,}\b(?:\s*\([^()\n]*\))?\s*)+)"
    rb"(?=\{|=(?!=)|:(?!:)|override\b|final\b|const\b|->)"
)
# C and C++ declarators that wrap the name of a function (`*f()`, `&f()`, `f() const`).
_DECLARATOR_WRAPPERS = (
    "function_declarator", "pointer_declarator", "reference_declarator",
    "parenthesized_declarator", "attributed_declarator",
)
_DECLARATOR_NAMES = (
    "identifier", "field_identifier", "destructor_name", "operator_name", "type_identifier",
)
_C_FAMILY = ("c", "cpp")


def _inside_function_definition(node) -> bool:
    """True when a C declarator is part of a function definition, not a prototype."""
    parent = node.parent
    while parent is not None:
        if parent.type == "function_definition":
            return True
        if parent.type in ("declaration", "translation_unit", "field_declaration"):
            return False
        parent = parent.parent
    return False
# A declarator with one of these names is a misparse, not a function: with
# `FMT_BEGIN_NAMESPACE namespace detail {` the macro looks like a return type and
# `namespace` like the name of a function.
_CPP_KEYWORD_NAMES = frozenset({
    "namespace", "if", "for", "while", "switch", "return", "sizeof", "do", "else", "case",
    "decltype", "alignof", "static_assert", "template", "typedef", "using", "class",
    "struct", "union", "enum", "noexcept", "throw", "catch", "try", "new", "delete",
})


def is_cpp_header(filepath: str, content: bytes) -> bool:
    """True for a `.h` file with C++ syntax (`namespace`, `class X`, `template <`, `::`)."""
    return Path(filepath).suffix.lower() == ".h" and bool(_CPP_HEADER_MARKERS.search(content))


def mask_cpp_class_macros(content: bytes) -> bytes:
    """The source with the macros of C++ class heads and function tails replaced by spaces.

    `class BASE_EXPORT Foo {` is parsed as `class             Foo {`, and the macro
    of `void Acquire() EXCLUSIVE_LOCK_FUNCTION() {` becomes spaces in the same way.
    The length and the line breaks do not change, so node offsets and line numbers stay
    valid and the names and code of the symbols are still read from the original source.
    """
    def blank(match: "re.Match[bytes]") -> bytes:
        start, end = match.span(1)
        head = match.group(0)
        macros = re.sub(rb"[^\n]", b" ", match.group(1))
        offset = start - match.start()
        return head[:offset] + macros + head[offset + (end - start):]

    masked = _CPP_CLASS_HEAD_MACROS.sub(blank, content)
    return _CPP_FUNCTION_TAIL_MACROS.sub(blank, masked)


def split_cpp_scope(name: str) -> List[str]:
    """Split a C++ name on each `::` that is outside template arguments."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(name):
        ch = name[i]
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif depth == 0 and name.startswith("::", i):
            parts.append(name[start:i])
            i += 2
            start = i
            continue
        i += 1
    parts.append(name[start:])
    return parts


def c_declarator_name(
    node: Any, text: Callable[[Any], str]
) -> Optional[Tuple[List[str], str]]:
    """The scope and the name of a C or C++ function definition, from its declarator.

    `WeakReference WeakReferenceOwner::GetRef() const` gives
    (["WeakReferenceOwner"], "GetRef"), not the return type. Template arguments
    leave the scope (`Holder<T>::Reset` gives ["Holder"]). Constructors, destructors
    (`~Foo`), operators (`operator==`, `operator bool`) and pointer or reference
    returns (`char* f()`, `A& f()`) keep their own name. None when no name is found.

    ``node`` has the tree-sitter node API (`type`, `child_by_field_name`,
    `named_children`), and ``text`` gives the source text of a node. The ast-grep
    parser passes a view of its own nodes, so both parsers name a function the same way.
    """
    scope: List[str] = []
    decl = node.child_by_field_name("declarator")
    while decl is not None:
        kind = decl.type
        if kind in _DECLARATOR_WRAPPERS:
            inner = decl.child_by_field_name("declarator")
            if inner is None:  # `&f()` has no field name for its inner declarator
                named = [c for c in decl.named_children if "declarator" in c.type]
                inner = named[-1] if named else None
            decl = inner
        elif kind == "qualified_identifier":
            outer = decl.child_by_field_name("scope")
            if outer is not None:
                if outer.type == "template_type":
                    outer = outer.child_by_field_name("name") or outer
                scope.append(text(outer).strip())
            decl = decl.child_by_field_name("name")
        elif kind == "template_function":
            decl = decl.child_by_field_name("name")
        elif kind == "operator_cast":
            target = decl.child_by_field_name("type")
            target_text = text(target).strip() if target else ""
            return scope, f"operator {target_text}".strip()
        elif kind in _DECLARATOR_NAMES:
            name = " ".join(text(decl).split())
            if name in _CPP_KEYWORD_NAMES:
                return None
            return scope, re.sub(r"^operator\s+(?=\W)", "operator", name)
        else:
            return None
    return None
# Nodes between a doc comment and its item: `#[derive(Debug)]` in Rust, `[Fact]` in C#.
_ATTRIBUTE_KINDS = ("attribute_item", "attribute_list", "attribute")
# A doc comment: `/// text` or `/** text */`. `//!` and `/*!` describe the enclosing module.
_DOC_COMMENT = re.compile(r"^(?:///(?!/)|/\*\*(?!/))")
_XML_TAG = re.compile(r"</?[A-Za-z][^>]*>")


class TreeSitterParser(IParser):
    """Production tree-sitter parser. NOT thread-safe per instance."""

    MAX_CODE_SNIPPET_CHARS = 4000
    MAX_FILE_SIZE_MB = 10
    MAX_MODULE_DOC_CHARS = 1500
    MIN_MODULE_DOC_CHARS = 20
    MAX_MODULE_DEFINES = 40
    # A header comment with one of these words is a licence text, not a description.
    LICENCE_WORDS = ("copyright", "license", "licence", "spdx")
    # File names that say nothing: the folder name is the module name.
    PACKAGE_FILE_STEMS = ("__init__", "index", "mod", "main")
    # Nodes before the top comment that say nothing about the file: `<?php`,
    # `declare(strict_types=1);`.
    FILE_PREAMBLE_KINDS = ("php_tag", "declare_statement")
    # Comments that talk to a tool, not to a reader: they are not a module description.
    PRAGMA_PREFIXES = (
        "pylint:", "type:", "noqa", "mypy:", "pyright:", "flake8:", "isort:", "fmt:", "ruff:",
        "eslint", "prettier", "@ts-", "tslint:", "jshint", "istanbul ", "c8 ", "biome",
        "go:build", "+build", "go:generate", "nolint", "nosec", "pragma", "vim:", "lint:",
    )

    def __init__(self):
        self._parsers: Dict[str, Any] = {}

    def can_parse(self, filepath: str) -> bool:
        language = get_language_for_file(filepath)
        return language is not None and Path(filepath).suffix.lower() in get_treesitter_extensions()

    def parse_file(self, filepath: str) -> ParsedFile:
        start_time = time.time()
        file_path = Path(filepath)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        language = get_language_for_file(filepath)
        if language is None:
            return ParsedFile(
                filepath=str(filepath),
                language="unknown",
                error=f"Unsupported file type: {file_path.suffix}",
                parse_time=time.time() - start_time,
            )

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > self.MAX_FILE_SIZE_MB:
            logger.warning("Large file (%.2fMB): %s", file_size_mb, filepath)

        try:
            content = file_path.read_bytes()
            if self._is_binary_file(content):
                return ParsedFile(
                    filepath=str(filepath),
                    language=language,
                    error="Binary file detected",
                    parse_time=time.time() - start_time,
                )
        except Exception as e:
            return ParsedFile(
                filepath=str(filepath),
                language=language,
                error=f"Error reading file: {e}",
                parse_time=time.time() - start_time,
            )

        if language == "c" and is_cpp_header(filepath, content):
            language = "cpp"

        try:
            parser = self._get_parser(language)
            config = get_config_for_language(language)
        except Exception as e:  # pragma: no cover
            return ParsedFile(
                filepath=str(filepath),
                language=language,
                error=f"Failed to initialize parser: {e}",
                parse_time=time.time() - start_time,
            )

        try:
            source = mask_cpp_class_macros(content) if language == "cpp" else content
            tree = parser.parse(source)
            symbols = self._extract_symbols(
                tree.root_node, content, filepath, language, config
            )
            module = self._extract_module_symbol(
                tree.root_node, content, filepath, language, symbols
            )
            if module:
                symbols.append(module)
            imports = self._extract_imports(tree.root_node, content, config)
            parse_time = time.time() - start_time
            return ParsedFile(
                filepath=str(filepath),
                language=language,
                symbols=symbols,
                imports=imports,
                parse_time=parse_time,
            )
        except Exception as e:  # pragma: no cover
            return ParsedFile(
                filepath=str(filepath),
                language=language,
                error=f"Parsing error: {e}",
                parse_time=time.time() - start_time,
            )

    def get_supported_extensions(self) -> List[str]:
        return sorted(list(get_treesitter_extensions()))

    # --- Private helpers ---

    def _is_binary_file(self, content: bytes) -> bool:
        sample = content[:8192]
        if b"\x00" in sample:
            return True
        try:
            sample.decode("utf-8")
            return False
        except UnicodeDecodeError:
            text_chars = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
            if len(sample) == 0:
                return False
            return (text_chars / len(sample)) < 0.7

    def _get_parser(self, language: str):
        if language not in self._parsers:
            self._parsers[language] = get_parser(TREESITTER_GRAMMARS.get(language, language))
        return self._parsers[language]

    def _extract_symbols(
        self, root_node, content: bytes, filepath: str, language: str, config: Dict
    ) -> List[Symbol]:
        symbols: List[Symbol] = []
        current_class: Optional[str] = None
        class_stack: List[str] = []

        function_types = config.get("function_types", [])
        class_types = config.get("class_types", [])
        method_types = config.get("method_types", [])
        constant_types = config.get("constant_types", [])
        scope_types = config.get("scope_types", {})

        def traverse(node, depth: int = 0):
            nonlocal current_class
            node_type = node.type
            opened_class = False  # only a class that gave a symbol closes a scope

            if node_type in scope_types:
                scope = self._scope_name(node, content, scope_types[node_type])
                if scope:
                    class_stack.append(scope)
                    current_class = scope
                    opened_class = True

            elif node_type in class_types:
                # A C++ class nested in another has the outer class as parent, so it is
                # found as `Outer::Inner` (`Outer.Inner` in the index).
                outer = class_stack[-1] if (language == "cpp" and class_stack) else None
                sym = self._extract_class_symbol(
                    node, content, filepath, language, config, parent=outer
                )
                if sym:
                    symbols.append(sym)
                    scope = sym.qualified_name if language == "cpp" else sym.name
                    class_stack.append(scope)
                    current_class = scope
                    opened_class = True

            elif node_type in function_types and not current_class:
                # C lists `function_declarator` to keep header prototypes. The declarator
                # inside a definition is the same function again, as a one-line chunk.
                if node_type == "function_declarator" and _inside_function_definition(node):
                    sym = None
                else:
                    sym = self._extract_function_symbol(
                        node, content, filepath, language, config, parent=None
                    )
                if sym:
                    symbols.append(sym)

            elif node_type in method_types and current_class:
                sym = self._extract_function_symbol(
                    node, content, filepath, language, config,
                    parent=current_class, is_method=True,
                )
                if sym:
                    symbols.append(sym)

            elif node_type in constant_types and not current_class:
                sym = self._extract_constant_symbol(node, content, filepath, language, config)
                if sym:
                    symbols.append(sym)

            for child in node.children:
                traverse(child, depth + 1)

            if opened_class:
                class_stack.pop()
                current_class = class_stack[-1] if class_stack else None

        traverse(root_node)
        return symbols

    def _extract_module_symbol(
        self, root_node, content: bytes, filepath: str, language: str, symbols: List[Symbol]
    ) -> Optional[Symbol]:
        """One symbol for the file itself, from the docstring or comment at its top.

        The text at the top of a file often says what the file is for, in words that
        no class or function in it repeats. Without this symbol a search by concept
        cannot find such a file. A file with no such text gets no module symbol.
        """
        doc, end_line = self._module_doc(root_node, content)
        if len(doc) < self.MIN_MODULE_DOC_CHARS:
            return None
        path = Path(filepath)
        name = path.parent.name if path.stem in self.PACKAGE_FILE_STEMS else path.stem
        defines = [
            s.name for s in symbols
            if s.parent is None and s.type in (SymbolType.CLASS, SymbolType.FUNCTION)
        ]
        return Symbol(
            name=name or path.stem,
            type=SymbolType.MODULE,
            filepath=filepath,
            line_start=1,
            line_end=end_line,
            language=language,
            signature="",
            code_snippet=doc[: self.MAX_MODULE_DOC_CHARS],
            metadata={"defines": defines[: self.MAX_MODULE_DEFINES]},
        )

    def _is_pragma_comment(self, text: str) -> bool:
        """A comment for a tool (shebang, encoding, linter, type checker), not for a reader."""
        low = text.strip().lower()
        return (
            low.startswith(self.PRAGMA_PREFIXES)
            or "coding:" in low[:30]
            or "-*-" in low
        )

    def _comment_text(self, raw: str) -> Optional[str]:
        """The words of one comment node without its markers, or None for a pragma comment."""
        raw = raw.replace("\r\n", "\n")
        if raw.startswith("#!"):
            return None
        lines = [line.strip().lstrip("/#*!-").rstrip("*/").strip() for line in raw.split("\n")]
        if self._is_pragma_comment(lines[0]):
            return None
        return "\n".join(lines)

    def _module_doc(self, root_node, content: bytes) -> tuple[str, int]:
        """The docstring or the comment block at the top of a file, and its last line.

        A docstring wins over comments, and comments for a tool (a linter pragma, an
        encoding line) or a licence are skipped one block at a time: a licence block, a
        blank line and then a package comment still give the package comment. A block is
        a run of comment lines with no blank line between them.
        """
        blocks: List[List[Tuple[str, int]]] = []  # each item: (text, last line)
        prev_end: Optional[int] = None
        docstring: Optional[Tuple[str, int]] = None
        for child in root_node.children:
            if child.type in self.FILE_PREAMBLE_KINDS:
                continue
            if "comment" in child.type:
                text = self._comment_text(self._get_node_text(child, content))
                first_line, last_line = child.start_point[0], child.end_point[0]
                if text is None:
                    prev_end = None  # a pragma ends the block before it
                    continue
                if blocks and prev_end is not None and first_line - prev_end <= 1:
                    blocks[-1].append((text, last_line + 1))
                else:
                    blocks.append([(text, last_line + 1)])
                prev_end = last_line
                continue
            if child.type == "expression_statement":
                for sc in child.children:
                    if sc.type in ("string", "string_literal"):
                        raw = self._get_node_text(sc, content).strip()
                        text = self._strip_quotes(raw).replace("\r\n", "\n")
                        docstring = (text, child.end_point[0] + 1)
                        break
            break
        # `"use strict"` is a string statement too: too short to be a docstring.
        if docstring and len(docstring[0]) >= self.MIN_MODULE_DOC_CHARS:
            return docstring
        for block in blocks:
            doc = "\n".join(text for text, _ in block).strip()
            if doc and not any(word in doc.lower() for word in self.LICENCE_WORDS):
                return doc, block[-1][1]
        return "", 1

    @staticmethod
    def _strip_quotes(raw: str) -> str:
        text = raw.lstrip("rRbBuUfF")
        for q in ('"""', "'''", '"', "'"):
            if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
                return text[len(q):-len(q)].strip()
        return text.strip()

    def _extract_class_symbol(
        self, node, content: bytes, filepath: str, language: str, config: Dict,
        parent: Optional[str] = None,
    ) -> Optional[Symbol]:
        # `class Foo;` and `struct foo *p` name a type but do not define it.
        if language in _C_FAMILY and node.child_by_field_name("body") is None:
            return None
        name = self._get_node_name(node, content, config)
        if not name:
            return None
        if language == "cpp" and "::" in name:
            # `class Outer::Inner {` out of line: the same name and parent as a class nested
            # in the body of `Outer`. Only a `::` outside template arguments splits the
            # name (`struct std::hash<a::B> {` is `hash<a::B>` in `std`). Template
            # arguments in the scope are dropped.
            parts = [p.strip() for p in split_cpp_scope(name)]
            scope = [re.sub(r"<.*>", "", p, flags=re.DOTALL).strip() for p in parts[:-1]]
            if not parts[-1]:
                return None
            name = parts[-1]
            parent = ".".join(filter(None, [parent, *scope])) or None
        return Symbol(
            name=name,
            type=SymbolType.CLASS,
            parent=parent,
            filepath=filepath,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            signature=self._get_node_text(node, content).split("\n")[0].strip(),
            docstring=self._extract_docstring(node, content, config),
            code_snippet=self._get_code_snippet(node, content),
            imports=tuple(self._extract_imports_from_node(node, content, config)),
            calls=tuple(self._extract_calls_from_node(node, content, config)),
        )

    def _extract_function_symbol(
        self, node, content: bytes, filepath: str, language: str, config: Dict,
        parent: Optional[str] = None, is_method: bool = False,
    ) -> Optional[Symbol]:
        if language in _C_FAMILY and node.type == "function_definition":
            resolved = c_declarator_name(node, lambda n: self._get_node_text(n, content))
            if resolved is None:
                return None  # a name from elsewhere (the return type) is worse than none
            scope, name = resolved
            if scope:
                # `Owner::Method() {...}` out of line: a method of `Owner`. The parser
                # cannot tell a class scope from a namespace scope.
                parent = ".".join(filter(None, [parent, *scope]))
                is_method = True
        else:
            name = self._get_node_name(node, content, config)
        if not name:
            return None
        return Symbol(
            name=name,
            type=SymbolType.METHOD if is_method else SymbolType.FUNCTION,
            filepath=filepath,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            signature=self._get_node_text(node, content).split("\n")[0].strip(),
            docstring=self._extract_docstring(node, content, config),
            parent=parent,
            code_snippet=self._get_code_snippet(node, content),
            imports=tuple(self._extract_imports_from_node(node, content, config)),
            calls=tuple(self._extract_calls_from_node(node, content, config)),
        )

    def _extract_constant_symbol(
        self, node, content: bytes, filepath: str, language: str, config: Dict
    ) -> Optional[Symbol]:
        full_text = self._get_node_text(node, content).strip()
        name = None

        if language == "python":
            for child in node.children:
                if child.type == "assignment":
                    for sc in child.children:
                        if sc.type == "identifier":
                            pn = self._get_node_text(sc, content)
                            if pn.isupper() or ("_" in pn and pn.replace("_", "").isupper()):
                                name = pn
                            break
                    break

        elif language in ("javascript", "typescript"):
            if full_text.startswith("const "):
                for child in node.children:
                    if child.type == "variable_declarator":
                        for sc in child.children:
                            if sc.type == "identifier":
                                pn = self._get_node_text(sc, content)
                                if pn.isupper() or (
                                    "_" in pn and pn.replace("_", "").isupper()
                                ):
                                    name = pn
                                break
                        break

        elif language == "rust":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = self._get_node_text(name_node, content).strip()

        if not name:
            return None

        return Symbol(
            name=name,
            type=SymbolType.VARIABLE,
            filepath=filepath,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            signature=full_text,
            code_snippet=full_text,
        )

    def _extract_imports(self, root_node, content: bytes, config: Dict) -> List[str]:
        imports: List[str] = []
        import_types = config.get("import_types", [])

        def traverse(node):
            if node.type in import_types:
                text = self._get_node_text(node, content).strip()
                if text and text not in imports:
                    imports.append(text)
            for child in node.children:
                traverse(child)

        traverse(root_node)
        return imports

    def _extract_imports_from_node(self, node, content: bytes, config: Dict) -> List[str]:
        imports: List[str] = []
        import_types = config.get("import_types", [])

        def traverse(n):
            if n.type in import_types:
                text = self._get_node_text(n, content).strip()
                if text and text not in imports:
                    imports.append(text)
            for child in n.children:
                traverse(child)

        traverse(node)
        return imports

    def _extract_calls_from_node(self, node, content: bytes, config: Dict) -> List[str]:
        calls: Set[str] = set()
        call_types = config.get("call_types", [])

        def traverse(n):
            if n.type in call_types:
                name = self._get_call_name(n, content)
                if name:
                    calls.add(name)
            for child in n.children:
                traverse(child)

        traverse(node)
        return sorted(calls)

    def _get_call_name(self, call_node, content: bytes) -> Optional[str]:
        for child in call_node.children:
            if child.type in ("identifier", "name", "word", "field_identifier"):
                return self._get_node_text(child, content).strip()
            elif child.type == "attribute":
                for sc in child.children:
                    if sc.type in ("identifier", "property_identifier", "field_identifier"):
                        return self._get_node_text(sc, content).strip()
            elif child.type == "member_expression":
                for sc in child.children:
                    if sc.type in ("property_identifier", "identifier"):
                        return self._get_node_text(sc, content).strip()
            elif child.type == "selector_expression":
                for sc in child.children:
                    if sc.type in ("field_identifier", "identifier"):
                        return self._get_node_text(sc, content).strip()
        if call_node.child_count > 0:
            return self._get_node_text(call_node.children[0], content).strip().split("(")[0]
        return None

    def _extract_docstring(self, node, content: bytes, config: Dict) -> str:
        if config.get("doc_comment") == "leading":
            return self._leading_doc_comment(node, content)
        body = self._get_body_node(node, config)
        if not body:
            return ""
        for child in body.children:
            if child.type == "expression_statement":
                for sc in child.children:
                    if sc.type in ("string", "string_literal"):
                        return self._clean_docstring(self._get_node_text(sc, content))
            elif child.type in ("string", "string_literal", "comment"):
                return self._clean_docstring(self._get_node_text(child, content))
        return ""

    def _leading_doc_comment(self, node, content: bytes) -> str:
        """The first line of the doc comment just above a node (`///` or `/** */`).

        Attributes between the comment and the node are skipped. A blank line ends the
        comment. C# XML tags (`<summary>`) and PHPDoc tags (`@param`) are not text.
        """
        parts: List[str] = []
        row = node.start_point[0]
        prev = node.prev_named_sibling
        while prev is not None and prev.end_point[0] >= row - 1:
            if prev.type in _ATTRIBUTE_KINDS:
                pass
            elif "comment" in prev.type:
                text = self._get_node_text(prev, content).strip()
                if not _DOC_COMMENT.match(text):
                    break
                parts.insert(0, text)
            else:
                break
            row = prev.start_point[0]
            prev = prev.prev_named_sibling
        for raw in "\n".join(parts).split("\n"):
            line = raw.strip().lstrip("/*!").rstrip("*/").strip()
            line = _XML_TAG.sub("", line).strip()
            if line and not line.startswith("@"):
                return line
        return ""

    def _scope_name(self, node, content: bytes, field: str) -> Optional[str]:
        """The type that a scope node such as Rust `impl<T> Box<T>` belongs to: `Box`."""
        target = node.child_by_field_name(field)
        while target is not None:
            if target.type == "type_identifier":
                return self._get_node_text(target, content).strip()
            if target.type == "scoped_type_identifier":
                target = target.child_by_field_name("name")
            elif target.type in ("generic_type", "reference_type", "pointer_type"):
                target = target.child_by_field_name("type")
            else:
                break
        return None

    def _get_body_node(self, node, config: Dict):
        for child in node.children:
            if child.type in ("block", "body", "compound_statement", "statement_block"):
                return child
        if hasattr(node, "child_by_field_name"):
            body = node.child_by_field_name(config.get("body_field", "body"))
            if body:
                return body
        return None

    def _get_node_name(self, node, content: bytes, config: Dict) -> Optional[str]:
        if hasattr(node, "child_by_field_name"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._get_node_text(name_node, content).strip()
        for child in node.children:
            if child.type in (
                "identifier", "name", "type_identifier",
                "field_identifier", "property_identifier",
            ):
                return self._get_node_text(child, content).strip()
        for child in node.children:
            if child.type in ("declarator", "function_declarator"):
                return self._get_node_name(child, content, config)
        return None

    def _get_node_text(self, node, content: bytes) -> str:
        try:
            return content[node.start_byte:node.end_byte].decode("utf-8")
        except UnicodeDecodeError:
            return content[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

    def _get_code_snippet(self, node, content: bytes) -> str:
        text = self._get_node_text(node, content)
        if len(text) > self.MAX_CODE_SNIPPET_CHARS:
            return text[: self.MAX_CODE_SNIPPET_CHARS] + "..."
        return text

    def _clean_docstring(self, raw: str) -> str:
        cleaned = raw.strip()
        for q in ('"""', "'''"):
            if cleaned.startswith(q) and cleaned.endswith(q):
                cleaned = cleaned[3:-3]
                break
        for q in ('"', "'"):
            if cleaned.startswith(q) and cleaned.endswith(q):
                cleaned = cleaned[1:-1]
                break
        cleaned = cleaned.strip()
        lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
        return lines[0] if lines else cleaned


class ThreadLocalParserFactory:
    """Thread-local parser factory for safe parallel parsing."""

    def __init__(self):
        self._local = threading.local()

    def get_parser(self) -> TreeSitterParser:
        if not hasattr(self._local, "parser"):
            self._local.parser = TreeSitterParser()
        return self._local.parser
