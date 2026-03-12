"""Tests for LanceDB-backed memory store."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from nexus_mcp.core.models import Memory, MemoryType
from nexus_mcp.memory.memory_store import MemoryStore

VECTOR_DIMS = 4


def _mock_embedding_service(dims=VECTOR_DIMS):
    svc = MagicMock()
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.return_value = [[0.1] * dims]
    return svc


def _make_memory(
    content="test content",
    memory_type=MemoryType.NOTE,
    project="test-project",
    tags=None,
    ttl="permanent",
):
    return Memory(
        id=str(uuid.uuid4())[:8],
        content=content,
        memory_type=memory_type,
        project=project,
        tags=tags or [],
        ttl=ttl,
    )


@pytest.fixture
def store(tmp_path):
    svc = _mock_embedding_service()
    return MemoryStore(
        db_path=str(tmp_path / "lancedb"),
        embedding_service=svc,
        vector_dims=VECTOR_DIMS,
    )


class TestRemember:
    def test_remember_single(self, store):
        mem = _make_memory(content="hello world")
        mem_id = store.remember(mem)
        assert mem_id == mem.id
        assert store.count() == 1

    def test_remember_multiple(self, store):
        for i in range(3):
            store.remember(_make_memory(content=f"memory {i}"))
        assert store.count() == 3

    def test_remember_with_tags(self, store):
        mem = _make_memory(content="tagged memory", tags=["auth", "important"])
        store.remember(mem)
        assert store.count() == 1

    def test_remember_with_metadata(self, store):
        mem = _make_memory(content="meta memory")
        mem.metadata = {"key": "value"}
        store.remember(mem)
        results = store.recall("meta memory")
        assert len(results) >= 1


class TestRecall:
    def test_recall_basic(self, store):
        store.remember(_make_memory(content="python async await"))
        results = store.recall("python async")
        assert len(results) >= 1
        assert results[0].content == "python async await"

    def test_recall_limit(self, store):
        for i in range(5):
            store.remember(_make_memory(content=f"memory number {i}"))
        results = store.recall("memory", limit=2)
        assert len(results) <= 2

    def test_recall_empty_store(self, store):
        results = store.recall("anything")
        assert results == []

    def test_recall_filter_by_type(self, store):
        store.remember(_make_memory(content="a note", memory_type=MemoryType.NOTE))
        store.remember(_make_memory(content="a decision", memory_type=MemoryType.DECISION))
        results = store.recall("memory", memory_type="note")
        assert all(r.memory_type == MemoryType.NOTE for r in results)

    def test_recall_filter_by_tags(self, store):
        store.remember(_make_memory(content="auth related", tags=["auth"]))
        store.remember(_make_memory(content="no tags"))
        results = store.recall("memory", tags=["auth"])
        assert len(results) >= 1
        assert any("auth" in r.tags for r in results)

    def test_recall_filter_by_project(self, store):
        store.remember(_make_memory(content="proj A", project="project-a"))
        store.remember(_make_memory(content="proj B", project="project-b"))
        results = store.recall("project", project="project-a")
        assert all(r.project == "project-a" for r in results)

    def test_recall_touches_accessed_at(self, store):
        mem = _make_memory(content="touch test")
        original_accessed = mem.accessed_at
        store.remember(mem)
        results = store.recall("touch test")
        assert len(results) >= 1
        # accessed_at should be updated
        assert results[0].accessed_at >= original_accessed


class TestForget:
    def test_forget_by_id(self, store):
        mem = _make_memory(content="to forget")
        store.remember(mem)
        assert store.count() == 1
        deleted = store.forget(memory_id=mem.id)
        assert deleted == 1
        assert store.count() == 0

    def test_forget_by_type(self, store):
        store.remember(_make_memory(content="note 1", memory_type=MemoryType.NOTE))
        store.remember(_make_memory(content="note 2", memory_type=MemoryType.NOTE))
        store.remember(_make_memory(content="decision", memory_type=MemoryType.DECISION))
        deleted = store.forget(memory_type="note")
        assert deleted == 2
        assert store.count() == 1

    def test_forget_by_tags(self, store):
        store.remember(_make_memory(content="tagged", tags=["temp"]))
        store.remember(_make_memory(content="not tagged"))
        deleted = store.forget(tags=["temp"])
        assert deleted == 1
        assert store.count() == 1

    def test_forget_empty_store(self, store):
        deleted = store.forget(memory_id="nonexistent")
        assert deleted == 0

    def test_forget_no_criteria(self, store):
        store.remember(_make_memory(content="keep me"))
        deleted = store.forget()  # No criteria = no-op
        assert deleted == 0
        assert store.count() == 1


class TestTTLExpiration:
    def test_expire_permanent_not_deleted(self, store):
        store.remember(_make_memory(content="permanent", ttl="permanent"))
        expired = store.expire_ttl()
        assert expired == 0
        assert store.count() == 1

    def test_expire_old_day_ttl(self, store):
        mem = _make_memory(content="old daily", ttl="day")
        mem.created_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        store.remember(mem)
        expired = store.expire_ttl()
        assert expired == 1
        assert store.count() == 0

    def test_expire_fresh_day_ttl_not_deleted(self, store):
        mem = _make_memory(content="fresh daily", ttl="day")
        # created_at is now by default, so within TTL
        store.remember(mem)
        expired = store.expire_ttl()
        assert expired == 0
        assert store.count() == 1


class TestPersistence:
    def test_data_persists(self, tmp_path):
        db_path = str(tmp_path / "lancedb")
        svc = _mock_embedding_service()

        store1 = MemoryStore(db_path=db_path, embedding_service=svc, vector_dims=VECTOR_DIMS)
        store1.remember(_make_memory(content="persistent"))
        assert store1.count() == 1

        store2 = MemoryStore(db_path=db_path, embedding_service=svc, vector_dims=VECTOR_DIMS)
        assert store2.count() == 1


class TestClear:
    def test_clear(self, store):
        store.remember(_make_memory(content="to clear"))
        assert store.count() == 1
        store.clear()
        assert store.count() == 0

    def test_clear_empty(self, store):
        store.clear()
        assert store.count() == 0
