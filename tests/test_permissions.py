"""Tests for tool permission model (Phase 5b)."""

import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.security.permissions import (
    DEFAULT_POLICY,
    FULL_ACCESS_POLICY,
    TOOL_PERMISSIONS,
    PermissionPolicy,
    ToolCategory,
    check_permission,
    get_tool_category,
    policy_from_level,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_settings()
    yield
    reset_settings()


# --- ToolCategory and registry ---


def test_tool_categories_defined():
    """All 12 tools should be registered."""
    expected_tools = {
        "status", "search", "find_symbol", "find_callers", "find_callees",
        "explain", "recall", "health",
        "index", "analyze", "impact",
        "remember", "forget",
    }
    assert set(TOOL_PERMISSIONS.keys()) == expected_tools


def test_read_tools_classified():
    """Read-only tools are classified as READ."""
    read_tools = ["status", "search", "find_symbol", "find_callers",
                  "find_callees", "explain", "recall", "health"]
    for tool in read_tools:
        assert TOOL_PERMISSIONS[tool] == ToolCategory.READ, f"{tool} should be READ"


def test_mutate_tools_classified():
    """Mutating tools are classified as MUTATE."""
    for tool in ["index", "analyze", "impact"]:
        assert TOOL_PERMISSIONS[tool] == ToolCategory.MUTATE, f"{tool} should be MUTATE"


def test_write_tools_classified():
    """Write tools are classified as WRITE."""
    for tool in ["remember", "forget"]:
        assert TOOL_PERMISSIONS[tool] == ToolCategory.WRITE, f"{tool} should be WRITE"


# --- DEFAULT_POLICY ---


def test_default_policy_allows_read():
    """DEFAULT_POLICY allows read tools."""
    assert check_permission("status", DEFAULT_POLICY) is True
    assert check_permission("search", DEFAULT_POLICY) is True
    assert check_permission("recall", DEFAULT_POLICY) is True


def test_default_policy_denies_mutate():
    """DEFAULT_POLICY denies mutate tools."""
    assert check_permission("index", DEFAULT_POLICY) is False
    assert check_permission("analyze", DEFAULT_POLICY) is False
    assert check_permission("impact", DEFAULT_POLICY) is False


def test_default_policy_denies_write():
    """DEFAULT_POLICY denies write tools."""
    assert check_permission("remember", DEFAULT_POLICY) is False
    assert check_permission("forget", DEFAULT_POLICY) is False


# --- FULL_ACCESS_POLICY ---


def test_full_access_allows_all():
    """FULL_ACCESS_POLICY allows all tools."""
    for tool_name in TOOL_PERMISSIONS:
        assert check_permission(tool_name, FULL_ACCESS_POLICY) is True


# --- Custom policies ---


def test_custom_policy_with_allowed_tools():
    """Explicit allowed_tools overrides category check."""
    policy = PermissionPolicy(
        allowed_categories=frozenset({ToolCategory.READ}),
        allowed_tools=frozenset({"index"}),
    )
    assert check_permission("index", policy) is True
    assert check_permission("search", policy) is True
    assert check_permission("remember", policy) is False


def test_custom_policy_with_denied_tools():
    """Explicit denied_tools overrides everything."""
    policy = PermissionPolicy(
        allowed_categories=frozenset({ToolCategory.READ, ToolCategory.MUTATE, ToolCategory.WRITE}),
        denied_tools=frozenset({"forget"}),
    )
    assert check_permission("forget", policy) is False
    assert check_permission("remember", policy) is True
    assert check_permission("search", policy) is True


def test_denied_overrides_allowed():
    """denied_tools takes precedence over allowed_tools."""
    policy = PermissionPolicy(
        allowed_tools=frozenset({"index"}),
        denied_tools=frozenset({"index"}),
    )
    assert check_permission("index", policy) is False


def test_unknown_tool_denied():
    """Unknown tool names are always denied."""
    assert check_permission("nonexistent_tool", FULL_ACCESS_POLICY) is False
    assert check_permission("nonexistent_tool", DEFAULT_POLICY) is False


# --- Helper functions ---


def test_get_tool_category():
    """get_tool_category returns correct category or None."""
    assert get_tool_category("search") == ToolCategory.READ
    assert get_tool_category("index") == ToolCategory.MUTATE
    assert get_tool_category("remember") == ToolCategory.WRITE
    assert get_tool_category("nonexistent") is None


def test_policy_from_level_full():
    """policy_from_level('full') returns FULL_ACCESS_POLICY."""
    policy = policy_from_level("full")
    assert policy == FULL_ACCESS_POLICY


def test_policy_from_level_read():
    """policy_from_level('read') returns DEFAULT_POLICY."""
    policy = policy_from_level("read")
    assert policy == DEFAULT_POLICY


def test_policy_from_level_unknown_defaults_to_read():
    """Unknown level defaults to read-only."""
    policy = policy_from_level("unknown")
    assert policy == DEFAULT_POLICY


# --- Config integration ---


def test_config_default_permission_level():
    """Default permission level is 'full' for backward compatibility."""
    s = Settings()
    assert s.default_permission_level == "full"


def test_config_permission_level_env(monkeypatch):
    """NEXUS_PERMISSION_LEVEL env var sets permission level."""
    monkeypatch.setenv("NEXUS_PERMISSION_LEVEL", "read")
    reset_settings()
    s = Settings()
    assert s.default_permission_level == "read"
