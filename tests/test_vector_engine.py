"""Tests for LanceDB vector engine."""

from unittest.mock import MagicMock

import pytest

from nexus_mcp.engines.vector_engine import LanceDBVectorEngine

VECTOR_DIMS = 4  # Small dims for fast tests


def _mock_embedding_service(dims=VECTOR_DIMS):
    """Create a mock EmbeddingService that returns deterministic vectors."""
    svc = MagicMock()
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.return_value = [[0.1] * dims]
    return svc


def _make_chunk(id_: str, text: str = "hello", filepath: str = "/a.py",
                language: str = "python", symbol_type: str = "function",
                vector=None):
    """Create a chunk dict for testing."""
    return {
        "id": id_,
        "vector": vector or [0.1] * VECTOR_DIMS,
        "text": text,
        "filepath": filepath,
        "symbol_name": f"func_{id_}",
        "symbol_type": symbol_type,
        "language": language,
        "line_start": 1,
        "line_end": 10,
        "signature": f"def func_{id_}():",
        "parent": "",
        "docstring": "",
    }


@pytest.fixture
def engine(tmp_path):
    """Create a fresh LanceDBVectorEngine."""
    svc = _mock_embedding_service()
    return LanceDBVectorEngine(
        db_path=str(tmp_path / "lancedb"),
        embedding_service=svc,
        vector_dims=VECTOR_DIMS,
    )


class TestCreate:
    def test_create_engine(self, engine):
        assert engine is not None

    def test_count_empty(self, engine):
        assert engine.count() == 0


class TestAdd:
    def test_add_single(self, engine):
        engine.add([_make_chunk("a")])
        assert engine.count() == 1

    def test_add_batch(self, engine):
        chunks = [_make_chunk(f"c{i}") for i in range(5)]
        engine.add(chunks)
        assert engine.count() == 5

    def test_add_empty(self, engine):
        engine.add([])
        assert engine.count() == 0


class TestSearch:
    def test_search_basic(self, engine):
        engine.add([_make_chunk("a", text="hello world")])
        results = engine.search("hello")
        assert len(results) == 1
        assert results[0]["id"] == "a"
        assert "score" in results[0]
        assert results[0]["score"] > 0

    def test_search_limit(self, engine):
        engine.add([_make_chunk(f"c{i}") for i in range(5)])
        results = engine.search("test", limit=2)
        assert len(results) == 2

    def test_search_empty_table(self, engine):
        results = engine.search("anything")
        assert results == []

    def test_search_no_distance_field(self, engine):
        engine.add([_make_chunk("a")])
        results = engine.search("test")
        assert "_distance" not in results[0]

    def test_search_language_filter(self, engine):
        engine.add([
            _make_chunk("py", language="python"),
            _make_chunk("js", language="javascript"),
        ])
        results = engine.search("test", language="python")
        assert len(results) == 1
        assert results[0]["language"] == "python"

    def test_search_symbol_type_filter(self, engine):
        engine.add([
            _make_chunk("f", symbol_type="function"),
            _make_chunk("c", symbol_type="class"),
        ])
        results = engine.search("test", symbol_type="class")
        assert len(results) == 1
        assert results[0]["symbol_type"] == "class"


class TestDelete:
    def test_delete_by_id(self, engine):
        engine.add([_make_chunk("a"), _make_chunk("b"), _make_chunk("c")])
        engine.delete(["b"])
        assert engine.count() == 2

    def test_delete_empty_list(self, engine):
        engine.add([_make_chunk("a")])
        engine.delete([])
        assert engine.count() == 1

    def test_delete_by_filepath(self, engine):
        engine.add([
            _make_chunk("a1", filepath="/file1.py"),
            _make_chunk("a2", filepath="/file1.py"),
            _make_chunk("b1", filepath="/file2.py"),
        ])
        engine.delete_by_filepath("/file1.py")
        assert engine.count() == 1


class TestUpsert:
    def test_upsert_new(self, engine):
        engine.upsert([_make_chunk("a")])
        assert engine.count() == 1

    def test_upsert_existing(self, engine):
        engine.add([_make_chunk("a", text="old")])
        engine.upsert([_make_chunk("a", text="new")])
        assert engine.count() == 1
        results = engine.search("test", limit=1)
        assert results[0]["text"] == "new"

    def test_upsert_empty(self, engine):
        engine.upsert([])
        assert engine.count() == 0


class TestClear:
    def test_clear(self, engine):
        engine.add([_make_chunk("a"), _make_chunk("b")])
        assert engine.count() == 2
        engine.clear()
        assert engine.count() == 0

    def test_clear_empty(self, engine):
        engine.clear()
        assert engine.count() == 0


class TestPersistence:
    def test_table_persists(self, tmp_path):
        svc = _mock_embedding_service()
        db_path = str(tmp_path / "lancedb")

        # Create engine and add data
        e1 = LanceDBVectorEngine(db_path=db_path, embedding_service=svc, vector_dims=VECTOR_DIMS)
        e1.add([_make_chunk("a")])
        assert e1.count() == 1

        # Create new engine on same path
        e2 = LanceDBVectorEngine(db_path=db_path, embedding_service=svc, vector_dims=VECTOR_DIMS)
        assert e2.count() == 1
