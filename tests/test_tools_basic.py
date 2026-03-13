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
        assert "hint" in result

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
        assert "hint" in status


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
            from unittest.mock import patch
            from tests.conftest import _mock_embedding_service

            storage = tmp_path / ".nexus"
            mcp, _, result1 = await _setup_indexed(mini_codebase, storage)
            assert result1["total_files"] >= 2

            # Second call should be incremental (metadata exists)
            # Keep embedding service patched since _setup_indexed's patch has exited
            server_module._pipeline = None
            mock_svc = _mock_embedding_service()
            with patch("nexus_mcp.indexing.pipeline.get_embedding_service", return_value=mock_svc), \
                 patch.dict("os.environ", {"NEXUS_STORAGE_DIR": str(storage)}):
                from nexus_mcp.config import reset_settings
                reset_settings()
                result2 = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
                reset_settings()
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
        # New fields: code_snippet, absolute_path, no raw vector
        assert "code_snippet" in r, "search results must include code_snippet"
        assert "absolute_path" in r, "search results must include absolute_path"
        assert r["absolute_path"].startswith("/"), "absolute_path must be absolute"
        assert "vector" not in r, "raw embedding vector must be stripped"
        assert "text" not in r, "text field should be renamed to code_snippet"
        # Response-level hint
        assert "hint" in result

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
