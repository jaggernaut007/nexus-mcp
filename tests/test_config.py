"""Tests for config.py."""

import pytest

from nexus_mcp.config import Settings, get_settings, reset_settings


@pytest.fixture(autouse=True)
def clean_settings():
    reset_settings()
    yield
    reset_settings()


def test_default_settings():
    s = Settings()
    assert s.embedding_model == "jina-code"
    assert s.embedding_device == "auto"
    assert s.embedding_batch_size == 32
    assert s.max_memory_mb == 350
    assert s.log_level == "INFO"


def test_storage_paths():
    s = Settings()
    assert str(s.storage_path) == ".nexus"
    assert "lancedb" in str(s.lancedb_path)
    assert "graph.db" in str(s.graph_path)


def test_env_override(monkeypatch):
    monkeypatch.setenv("NEXUS_EMBEDDING_MODEL", "granite-embedding-small")
    monkeypatch.setenv("NEXUS_EMBEDDING_BATCH_SIZE", "64")
    monkeypatch.setenv("NEXUS_LOG_LEVEL", "DEBUG")
    s = Settings()
    assert s.embedding_model == "granite-embedding-small"
    assert s.embedding_batch_size == 64
    assert s.log_level == "DEBUG"


def test_env_invalid_int_keeps_default(monkeypatch):
    monkeypatch.setenv("NEXUS_EMBEDDING_BATCH_SIZE", "not_a_number")
    s = Settings()
    assert s.embedding_batch_size == 32  # default


def test_singleton():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_reset_singleton():
    s1 = get_settings()
    reset_settings()
    s2 = get_settings()
    assert s1 is not s2
