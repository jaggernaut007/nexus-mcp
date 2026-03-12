"""Tests for MCP tools: index, search, status."""

import asyncio

import nexus_mcp.server as server_module
from tests.conftest import _call_tool, _setup_indexed


class TestStatus:
    def test_status_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "status"))
        assert result["version"] == "0.1.0"
        assert result["indexed"] is False
        assert result["codebase_path"] is None

    def test_status_after_index(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "status")

        status = asyncio.run(run())
        assert status["indexed"] is True
        assert status["codebase_path"] is not None
        assert "vector_chunks" in status
        assert status["vector_chunks"] > 0
        assert "graph" in status


class TestIndex:
    def test_index_returns_stats(self, mini_codebase, tmp_path):
        async def run():
            _, _, result = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return result

        result = asyncio.run(run())
        assert result["total_files"] >= 2
        assert result["total_symbols"] > 0
        assert result["total_chunks"] > 0
        assert result["time_seconds"] > 0

    def test_index_invalid_path(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "index", {"path": "/nonexistent/path"}))
        assert "error" in result

    def test_index_sets_state(self, mini_codebase, tmp_path):
        async def run():
            await _setup_indexed(mini_codebase, tmp_path / ".nexus")

        asyncio.run(run())
        from nexus_mcp.state import get_state
        state = get_state()
        assert state.is_indexed
        assert state.vector_engine is not None
        assert state.graph_engine is not None

    def test_index_incremental_on_second_call(self, mini_codebase, tmp_path):
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, result1 = await _setup_indexed(mini_codebase, storage)
            assert result1["total_files"] >= 2

            # Second call should be incremental (metadata exists)
            server_module._pipeline = None
            result2 = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
            return result2

        result2 = asyncio.run(run())
        assert "error" not in result2


class TestSearch:
    def test_search_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "search", {"query": "hello"}))
        assert "error" in result

    def test_search_after_index(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "hello"})

        result = asyncio.run(run())
        assert "error" not in result
        assert result["query"] == "hello"
        assert result["total"] > 0
        assert len(result["results"]) > 0

    def test_search_result_format(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "test"})

        result = asyncio.run(run())
        r = result["results"][0]
        assert "filepath" in r
        assert "symbol_name" in r
        assert "score" in r
        assert "language" in r

    def test_search_with_limit(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "test", "limit": 1})

        result = asyncio.run(run())
        assert result["total"] <= 1

    def test_search_relative_paths(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "test"})

        result = asyncio.run(run())
        for r in result["results"]:
            assert not r["filepath"].startswith("/"), f"Path not relative: {r['filepath']}"
