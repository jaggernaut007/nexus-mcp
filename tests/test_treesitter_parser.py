"""Tests for parsing/treesitter_parser.py."""


import pytest

from nexus_mcp.core.models import SymbolType
from nexus_mcp.parsing.treesitter_parser import ThreadLocalParserFactory, TreeSitterParser


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def python_file(tmp_path):
    code = '''
"""Module docstring."""

import os
from pathlib import Path

MAX_SIZE = 100

class Calculator:
    """A calculator class."""

    def add(self, a, b):
        """Add two numbers."""
        return a + b

    def subtract(self, a, b):
        return a - b

def standalone_function(x):
    """A standalone function."""
    result = x * 2
    print(result)
    return result
'''
    f = tmp_path / "calculator.py"
    f.write_text(code)
    return f


@pytest.fixture
def js_file(tmp_path):
    code = '''
const MAX_RETRIES = 3;

function greet(name) {
    console.log("Hello " + name);
}

class Animal {
    constructor(name) {
        this.name = name;
    }

    speak() {
        return this.name + " speaks";
    }
}
'''
    f = tmp_path / "app.js"
    f.write_text(code)
    return f


def test_can_parse_python(parser):
    assert parser.can_parse("test.py")
    assert parser.can_parse("test.pyi")


def test_can_parse_javascript(parser):
    assert parser.can_parse("test.js")
    assert parser.can_parse("test.jsx")


def test_cannot_parse_unsupported(parser):
    assert not parser.can_parse("test.txt")
    assert not parser.can_parse("test.csv")


def test_parse_python_file(parser, python_file):
    result = parser.parse_file(str(python_file))
    assert result.is_successful
    assert result.language == "python"
    assert len(result.symbols) >= 3  # Calculator, add, subtract, standalone

    # Check we found the class
    classes = result.get_symbols_by_type(SymbolType.CLASS)
    assert len(classes) == 1
    assert classes[0].name == "Calculator"

    # Check we found functions
    functions = result.get_symbols_by_type(SymbolType.FUNCTION)
    assert any(f.name == "standalone_function" for f in functions)

    # Check methods
    methods = result.get_symbols_by_type(SymbolType.METHOD)
    method_names = {m.name for m in methods}
    assert "add" in method_names
    assert "subtract" in method_names

    # Check imports
    assert len(result.imports) >= 2


def test_parse_js_file(parser, js_file):
    result = parser.parse_file(str(js_file))
    assert result.is_successful
    assert result.language == "javascript"

    # Should find function and class
    functions = result.get_symbols_by_type(SymbolType.FUNCTION)
    assert any(f.name == "greet" for f in functions)

    classes = result.get_symbols_by_type(SymbolType.CLASS)
    assert any(c.name == "Animal" for c in classes)


def test_parse_nonexistent_file(parser):
    with pytest.raises(FileNotFoundError):
        parser.parse_file("/nonexistent/file.py")


def test_parse_unsupported_extension(parser, tmp_path):
    f = tmp_path / "readme.txt"
    f.write_text("hello")
    result = parser.parse_file(str(f))
    assert not result.is_successful
    assert "Unsupported" in result.error


def test_parse_binary_file(parser, tmp_path):
    f = tmp_path / "binary.py"
    f.write_bytes(b"\x00\x01\x02\x03\x04\x05")
    result = parser.parse_file(str(f))
    assert not result.is_successful
    assert "Binary" in result.error


def test_code_snippet_truncation(parser, tmp_path):
    # Create a file with a very long function
    long_body = "\n".join([f"    x_{i} = {i}" for i in range(500)])
    code = f"def long_func():\n{long_body}\n    return x_0\n"
    f = tmp_path / "long.py"
    f.write_text(code)
    result = parser.parse_file(str(f))
    assert result.is_successful
    for sym in result.symbols:
        assert len(sym.code_snippet) <= TreeSitterParser.MAX_CODE_SNIPPET_CHARS + 3  # "..."


def test_supported_extensions(parser):
    exts = parser.get_supported_extensions()
    assert ".py" in exts
    assert ".js" in exts
    assert ".go" in exts
    assert isinstance(exts, list)
    assert exts == sorted(exts)


def test_thread_local_factory():
    factory = ThreadLocalParserFactory()
    p1 = factory.get_parser()
    p2 = factory.get_parser()
    assert p1 is p2  # Same thread, same parser
    assert isinstance(p1, TreeSitterParser)
