"""Tests for the impact MCP tool."""

import asyncio

import nexus_mcp.server as server_module
from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.state import get_state
from tests.conftest import _call_tool


def _setup_deep_call_chain(state, codebase_path):
    """Set up a graph with a deep call chain for impact testing.

    Chain: d -> c -> b -> a (d calls c, c calls b, b calls a)
    Also: e -> a (e directly calls a)
    """
    graph = RustworkxCodeGraph()

    for name in ["a", "b", "c", "d", "e"]:
        node = UniversalNode(
            id=f"function:{name}",
            name=name,
            node_type=NodeType.FUNCTION,
            location=UniversalLocation(
                file_path=str(codebase_path / "src" / f"{name}.py"),
                start_line=1,
                end_line=10,
            ),
            language="python",
            complexity=2,
            line_count=10,
        )
        graph.add_node(node)

    # b calls a, c calls b, d calls c, e calls a
    for src, tgt in [("b", "a"), ("c", "b"), ("d", "c"), ("e", "a")]:
        graph.add_relationship(UniversalRelationship(
            id=f"calls:{src}->{tgt}",
            source_id=f"function:{src}",
            target_id=f"function:{tgt}",
            relationship_type=RelationshipType.CALLS,
        ))

    state.graph_engine = graph
    state.codebase_path = codebase_path


class TestImpact:
    def test_impact_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "a"}))
        assert "error" in result

    def test_impact_symbol_not_found(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_deep_call_chain(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "nonexistent"}))
        assert "error" in result

    def test_impact_no_callers(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_deep_call_chain(state, tmp_path)

        # d is at the top of the chain, nobody calls d
        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "d"}))
        assert "error" not in result
        assert result["total_impacted"] == 0
        assert result["impacted_symbols"] == []

    def test_impact_with_transitive_callers(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_deep_call_chain(state, tmp_path)

        # a is called by b, c (via b), d (via c->b), and e
        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "a"}))
        assert "error" not in result
        assert result["symbol"] == "a"
        assert result["total_impacted"] >= 4
        names = [s["name"] for s in result["impacted_symbols"]]
        assert "b" in names
        assert "c" in names
        assert "d" in names
        assert "e" in names

    def test_impact_max_depth(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_deep_call_chain(state, tmp_path)

        # With max_depth=1, only direct callers of a: b and e
        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "a", "max_depth": 1}))
        assert "error" not in result
        assert result["max_depth"] == 1
        names = [s["name"] for s in result["impacted_symbols"]]
        assert "b" in names
        assert "e" in names
        # c and d should NOT be in results (depth 2 and 3)
        assert "c" not in names
        assert "d" not in names

    def test_impact_groups_by_file(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_deep_call_chain(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "impact", {"symbol_name": "a"}))
        assert "impacted_files" in result
        assert isinstance(result["impacted_files"], dict)
        # Each file should map to a list of symbol names
        for file_path, names in result["impacted_files"].items():
            assert isinstance(names, list)
            assert not file_path.startswith("/"), f"Path not relative: {file_path}"
