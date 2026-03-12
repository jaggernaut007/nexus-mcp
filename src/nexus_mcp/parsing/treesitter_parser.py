"""Tree-sitter based multi-language code parser.

Ported from CodeGrok MCP. Extracts symbols, imports, and calls from source files.
Supports 9 languages via tree-sitter grammars.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tree_sitter_languages import get_parser

from nexus_mcp.core.interfaces import IParser
from nexus_mcp.core.models import ParsedFile, Symbol, SymbolType
from nexus_mcp.parsing.language_registry import (
    get_config_for_language,
    get_language_for_file,
    get_treesitter_extensions,
)

logger = logging.getLogger(__name__)


class TreeSitterParser(IParser):
    """Production tree-sitter parser. NOT thread-safe per instance."""

    MAX_CODE_SNIPPET_CHARS = 4000
    MAX_FILE_SIZE_MB = 10

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
            tree = parser.parse(content)
            symbols = self._extract_symbols(
                tree.root_node, content, filepath, language, config
            )
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
            self._parsers[language] = get_parser(language)
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

        def traverse(node, depth: int = 0):
            nonlocal current_class
            node_type = node.type

            if node_type in class_types:
                sym = self._extract_class_symbol(node, content, filepath, language, config)
                if sym:
                    symbols.append(sym)
                    class_stack.append(sym.name)
                    current_class = sym.name

            elif node_type in function_types and not current_class:
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

            if node_type in class_types and class_stack:
                class_stack.pop()
                current_class = class_stack[-1] if class_stack else None

        traverse(root_node)
        return symbols

    def _extract_class_symbol(
        self, node, content: bytes, filepath: str, language: str, config: Dict
    ) -> Optional[Symbol]:
        name = self._get_node_name(node, content, config)
        if not name:
            return None
        return Symbol(
            name=name,
            type=SymbolType.CLASS,
            filepath=filepath,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=language,
            signature=self._get_node_text(node, content).split("\n")[0].strip(),
            docstring=self._extract_docstring(node, content, config),
            code_snippet=self._get_code_snippet(node, content),
            imports=self._extract_imports_from_node(node, content, config),
            calls=self._extract_calls_from_node(node, content, config),
        )

    def _extract_function_symbol(
        self, node, content: bytes, filepath: str, language: str, config: Dict,
        parent: Optional[str] = None, is_method: bool = False,
    ) -> Optional[Symbol]:
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
            imports=self._extract_imports_from_node(node, content, config),
            calls=self._extract_calls_from_node(node, content, config),
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
