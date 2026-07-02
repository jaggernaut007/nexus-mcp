"""Tests for MCP tools: index, search, status."""

import asyncio
import time
from unittest.mock import patch

import nexus_mcp.server as server_module
from nexus_mcp.state import get_state
from tests.conftest import _call_tool, _mock_embedding_service, _setup_indexed


async def _index_no_watch(codebase_path, storage_dir):
    """Like _setup_indexed, but with auto-watch off — for tests that manage
    staleness/reindex behavior deterministically instead of racing a real watcher."""
    with (
        patch("nexus_mcp.indexing.pipeline.get_embedding_service") as mock_get,
        patch.dict(
            "os.environ",
            {"NEXUS_STORAGE_DIR": str(storage_dir), "NEXUS_AUTO_WATCH": "false"},
        ),
    ):
        from nexus_mcp.config import reset_settings
        reset_settings()

        mock_svc = _mock_embedding_service()
        mock_get.return_value = mock_svc

        mcp = server_module.create_server()
        result = await _call_tool(mcp, "index", {"path": str(codebase_path)})

        state = get_state()
        if state.vector_engine:
            state.vector_engine._embedding_service = mock_svc

        reset_settings()
        return mcp, result


class TestStatus:
    def test_status_before_index(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "status"))
        assert result["version"] == "1.0.1"
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
            with (
                patch("nexus_mcp.indexing.pipeline.get_embedding_service", return_value=mock_svc),
                patch.dict("os.environ", {"NEXUS_STORAGE_DIR": str(storage)}),
            ):
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


class TestStaleness:
    def test_status_not_stale_immediately_after_index(self, mini_codebase, tmp_path):
        async def run():
            mcp, _ = await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "status")

        status = asyncio.run(run())
        assert status["stale"] is False
        assert status["staleness_warning"] is None

    def test_status_stale_after_file_modified(self, mini_codebase, tmp_path):
        async def run():
            mcp, _ = await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            time.sleep(0.05)
            (mini_codebase / "src" / "main.py").write_text("def hello():\n    pass\n")
            return await _call_tool(mcp, "status")

        status = asyncio.run(run())
        assert status["stale"] is True
        assert "out of date" in status["staleness_warning"]

    def test_search_warns_when_stale(self, mini_codebase, tmp_path):
        async def run():
            mcp, _ = await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            time.sleep(0.05)
            (mini_codebase / "src" / "main.py").write_text("def hello():\n    pass\n")
            return await _call_tool(mcp, "search", {"query": "hello"})

        result = asyncio.run(run())
        assert result["warning"] is not None
        assert "refreshing in background" in result["warning"]

    def test_search_no_warning_when_fresh(self, mini_codebase, tmp_path):
        async def run():
            mcp, _ = await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "hello"})

        result = asyncio.run(run())
        assert result["warning"] is None

    def test_staleness_check_is_throttled(self, mini_codebase, tmp_path):
        """Two status() calls within the throttle window should only recompute
        staleness once — a per-call filesystem walk would regress search latency
        on large repos."""
        async def run():
            mcp, _ = await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            pipeline = server_module._pipeline
            with patch.object(
                pipeline, "check_staleness", wraps=pipeline.check_staleness
            ) as spy:
                await _call_tool(mcp, "status")
                await _call_tool(mcp, "status")
                return spy.call_count

        assert asyncio.run(run()) == 1


class TestBackgroundReindex:
    def test_skipped_when_pipeline_busy(self, mini_codebase, tmp_path):
        asyncio.run(_index_no_watch(mini_codebase, tmp_path / ".nexus"))
        pipeline = server_module._pipeline
        state = get_state()

        with patch.object(pipeline, "incremental_index") as mock_incr:
            assert server_module._pipeline_lock.acquire(blocking=False)
            try:
                server_module._trigger_background_reindex(
                    state.codebase_path, state.codebase_paths
                )
            finally:
                server_module._pipeline_lock.release()
            mock_incr.assert_not_called()

    def test_runs_when_pipeline_free(self, mini_codebase, tmp_path):
        asyncio.run(_index_no_watch(mini_codebase, tmp_path / ".nexus"))
        pipeline = server_module._pipeline
        state = get_state()

        with patch.object(pipeline, "incremental_index") as mock_incr:
            server_module._trigger_background_reindex(
                state.codebase_path, state.codebase_paths
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not mock_incr.called:
                time.sleep(0.02)
            mock_incr.assert_called_once()


class TestFileWatcherWiring:
    def test_debounced_change_triggers_background_reindex(self, mini_codebase, tmp_path):
        async def run():
            from nexus_mcp.parsing.file_watcher import DebouncedFileWatcher

            await _index_no_watch(mini_codebase, tmp_path / ".nexus")
            pipeline = server_module._pipeline
            state = get_state()

            with patch.object(pipeline, "incremental_index") as mock_incr:
                def on_change():
                    server_module._trigger_background_reindex(
                        state.codebase_path, state.codebase_paths
                    )

                watcher = DebouncedFileWatcher(
                    project_root=state.codebase_path,
                    callback=on_change,
                    debounce_delay=0.2,
                )
                await watcher.start()
                try:
                    (mini_codebase / "src" / "main.py").write_text(
                        "def hello():\n    pass\n"
                    )
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline and not mock_incr.called:
                        await asyncio.sleep(0.05)
                finally:
                    await watcher.stop()

                assert mock_incr.called

        asyncio.run(run())
