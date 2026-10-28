"""Audit tests for core_api.restore_session: multi-root, stale graph, bad metadata."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.indexing.embedding_service import model_dimensions
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state


def _pipeline():
    dims = model_dimensions(Settings().embedding_model)
    svc = MagicMock()
    svc.model_name = Settings().embedding_model
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service", return_value=svc):
        pipeline = IndexingPipeline(Settings())
    pipeline._vector_engine._embedding_service = svc
    return pipeline


@pytest.fixture
def roots(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "x.py").write_text("from y import bx\n\n\ndef ax():\n    return bx()\n")
    (b / "y.py").write_text("def bx():\n    return 1\n")
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.chdir(tmp_path)  # restore only accepts roots inside the working directory
    reset_settings()
    return a, b


def _new_process(monkeypatch):
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    reset_settings()


def test_restore_session_multi_root_attaches_every_root(roots, monkeypatch):
    a, b = roots
    _pipeline().multi_index([a, b])
    _new_process(monkeypatch)

    assert core_api.restore_session() is True
    state = get_state()
    assert [p.resolve() for p in state.codebase_paths] == [a.resolve(), b.resolve()]
    assert state.codebase_path.resolve() == a.resolve()
    assert state.graph_engine.nodes  # the graph was loaded, not left empty


def test_restore_session_multi_root_refuses_when_one_root_is_gone(roots, monkeypatch):
    import shutil

    a, b = roots
    _pipeline().multi_index([a, b])
    shutil.rmtree(b)
    _new_process(monkeypatch)

    assert core_api.restore_session() is False
    assert get_state().is_indexed is False


def test_restore_session_after_incremental_reindex_without_shutdown_refuses(roots, monkeypatch):
    """Incremental reindex saves metadata but not the graph, so a crash leaves a stale graph."""
    a, _b = roots
    _pipeline().index(a)
    time.sleep(0.05)
    (a / "x.py").write_text("def ax():\n    return 2\n")
    _pipeline().incremental_index(a)  # new process, no shutdown() afterwards
    _new_process(monkeypatch)

    assert core_api.restore_session() is False


def test_restore_session_corrupt_metadata_json_returns_false(roots, monkeypatch, tmp_path):
    a, _b = roots
    _pipeline().index(a)
    (tmp_path / "storage" / "index_metadata.json").write_text("{not json")
    _new_process(monkeypatch)

    assert core_api.restore_session() is False


def test_restore_session_metadata_without_a_root_returns_false(roots, monkeypatch, tmp_path):
    a, _b = roots
    _pipeline().index(a)
    meta_path = tmp_path / "storage" / "index_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.pop("codebase_path")
    meta.pop("codebase_paths", None)
    meta_path.write_text(json.dumps(meta))
    _new_process(monkeypatch)

    assert core_api.restore_session() is False


def test_restore_session_metadata_that_is_not_an_object_returns_false(
    roots, monkeypatch, tmp_path
):
    a, _b = roots
    _pipeline().index(a)
    (tmp_path / "storage" / "index_metadata.json").write_text("[1, 2]")
    _new_process(monkeypatch)

    assert core_api.restore_session() is False
    assert core_api.status()["indexed"] is False  # must not raise
