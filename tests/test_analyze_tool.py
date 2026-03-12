"""Tests for the analyze MCP tool."""

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
from tests.conftest import _call_tool, _setup_indexed


def _setup_analysis_graph(state, codebase_path):
    """Set up a graph with nodes suitable for analysis testing."""
    graph = RustworkxCodeGraph()

    # Module node
    mod = UniversalNode(
        id="module:main",
        name="main",
        node_type=NodeType.MODULE,
        location=UniversalLocation(
            file_path=str(codebase_path / "src" / "main.py"),
            start_line=1,
            end_line=50,
        ),
        language="python",
        line_count=50,
    )
    graph.add_node(mod)

    # Functions with varying complexity
    func_simple = UniversalNode(
        id="function:simple_func",
        name="simple_func",
        node_type=NodeType.FUNCTION,
        location=UniversalLocation(
            file_path=str(codebase_path / "src" / "main.py"),
            start_line=5,
            end_line=10,
        ),
        language="python",
        complexity=2,
        line_count=6,
        docstring="A simple function.",
    )
    func_complex = UniversalNode(
        id="function:complex_func",
        name="complex_func",
        node_type=NodeType.FUNCTION,
        location=UniversalLocation(
            file_path=str(codebase_path / "src" / "main.py"),
            start_line=15,
            end_line=80,
        ),
        language="python",
        complexity=18,
        line_count=66,
    )
    func_utils = UniversalNode(
        id="function:util_func",
        name="util_func",
        node_type=NodeType.FUNCTION,
        location=UniversalLocation(
            file_path=str(codebase_path / "src" / "utils.py"),
            start_line=1,
            end_line=10,
        ),
        language="python",
        complexity=3,
        line_count=10,
        docstring="Utility function.",
    )

    graph.add_node(func_simple)
    graph.add_node(func_complex)
    graph.add_node(func_utils)

    # Add a CALLS relationship
    graph.add_relationship(UniversalRelationship(
        id="calls:complex->simple",
        source_id="function:complex_func",
        target_id="function:simple_func",
        relationship_type=RelationshipType.CALLS,
    ))

    state.graph_engine = graph
    state.codebase_path = codebase_path


class TestAnalyze:
    def test_analyze_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "analyze"))
        assert "error" in result

    def test_analyze_full_codebase(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_analysis_graph(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "analyze"))
        assert "error" not in result
        assert "complexity" in result
        assert "dependencies" in result
        assert "code_smells" in result
        assert "quality" in result

    def test_analyze_result_structure(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_analysis_graph(state, tmp_path)

        result = asyncio.run(_call_tool(mcp, "analyze"))

        # Complexity section
        cx = result["complexity"]
        assert "total_functions" in cx
        assert "average_complexity" in cx
        assert "max_complexity" in cx
        assert "high_complexity_functions" in cx
        assert cx["total_functions"] == 3
        assert cx["max_complexity"] == 18

        # Quality section
        q = result["quality"]
        assert "maintainability_index" in q
        assert "documentation_ratio" in q
        assert "quality_score" in q

        # Code smells section
        smells = result["code_smells"]
        assert "long_functions" in smells
        assert "complex_functions" in smells
        # complex_func has line_count=66 (>50) and complexity=18 (>15)
        assert len(smells["long_functions"]) >= 1
        assert len(smells["complex_functions"]) >= 1

    def test_analyze_with_path_filter(self, tmp_path):
        mcp = server_module.create_server()
        state = get_state()
        _setup_analysis_graph(state, tmp_path)

        # Filter to only src/utils.py
        result = asyncio.run(_call_tool(mcp, "analyze", {"path": "src/utils.py"}))
        assert "error" not in result

        # High complexity functions should not include complex_func (it's in main.py)
        high_cx = result["complexity"].get("high_complexity_functions", [])
        for f in high_cx:
            loc = f.get("location", "")
            assert "main.py" not in loc

    def test_analyze_after_real_index(self, mini_codebase, tmp_path):
        """Test analyze works after a real indexing pipeline run."""
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "analyze")

        result = asyncio.run(run())
        assert "error" not in result
        assert "complexity" in result
        assert "quality" in result
