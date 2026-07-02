"""Tests for state.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nexus_mcp.state import get_state, reset_state


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


def test_initial_state():
    state = get_state()
    assert not state.is_indexed
    assert state.codebase_path is None
    assert state.vector_engine is None
    assert state.graph_engine is None
    assert state.memory_store is None


def test_state_singleton():
    s1 = get_state()
    s2 = get_state()
    assert s1 is s2


def test_state_set_codebase():
    state = get_state()
    state.codebase_path = Path("/project")
    assert state.is_indexed
    assert state.codebase_path == Path("/project")


def test_state_engine_setters():
    state = get_state()
    state.vector_engine = "mock_vector"
    state.graph_engine = "mock_graph"
    state.memory_store = "mock_memory"
    assert state.vector_engine == "mock_vector"
    assert state.graph_engine == "mock_graph"
    assert state.memory_store == "mock_memory"


def test_reset_state():
    state = get_state()
    state.codebase_path = Path("/project")
    reset_state()
    state2 = get_state()
    assert not state2.is_indexed


def test_initial_file_watchers_empty():
    state = get_state()
    assert state._file_watchers == []


def test_shutdown_stops_watchers_when_no_loop_running():
    """The real post-server.run() shutdown scenario: no event loop is running,
    so shutdown() should drive the async stop() via asyncio.run() without error."""
    state = get_state()
    watcher = AsyncMock()
    state._file_watchers = [watcher]

    state.shutdown()

    watcher.stop.assert_awaited_once()
    assert state.shutting_down is True


def test_shutdown_from_within_running_loop_does_not_raise():
    """If shutdown() is ever called from async application code (a loop is
    already running), it must log and continue rather than raise or hang."""
    async def run():
        state = get_state()
        watcher = AsyncMock()
        state._file_watchers = [watcher]

        state.shutdown()  # should not raise even though a loop is running here

        assert state.shutting_down is True
        watcher.stop.assert_not_awaited()  # can't drive it synchronously from here

    asyncio.run(run())


def test_shutdown_is_idempotent_with_watchers():
    state = get_state()
    watcher = AsyncMock()
    state._file_watchers = [watcher]

    state.shutdown()
    state.shutdown()  # second call should be a no-op, not stop the watcher again

    watcher.stop.assert_awaited_once()


def test_shutdown_continues_if_one_watcher_fails_to_stop():
    state = get_state()
    good_watcher = AsyncMock()
    bad_watcher = AsyncMock()
    bad_watcher.stop.side_effect = RuntimeError("boom")
    state._file_watchers = [bad_watcher, good_watcher]

    state.shutdown()  # must not raise despite bad_watcher failing

    good_watcher.stop.assert_awaited_once()
