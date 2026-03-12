"""Tests for LanceDB BM25 full-text search engine."""

from unittest.mock import MagicMock

import pytest

from nexus_mcp.engines.bm25_engine import LanceDBBM25Engine
from nexus_mcp.engines.vector_engine import LanceDBVectorEngine

VECTOR_DIMS = 4


def _mock_embedding_service(dims=VECTOR_DIMS):
    """Create a mock EmbeddingService that returns deterministic vectors."""
    svc = MagicMock()
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.return_value = [[0.1] * dims]
    return svc


def _make_chunk(id_: str, text: str = "hello world", filepath: str = "/a.py",
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
def db_path(tmp_path):
    return str(tmp_path / "lancedb")


@pytest.fixture
def vector_engine(db_path):
    """Create a vector engine that owns the chunks table."""
    svc = _mock_embedding_service()
    return LanceDBVectorEngine(
        db_path=db_path, embedding_service=svc, vector_dims=VECTOR_DIMS,
    )


@pytest.fixture
def bm25_engine(db_path):
    """Create a BM25 engine pointing at the same DB."""
    return LanceDBBM25Engine(db_path=db_path)


@pytest.fixture
def populated_engines(vector_engine, bm25_engine):
    """Vector engine with data + BM25 engine on same table."""
    chunks = [
        _make_chunk("a", text="the quick brown fox jumps over the lazy dog"),
        _make_chunk("b", text="hello world python function example"),
        _make_chunk("c", text="rust memory safety borrow checker", language="rust"),
        _make_chunk("d", text="javascript async await promise callback", language="javascript",
                    symbol_type="class"),
    ]
    vector_engine.add(chunks)
    bm25_engine.ensure_fts_index()
    return vector_engine, bm25_engine


class TestCreate:
    def test_create_engine(self, bm25_engine):
        assert bm25_engine is not None

    def test_count_no_table(self, bm25_engine):
        assert bm25_engine.count() == 0

    def test_count_delegates_to_table(self, populated_engines):
        _, bm25 = populated_engines
        assert bm25.count() == 4


class TestFTSIndex:
    def test_ensure_fts_index_no_table(self, bm25_engine):
        assert bm25_engine.ensure_fts_index() is False

    def test_ensure_fts_index_success(self, vector_engine, bm25_engine):
        vector_engine.add([_make_chunk("a")])
        assert bm25_engine.ensure_fts_index() is True
        assert bm25_engine._fts_index_created is True

    def test_ensure_fts_index_idempotent(self, vector_engine, bm25_engine):
        vector_engine.add([_make_chunk("a")])
        assert bm25_engine.ensure_fts_index() is True
        assert bm25_engine.ensure_fts_index() is True


class TestSearch:
    def test_search_basic(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("python function")
        assert len(results) >= 1
        assert "score" in results[0]
        assert results[0]["score"] > 0

    def test_search_relevance(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("quick brown fox")
        assert len(results) >= 1
        assert results[0]["id"] == "a"

    def test_search_limit(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("the", limit=2)
        assert len(results) <= 2

    def test_search_empty_table(self, vector_engine, bm25_engine):
        results = bm25_engine.search("anything")
        assert results == []

    def test_search_no_table(self, bm25_engine):
        results = bm25_engine.search("anything")
        assert results == []

    def test_search_no_score_field_leak(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("python")
        if results:
            assert "_score" not in results[0]
            assert "_distance" not in results[0]

    def test_search_language_filter(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("memory safety", language="rust")
        assert len(results) >= 1
        assert all(r["language"] == "rust" for r in results)

    def test_search_symbol_type_filter(self, populated_engines):
        _, bm25 = populated_engines
        results = bm25.search("async await", symbol_type="class")
        assert len(results) >= 1
        assert all(r["symbol_type"] == "class" for r in results)


class TestNoOps:
    def test_add_is_noop(self, bm25_engine):
        bm25_engine.add([_make_chunk("a")])
        assert bm25_engine.count() == 0

    def test_delete_is_noop(self, populated_engines):
        _, bm25 = populated_engines
        count_before = bm25.count()
        bm25.delete(["a"])
        assert bm25.count() == count_before

    def test_clear_resets_fts_flag(self, populated_engines):
        _, bm25 = populated_engines
        assert bm25._fts_index_created is True
        bm25.clear()
        assert bm25._fts_index_created is False


class TestSharedTable:
    def test_bm25_sees_vector_data(self, vector_engine, bm25_engine):
        """BM25 engine reads data written by vector engine."""
        vector_engine.add([_make_chunk("x", text="shared table test")])
        bm25_engine.ensure_fts_index()
        results = bm25_engine.search("shared table")
        assert len(results) >= 1
        assert results[0]["id"] == "x"

    def test_bm25_reflects_vector_deletes(self, vector_engine, bm25_engine):
        """BM25 engine sees deletes made by vector engine."""
        vector_engine.add([
            _make_chunk("x", text="keep this one"),
            _make_chunk("y", text="delete this one"),
        ])
        bm25_engine.ensure_fts_index()
        vector_engine.delete(["y"])
        # Need to recreate FTS index after vector engine modifies table
        bm25_engine._fts_index_created = False
        bm25_engine.ensure_fts_index()
        assert bm25_engine.count() == 1
