"""Tool permission model for Nexus-MCP.

Classifies tools into READ, MUTATE, WRITE categories and enforces
access control based on the configured permission policy.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ToolCategory(Enum):
    """Permission categories for MCP tools."""

    READ = "read"
    MUTATE = "mutate"
    WRITE = "write"


# Static registry mapping tool name → category
TOOL_PERMISSIONS: dict[str, ToolCategory] = {
    # Read-only tools (query, no side effects)
    "status": ToolCategory.READ,
    "search": ToolCategory.READ,
    "find_symbol": ToolCategory.READ,
    "find_callers": ToolCategory.READ,
    "find_callees": ToolCategory.READ,
    "explain": ToolCategory.READ,
    "overview": ToolCategory.READ,
    "architecture": ToolCategory.READ,
    "recall": ToolCategory.READ,
    "health": ToolCategory.READ,
    # Mutating tools (triggers computation, disk writes)
    "index": ToolCategory.MUTATE,
    "analyze": ToolCategory.MUTATE,
    "impact": ToolCategory.MUTATE,
    # Write tools (modifies memory store)
    "remember": ToolCategory.WRITE,
    "forget": ToolCategory.WRITE,
}


@dataclass(frozen=True)
class PermissionPolicy:
    """Defines which tool categories and specific tools are allowed/denied."""

    allowed_categories: frozenset[ToolCategory] = field(
        default_factory=lambda: frozenset({ToolCategory.READ})
    )
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    denied_tools: frozenset[str] = field(default_factory=frozenset)


# Preset policies
DEFAULT_POLICY = PermissionPolicy(
    allowed_categories=frozenset({ToolCategory.READ})
)

FULL_ACCESS_POLICY = PermissionPolicy(
    allowed_categories=frozenset({ToolCategory.READ, ToolCategory.MUTATE, ToolCategory.WRITE})
)


def check_permission(tool_name: str, policy: PermissionPolicy) -> bool:
    """Check if a tool is allowed under the given policy.

    Resolution order:
    1. Explicit deny overrides everything
    2. Explicit allow overrides category check
    3. Category-based check
    4. Unknown tools are denied
    """
    if tool_name in policy.denied_tools:
        return False
    if tool_name in policy.allowed_tools:
        return True
    category = TOOL_PERMISSIONS.get(tool_name)
    if category is None:
        return False
    return category in policy.allowed_categories


def get_tool_category(tool_name: str) -> Optional[ToolCategory]:
    """Get the category for a tool, or None if unknown."""
    return TOOL_PERMISSIONS.get(tool_name)


def policy_from_level(level: str) -> PermissionPolicy:
    """Create a PermissionPolicy from a permission level string.

    Args:
        level: "read" for read-only, "full" for all access.
    """
    if level == "full":
        return FULL_ACCESS_POLICY
    return DEFAULT_POLICY
