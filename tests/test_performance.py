"""Performance benchmark tests. Marked slow — skip with: pytest -m 'not slow'."""

import asyncio
import time

import pytest

import nexus_mcp.server as server_module
from tests.conftest import _call_tool, _setup_indexed

pytestmark = pytest.mark.slow


class TestPerformanceBenchmarks:
    def test_warm_start_under_5s(self, mini_codebase, tmp_path):
        """Incremental index with no changes should complete in <5s."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, _ = await _setup_indexed(mini_codebase, storage)

            start = time.monotonic()
            result = await _call_tool(mcp, "index", {"path": str(mini_codebase)})
            elapsed = time.monotonic() - start

            assert "error" not in result
            assert elapsed < 5.0, f"Warm start took {elapsed:.2f}s (target <5s)"

        asyncio.run(run())

    def test_search_under_500ms(self, mini_codebase, tmp_path):
        """Search should complete in <500ms."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, _ = await _setup_indexed(mini_codebase, storage)

            start = time.monotonic()
            result = await _call_tool(mcp, "search", {"query": "hello function"})
            elapsed = time.monotonic() - start

            assert "error" not in result
            assert elapsed < 0.5, f"Search took {elapsed:.2f}s (target <0.5s)"

        asyncio.run(run())

    def test_find_symbol_under_100ms(self, mini_codebase, tmp_path):
        """find_symbol should complete in <100ms."""
        async def run():
            storage = tmp_path / ".nexus"
            mcp, _, _ = await _setup_indexed(mini_codebase, storage)

            start = time.monotonic()
            await _call_tool(mcp, "find_symbol", {"name": "hello"})
            elapsed = time.monotonic() - start

            # May or may not find symbol, but should be fast
            assert elapsed < 0.1, f"find_symbol took {elapsed:.2f}s (target <0.1s)"

        asyncio.run(run())

    def test_status_under_50ms(self):
        """Status tool should be near-instant."""
        mcp = server_module.create_server()
        start = time.monotonic()
        asyncio.run(_call_tool(mcp, "status"))
        elapsed = time.monotonic() - start
        assert elapsed < 0.05, f"Status took {elapsed:.2f}s (target <0.05s)"
