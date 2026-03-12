"""Tests for core/models.py — Symbol, ParsedFile, CodebaseIndex, Memory."""

import pytest

from nexus_mcp.core.models import (
    CodebaseIndex,
    Memory,
    MemoryType,
    ParsedFile,
    Symbol,
    SymbolType,
)

# --- SymbolType ---

def test_symbol_type_values():
    assert SymbolType.FUNCTION.value == "function"
    assert SymbolType.CLASS.value == "class"
    assert SymbolType.METHOD.value == "method"
    assert SymbolType.VARIABLE.value == "variable"


def test_symbol_type_from_string():
    assert SymbolType.from_string("function") == SymbolType.FUNCTION
    assert SymbolType.from_string("CLASS") == SymbolType.CLASS


def test_symbol_type_from_string_invalid():
    with pytest.raises(ValueError, match="Invalid SymbolType"):
        SymbolType.from_string("invalid")


def test_symbol_type_str():
    assert str(SymbolType.FUNCTION) == "function"


# --- Symbol ---

def _make_symbol(**overrides):
    defaults = {
        "name": "test_func",
        "type": SymbolType.FUNCTION,
        "filepath": "/test.py",
        "line_start": 1,
        "line_end": 10,
        "language": "python",
        "signature": "def test_func():",
    }
    defaults.update(overrides)
    return Symbol(**defaults)


def test_symbol_creation():
    sym = _make_symbol()
    assert sym.name == "test_func"
    assert sym.type == SymbolType.FUNCTION
    assert sym.line_count == 10


def test_symbol_qualified_name_no_parent():
    sym = _make_symbol()
    assert sym.qualified_name == "test_func"


def test_symbol_qualified_name_with_parent():
    sym = _make_symbol(name="method", parent="MyClass", type=SymbolType.METHOD)
    assert sym.qualified_name == "MyClass.method"


def test_symbol_to_dict_from_dict():
    sym = _make_symbol(docstring="A test", imports=["os"], calls=["print"])
    d = sym.to_dict()
    assert d["type"] == "function"
    restored = Symbol.from_dict(d)
    assert restored.name == sym.name
    assert restored.type == sym.type
    assert restored.imports == ["os"]


def test_symbol_empty_name_raises():
    with pytest.raises(ValueError, match="name cannot be empty"):
        _make_symbol(name="")


def test_symbol_empty_filepath_raises():
    with pytest.raises(ValueError, match="filepath cannot be empty"):
        _make_symbol(filepath="")


def test_symbol_invalid_lines_raises():
    with pytest.raises(ValueError, match="line_start must be >= 1"):
        _make_symbol(line_start=0)


def test_symbol_line_end_before_start_raises():
    with pytest.raises(ValueError):
        _make_symbol(line_start=5, line_end=3)


def test_symbol_empty_language_raises():
    with pytest.raises(ValueError, match="language cannot be empty"):
        _make_symbol(language="")


def test_symbol_invalid_type_raises():
    with pytest.raises(ValueError, match="type must be SymbolType"):
        _make_symbol(type="not_an_enum")


# --- ParsedFile ---

def test_parsed_file_creation():
    pf = ParsedFile(filepath="/test.py", language="python")
    assert pf.is_successful
    assert pf.symbol_count == 0


def test_parsed_file_with_error():
    pf = ParsedFile(filepath="/test.py", language="python", error="syntax error")
    assert not pf.is_successful


def test_parsed_file_symbols_by_type():
    sym1 = _make_symbol(name="func1")
    sym2 = _make_symbol(name="MyClass", type=SymbolType.CLASS)
    pf = ParsedFile(filepath="/test.py", language="python", symbols=[sym1, sym2])
    assert len(pf.get_symbols_by_type(SymbolType.FUNCTION)) == 1
    assert len(pf.get_symbols_by_type(SymbolType.CLASS)) == 1


def test_parsed_file_to_dict_from_dict():
    sym = _make_symbol()
    pf = ParsedFile(filepath="/test.py", language="python", symbols=[sym])
    d = pf.to_dict()
    restored = ParsedFile.from_dict(d)
    assert restored.filepath == pf.filepath
    assert len(restored.symbols) == 1


def test_parsed_file_empty_filepath_raises():
    with pytest.raises(ValueError):
        ParsedFile(filepath="", language="python")


def test_parsed_file_negative_parse_time_raises():
    with pytest.raises(ValueError):
        ParsedFile(filepath="/test.py", language="python", parse_time=-1.0)


# --- CodebaseIndex ---

def test_codebase_index_creation():
    idx = CodebaseIndex(root_path="/project")
    assert idx.root_path == "/project"
    assert idx.successful_parses == 0
    assert idx.failed_parses == 0


def test_codebase_index_with_files():
    pf1 = ParsedFile(filepath="/a.py", language="python")
    pf2 = ParsedFile(filepath="/b.py", language="python", error="fail")
    idx = CodebaseIndex(
        root_path="/project",
        files={"/a.py": pf1, "/b.py": pf2},
        total_files=2,
    )
    assert idx.successful_parses == 1
    assert idx.failed_parses == 1


def test_codebase_index_search_by_name():
    sym = _make_symbol(name="target_func")
    pf = ParsedFile(filepath="/test.py", language="python", symbols=[sym])
    idx = CodebaseIndex(root_path="/project", files={"/test.py": pf})
    found = idx.get_symbols_by_name("target_func")
    assert len(found) == 1


def test_codebase_index_to_dict_from_dict():
    idx = CodebaseIndex(root_path="/project", total_files=5, total_symbols=20)
    d = idx.to_dict()
    restored = CodebaseIndex.from_dict(d)
    assert restored.root_path == "/project"
    assert restored.total_files == 5


def test_codebase_index_empty_root_raises():
    with pytest.raises(ValueError):
        CodebaseIndex(root_path="")


# --- MemoryType ---

def test_memory_type_from_string():
    assert MemoryType.from_string("conversation") == MemoryType.CONVERSATION
    assert MemoryType.from_string("NOTE") == MemoryType.NOTE


def test_memory_type_from_string_invalid():
    with pytest.raises(ValueError):
        MemoryType.from_string("invalid")


# --- Memory ---

def _make_memory(**overrides):
    defaults = {
        "id": "mem-1",
        "content": "test content",
        "memory_type": MemoryType.NOTE,
        "project": "/project",
    }
    defaults.update(overrides)
    return Memory(**defaults)


def test_memory_creation():
    mem = _make_memory()
    assert mem.id == "mem-1"
    assert mem.ttl == "permanent"
    assert mem.source == "user"


def test_memory_to_dict_from_dict():
    mem = _make_memory(tags=["test"], ttl="week")
    d = mem.to_dict()
    assert d["memory_type"] == "note"
    restored = Memory.from_dict(d)
    assert restored.memory_type == MemoryType.NOTE
    assert restored.tags == ["test"]


def test_memory_touch():
    mem = _make_memory()
    old_time = mem.accessed_at
    mem.touch()
    # Should be same or newer
    assert mem.accessed_at >= old_time


def test_memory_empty_id_raises():
    with pytest.raises(ValueError):
        _make_memory(id="")


def test_memory_empty_content_raises():
    with pytest.raises(ValueError):
        _make_memory(content="")


def test_memory_invalid_ttl_raises():
    with pytest.raises(ValueError, match="ttl must be one of"):
        _make_memory(ttl="forever")
