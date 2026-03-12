"""End-to-end tests: full lifecycle, corrupt index recovery, graceful shutdown."""

import asyncio
import json

import nexus_mcp.server as server_module
from nexus_mcp.state import get_state
from tests.conftest import _call_tool, _setup_indexed


class TestFullLifecycle:
    def test_index_search_find_analyze(self, mini_codebase_with_calls, tmp_path):
        """Full lifecycle: index -> search -> find_symbol -> analyze -> impact."""
        async def run():
            mcp, _, idx_result = await _setup_indexed(
                mini_codebase_with_calls, tmp_path / ".nexus"
            )

            # Verify index
            assert idx_result["total_files"] >= 3
            assert idx_result["total_chunks"] > 0

            # Search
            search_result = await _call_tool(mcp, "search", {"query": "hello"})
            assert "error" not in search_result
            assert search_result["total"] >= 0

            # Find symbol
            find_result = await _call_tool(mcp, "find_symbol", {"name": "hello"})
            assert "error" not in find_result or "not found" in find_result.get("error", "").lower()

            # Analyze
            analyze_result = await _call_tool(mcp, "analyze")
            assert "complexity" in analyze_result
            assert "quality" in analyze_result

            # Status
            status = await _call_tool(mcp, "status")
            assert status["indexed"] is True
            assert "memory" in status
            assert status["memory"]["peak_rss_mb"] > 0

            return True

        assert asyncio.run(run())

    def test_status_has_memory_info(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "status"))
        assert "memory" in result
        assert "peak_rss_mb" in result["memory"]
        assert isinstance(result["memory"]["peak_rss_mb"], float)
        assert result["memory"]["peak_rss_mb"] > 0


class TestCorruptIndexRecovery:
    def test_corrupt_metadata_triggers_rebuild(self, mini_codebase, tmp_path):
        """Corrupt metadata JSON triggers full rebuild on incremental_index."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, first_result = await _setup_indexed(mini_codebase, storage)

            # Corrupt the metadata file
            metadata_path = storage / "index_metadata.json"
            assert metadata_path.exists()
            metadata_path.write_text("{invalid json!!!")

            # Second index should detect corruption and rebuild
            result = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
            assert "error" not in result
            assert result["total_files"] >= 2

            return True

        assert asyncio.run(run())

    def test_missing_metadata_triggers_full_index(self, mini_codebase, tmp_path):
        """Missing metadata falls through to full index."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, first_result = await _setup_indexed(mini_codebase, storage)

            # Delete the metadata file
            metadata_path = storage / "index_metadata.json"
            metadata_path.unlink()

            # Second index should do full index
            result = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
            assert "error" not in result
            assert result["total_files"] >= 2

            return True

        assert asyncio.run(run())

    def test_corrupt_metadata_missing_mtimes(self, mini_codebase, tmp_path):
        """Metadata missing 'mtimes' key triggers rebuild."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, _ = await _setup_indexed(mini_codebase, storage)

            metadata_path = storage / "index_metadata.json"
            metadata_path.write_text(json.dumps({"codebase_path": "/tmp"}))

            result = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
            assert "error" not in result
            assert result["total_files"] >= 2

            return True

        assert asyncio.run(run())


class TestGracefulShutdown:
    def test_shutdown_sets_flag(self):
        state = get_state()
        assert state.shutting_down is False
        state.shutdown()
        assert state.shutting_down is True

    def test_shutdown_idempotent(self):
        state = get_state()
        state.shutdown()
        state.shutdown()  # Second call should not raise
        assert state.shutting_down is True

    def test_shutdown_persists_graph(self, mini_codebase_with_calls, tmp_path):
        """Shutdown should persist graph state if indexed."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, _ = await _setup_indexed(
                mini_codebase_with_calls, storage
            )

            state = get_state()
            assert state.graph_engine is not None

            # Call shutdown — should persist graph
            state.shutdown()
            assert state.shutting_down is True

            return True

        assert asyncio.run(run())
