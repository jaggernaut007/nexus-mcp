"""Tests for graph MCP tools: find_symbol, find_callers, find_callees."""

import asyncio

import nexus_mcp.server as server_module
from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.state import get_state
from tests.conftest import _call_tool, _setup_indexed

# --- Helpers for direct graph manipulation ---

def _make_node(name, node_type=NodeType.FUNCTION, file_path="src/test.py", start_line=1):
    return UniversalNode(
        id=f"{node_type.value}:{name}",
        name=name,
        node_type=node_type,
        location=UniversalLocation(
            file_path=file_path,
            start_line=start_line,
            end_line=start_line + 5,
        ),
        language="python",
        complexity=3,
        line_count=6,
    )


def _make_call_rel(source_name, target_name):
    return UniversalRelationship(
        id=f"calls:{source_name}->{target_name}",
        source_id=f"function:{source_name}",
        target_id=f"function:{target_name}",
        relationship_type=RelationshipType.CALLS,
    )


def _setup_graph_with_calls(state, codebase_path):
    """Set up a graph with known call relationships for testing."""
    from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

    graph = RustworkxCodeGraph()

    # Create nodes
    hello = _make_node("hello", file_path=str(codebase_path / "src" / "main.py"))
    helper = _make_node("helper", file_path=str(codebase_path / "src" / "utils.py"))
    orchestrate = _make_node("orchestrate", file_path=str(codebase_path / "src" / "app.py"))

    graph.add_node(hello)
    graph.add_node(helper)
    graph.add_node(orchestrate)

    # orchestrate calls hello and helper; hello calls helper
    graph.add_relationship(_make_call_rel("orchestrate", "hello"))
    graph.add_relationship(_make_call_rel("orchestrate", "helper"))
    graph.add_relationship(_make_call_rel("hello", "helper"))

    state.graph_engine = graph
    state.codebase_path = codebase_path


class TestFindSymbol:
    def test_find_symbol_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "find_symbol", {"name": "hello"}))
        assert "error" in result

    def test_find_symbol_exact_match(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "hello"})

        result = asyncio.run(run())
        assert "error" not in result
        assert result["total"] >= 1
        assert any(s["name"] == "hello" for s in result["symbols"])

    def test_find_symbol_no_match(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "nonexistent_xyz"})

        result = asyncio.run(run())
        assert "error" in result

    def test_find_symbol_fuzzy_match(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "hell", "exact": False})

        result = asyncio.run(run())
        assert "error" not in result
        assert result["total"] >= 1
        assert any("hell" in s["name"].lower() for s in result["symbols"])

    def test_find_symbol_includes_relationships(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_symbol", {"name": "hello"}))
        assert "error" not in result
        symbol = result["symbols"][0]
        assert "relationships_out" in symbol
        assert "relationships_in" in symbol

    def test_find_symbol_relative_paths(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_symbol", {"name": "hello"}))
        symbol = result["symbols"][0]
        file_path = symbol["location"]["file"]
        assert not file_path.startswith("/"), f"Path not relative: {file_path}"


class TestFindCallers:
    def test_find_callers_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "find_callers", {"symbol_name": "hello"}))
        assert "error" in result

    def test_find_callers_not_found(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callers", {"symbol_name": "nonexistent"}))
        assert "error" in result

    def test_find_callers_no_callers(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callers", {"symbol_name": "orchestrate"}))
        assert "error" not in result
        assert result["total"] == 0
        assert result["callers"] == []

    def test_find_callers_with_callers(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callers", {"symbol_name": "helper"}))
        assert "error" not in result
        assert result["symbol"] == "helper"
        assert result["total"] >= 2
        caller_names = [c["name"] for c in result["callers"]]
        assert "hello" in caller_names
        assert "orchestrate" in caller_names


class TestFindCallees:
    def test_find_callees_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "find_callees", {"symbol_name": "hello"}))
        assert "error" in result

    def test_find_callees_not_found(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callees", {"symbol_name": "nonexistent"}))
        assert "error" in result

    def test_find_callees_with_callees(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callees", {"symbol_name": "orchestrate"}))
        assert "error" not in result
        assert result["symbol"] == "orchestrate"
        assert result["total"] >= 2
        callee_names = [c["name"] for c in result["callees"]]
        assert "hello" in callee_names
        assert "helper" in callee_names

    def test_find_callees_no_callees(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_graph_with_calls(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "find_callees", {"symbol_name": "helper"}))
        assert "error" not in result
        assert result["total"] == 0
        assert result["callees"] == []
