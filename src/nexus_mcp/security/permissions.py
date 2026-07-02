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
    "explain": ToolCategory.READ,
    "health": ToolCategory.READ,
    # graph() replaces find_callers/find_callees (READ) and impact (was MUTATE).
    # None of the three ever mutate persisted state — impact's old MUTATE tag
    # reflected computational cost, not data safety, so READ is the correct
    # category for the merged tool, not a permission loosening. See ADR-017.
    "graph": ToolCategory.READ,
    # map() replaces overview + architecture (both were already READ).
    "map": ToolCategory.READ,
    # Mutating tools (triggers computation, disk writes)
    "index": ToolCategory.MUTATE,
    "analyze": ToolCategory.MUTATE,
    # memory() replaces remember/forget (WRITE) + recall (READ). Static fallback
    # is WRITE (the more restrictive of the two) — memory() always passes an
    # explicit category_override derived from `action` at call time, so this
    # entry is only reached if that dispatch is ever bypassed. See ADR-017.
    "memory": ToolCategory.WRITE,
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


def check_permission(
    tool_name: str,
    policy: PermissionPolicy,
    category_override: Optional[ToolCategory] = None,
) -> bool:
    """Check if a tool is allowed under the given policy.

    Resolution order:
    1. Explicit deny overrides everything
    2. Explicit allow overrides category check
    3. Category-based check (category_override wins over the static registry —
       for tools like `memory` whose actual category depends on a call-time
       parameter, e.g. action="search" vs action="store")
    4. Unknown tools are denied
    """
    if tool_name in policy.denied_tools:
        return False
    if tool_name in policy.allowed_tools:
        return True
    category = category_override or TOOL_PERMISSIONS.get(tool_name)
    if category is None:
        return False
    return category in policy.allowed_categories


def get_tool_category(
    tool_name: str, category_override: Optional[ToolCategory] = None
) -> Optional[ToolCategory]:
    """Get the category for a tool, or None if unknown. category_override
    takes precedence, matching check_permission()'s resolution order."""
    return category_override or TOOL_PERMISSIONS.get(tool_name)


def policy_from_level(level: str) -> PermissionPolicy:
    """Create a PermissionPolicy from a permission level string.

    Args:
        level: "read" for read-only, "full" for all access.
    """
    if level == "full":
        return FULL_ACCESS_POLICY
    return DEFAULT_POLICY
