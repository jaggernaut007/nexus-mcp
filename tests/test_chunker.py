"""Tests for Symbol-to-CodeChunk conversion."""


from nexus_mcp.core.models import Symbol, SymbolType
from nexus_mcp.indexing.chunker import (
    CodeChunk,
    _generate_chunk_id,
    create_chunk,
    create_chunk_text,
    create_chunks,
)


def _make_symbol(**overrides) -> Symbol:
    """Create a test Symbol with sensible defaults."""
    defaults = {
        "name": "my_func",
        "type": SymbolType.FUNCTION,
        "filepath": "/src/example.py",
        "line_start": 10,
        "line_end": 20,
        "language": "python",
        "signature": "def my_func(x: int) -> str:",
        "docstring": "A test function.",
        "code_snippet": "def my_func(x: int) -> str:\n    return str(x)",
    }
    defaults.update(overrides)
    return Symbol(**defaults)


class TestGenerateChunkId:
    def test_deterministic(self):
        id1 = _generate_chunk_id("/a.py", "func", 10)
        id2 = _generate_chunk_id("/a.py", "func", 10)
        assert id1 == id2

    def test_unique_different_name(self):
        id1 = _generate_chunk_id("/a.py", "func_a", 10)
        id2 = _generate_chunk_id("/a.py", "func_b", 10)
        assert id1 != id2

    def test_unique_different_file(self):
        id1 = _generate_chunk_id("/a.py", "func", 10)
        id2 = _generate_chunk_id("/b.py", "func", 10)
        assert id1 != id2

    def test_unique_different_line(self):
        id1 = _generate_chunk_id("/a.py", "func", 10)
        id2 = _generate_chunk_id("/a.py", "func", 20)
        assert id1 != id2

    def test_length(self):
        chunk_id = _generate_chunk_id("/a.py", "func", 1)
        assert len(chunk_id) == 16


class TestCreateChunkText:
    def test_basic(self):
        sym = _make_symbol()
        text = create_chunk_text(sym)
        assert "/src/example.py:10" in text
        assert "function: my_func" in text
        assert "def my_func(x: int) -> str:" in text
        assert "A test function." in text
        assert "return str(x)" in text

    def test_no_docstring(self):
        sym = _make_symbol(docstring="")
        text = create_chunk_text(sym)
        assert "function: my_func" in text
        # No empty docstring section
        assert text.count("\n\n\n") == 0

    def test_with_imports_and_calls(self):
        sym = _make_symbol(imports=["os", "sys"], calls=["print", "len"])
        text = create_chunk_text(sym)
        assert "Imports: os, sys" in text
        assert "Calls: print, len" in text

    def test_no_imports_or_calls(self):
        sym = _make_symbol(imports=[], calls=[])
        text = create_chunk_text(sym)
        assert "Imports:" not in text
        assert "Calls:" not in text

    def test_truncation(self):
        long_snippet = "x" * 10000
        sym = _make_symbol(code_snippet=long_snippet)
        text = create_chunk_text(sym)
        # Should be truncated to chunk_max_chars (default 4000)
        assert len(text) < 10000

    def test_method_with_parent(self):
        sym = _make_symbol(
            name="do_thing",
            type=SymbolType.METHOD,
            parent="MyClass",
            signature="def do_thing(self):",
        )
        text = create_chunk_text(sym)
        assert "method: MyClass.do_thing" in text

    def test_no_signature(self):
        sym = _make_symbol(signature="")
        text = create_chunk_text(sym)
        assert "function: my_func" in text


class TestCreateChunk:
    def test_fields(self):
        sym = _make_symbol()
        chunk = create_chunk(sym)
        assert chunk.filepath == "/src/example.py"
        assert chunk.symbol_name == "my_func"
        assert chunk.symbol_type == "function"
        assert chunk.language == "python"
        assert chunk.line_start == 10
        assert chunk.line_end == 20
        assert chunk.signature == "def my_func(x: int) -> str:"
        assert chunk.docstring == "A test function."
        assert chunk.parent == ""
        assert chunk.vector == []

    def test_with_parent(self):
        sym = _make_symbol(name="method", type=SymbolType.METHOD, parent="Klass")
        chunk = create_chunk(sym)
        assert chunk.parent == "Klass"
        assert chunk.symbol_name == "Klass.method"

    def test_no_parent_defaults_empty(self):
        sym = _make_symbol(parent=None)
        chunk = create_chunk(sym)
        assert chunk.parent == ""

    def test_id_is_deterministic(self):
        sym = _make_symbol()
        c1 = create_chunk(sym)
        c2 = create_chunk(sym)
        assert c1.id == c2.id

    def test_text_is_populated(self):
        sym = _make_symbol()
        chunk = create_chunk(sym)
        assert len(chunk.text) > 0
        assert "my_func" in chunk.text

    def test_docstring_truncated(self):
        long_doc = "d" * 1000
        sym = _make_symbol(docstring=long_doc)
        chunk = create_chunk(sym)
        assert len(chunk.docstring) == 500


class TestCreateChunks:
    def test_batch(self):
        syms = [
            _make_symbol(name="a", line_start=1, line_end=5),
            _make_symbol(name="b", line_start=10, line_end=15),
            _make_symbol(name="c", line_start=20, line_end=25),
        ]
        chunks = create_chunks(syms)
        assert len(chunks) == 3
        assert [c.symbol_name for c in chunks] == ["a", "b", "c"]

    def test_empty(self):
        assert create_chunks([]) == []


class TestCodeChunkToDict:
    def test_to_dict(self):
        sym = _make_symbol()
        chunk = create_chunk(sym)
        d = chunk.to_dict()
        assert d["id"] == chunk.id
        assert d["text"] == chunk.text
        assert d["vector"] == []
        assert d["filepath"] == "/src/example.py"
        assert d["symbol_name"] == "my_func"
        assert d["symbol_type"] == "function"
        assert d["language"] == "python"
        assert d["line_start"] == 10
        assert d["line_end"] == 20

    def test_to_dict_has_all_fields(self):
        chunk = CodeChunk(
            id="abc",
            text="hello",
            filepath="/a.py",
            symbol_name="f",
            symbol_type="function",
            language="python",
            line_start=1,
            line_end=5,
            signature="def f():",
            parent="C",
            docstring="doc",
            vector=[0.1, 0.2],
        )
        d = chunk.to_dict()
        expected_keys = {
            "id", "text", "vector", "filepath", "symbol_name",
            "symbol_type", "language", "line_start", "line_end",
            "signature", "parent", "docstring",
        }
        assert set(d.keys()) == expected_keys
        assert d["vector"] == [0.1, 0.2]
