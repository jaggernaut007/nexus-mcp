"""A new server process must reattach to the index that is already on disk.

Before this, every session began with `status` saying `indexed: false`, so an agent had to
call `index` before any real work, even though the project had been indexed the day before.
"""

from unittest.mock import MagicMock, patch

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.indexing.embedding_service import model_dimensions
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "lib.py").write_text("def helper():\n    return 1\n")
    (root / "app.py").write_text("from lib import helper\n\n\ndef run():\n    helper()\n")
    return root


@pytest.fixture
def indexed_storage(project, tmp_path, monkeypatch):
    """Index the project once, as an earlier session would, then forget all in-memory state."""
    storage = tmp_path / "storage"
    monkeypatch.chdir(tmp_path)  # restore only accepts a project root inside the working dir
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(storage))
    reset_settings()
    dims = model_dimensions(Settings().embedding_model)
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
        svc = MagicMock()
        svc.model_name = Settings().embedding_model
        svc.embed.return_value = [0.1] * dims
        svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
        get_svc.return_value = svc
        pipeline = IndexingPipeline(Settings())
        pipeline._vector_engine._embedding_service = svc
        pipeline.index(project)
    reset_state()
    core_api._pipeline = None
    reset_settings()
    return storage


@pytest.fixture
def restore_on(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    reset_settings()
    yield
    reset_settings()


def test_status_in_a_new_process_reports_the_stored_index(indexed_storage, project, restore_on):
    result = core_api.status()
    assert result["indexed"] is True
    assert result["codebase_path"] == str(project.resolve())
    assert result["graph"]["total_nodes"] > 0


def test_graph_tools_work_in_a_new_process_without_calling_index(
    indexed_storage, restore_on
):
    result = core_api.graph("helper", direction="callers", detail="full")
    assert "error" not in result
    assert [c["name"] for c in result["callers"]] == ["run"]


def test_status_tool_starts_the_file_watcher_for_a_restored_index(indexed_storage, restore_on):
    import asyncio

    import nexus_mcp.server as server_module
    from tests.conftest import _call_tool

    mcp = server_module.create_server()
    result = asyncio.run(_call_tool(mcp, "status"))
    assert result["indexed"] is True
    assert len(get_state()._file_watchers) == 1


def test_restore_is_off_when_disabled(indexed_storage, monkeypatch):
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "false")
    reset_settings()
    assert core_api.status()["indexed"] is False
    assert "error" in core_api.graph("helper")


def test_nothing_to_restore_when_no_index_exists(tmp_path, monkeypatch, restore_on):
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "empty"))
    reset_settings()
    assert core_api.status()["indexed"] is False


def test_restore_refuses_an_index_built_by_another_model(
    indexed_storage, monkeypatch, restore_on
):
    monkeypatch.setenv("NEXUS_EMBEDDING_MODEL", "jina-code")
    reset_settings()
    assert core_api.restore_session() is False
    assert get_state().is_indexed is False


def test_restore_refuses_when_the_project_folder_is_gone(
    indexed_storage, project, restore_on
):
    import shutil

    shutil.rmtree(project)
    assert core_api.restore_session() is False


def test_restore_refuses_when_the_saved_graph_is_missing(indexed_storage, restore_on):
    (indexed_storage / "graph.db").unlink()
    assert core_api.restore_session() is False
    assert get_state().is_indexed is False


def test_restore_is_a_no_op_when_a_codebase_is_already_attached(
    indexed_storage, project, restore_on
):
    assert core_api.restore_session() is True
    engine = get_state().graph_engine
    assert core_api.restore_session() is True
    assert get_state().graph_engine is engine


def test_concurrent_first_calls_all_see_the_restored_index(indexed_storage, restore_on):
    """An agent sends `status` and `search` in one turn: both reach restore at once.

    Found by the live benchmark. The loser of the race used to answer "No codebase indexed.
    Run 'index' first.", and the agent then dropped nexus for Grep. Every caller must wait for
    the one restore that is running and then see the attached index.
    """
    import threading
    import time

    original = IndexingPipeline._restore_graph

    def slow_restore(self):
        time.sleep(0.4)  # keeps the first restore running while the others arrive
        return original(self)

    results = []
    barrier = threading.Barrier(4)

    def call():
        barrier.wait()
        results.append(core_api.restore_session())

    with patch.object(IndexingPipeline, "_restore_graph", slow_restore):
        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert results == [True, True, True, True]


def test_require_indexed_does_not_report_not_indexed_during_a_concurrent_restore(
    indexed_storage, restore_on
):
    import threading
    import time

    original = IndexingPipeline._restore_graph

    def slow_restore(self):
        time.sleep(0.4)
        return original(self)

    errors = []
    barrier = threading.Barrier(2)

    def call():
        barrier.wait()
        _state, error = core_api.require_indexed()
        errors.append(error)

    with patch.object(IndexingPipeline, "_restore_graph", slow_restore):
        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert errors == [None, None]
