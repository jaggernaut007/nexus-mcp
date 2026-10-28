"""Tests for health endpoint and new exceptions (Phase 5f)."""

import asyncio

import pytest

from nexus_mcp.config import reset_settings
from nexus_mcp.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NexusException,
    RateLimitError,
)
from nexus_mcp.state import reset_state
from tests.conftest import _call_api


@pytest.fixture(autouse=True)
def clean_state():
    reset_settings()
    reset_state()
    yield
    reset_settings()
    reset_state()


def test_health_returns_status():
    """Health tool returns expected structure."""
    result = asyncio.run(_call_api("health"))
    assert result["status"] == "healthy"
    assert "uptime_seconds" in result
    assert "indexed" in result
    assert "engines" in result


def test_health_shows_engine_states():
    """Health shows all engine availability flags."""
    result = asyncio.run(_call_api("health"))
    engines = result["engines"]
    assert "vector" in engines
    assert "bm25" in engines
    assert "graph" in engines
    assert "memory" in engines
    assert engines["vector"] is False
    assert engines["graph"] is False


def test_health_uptime_positive():
    """Health uptime is a positive number."""
    result = asyncio.run(_call_api("health"))
    assert result["uptime_seconds"] >= 0


def test_health_works_before_indexing():
    """Health works even when nothing is indexed."""
    result = asyncio.run(_call_api("health"))
    assert result["indexed"] is False
    assert result["status"] == "healthy"


# --- New exception types ---


def test_authentication_error_is_nexus_exception():
    assert issubclass(AuthenticationError, NexusException)


def test_authorization_error_is_nexus_exception():
    assert issubclass(AuthorizationError, NexusException)


def test_rate_limit_error_is_nexus_exception():
    assert issubclass(RateLimitError, NexusException)
