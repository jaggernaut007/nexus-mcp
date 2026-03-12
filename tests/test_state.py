"""Tests for state.py."""

from pathlib import Path

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
