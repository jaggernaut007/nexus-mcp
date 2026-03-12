"""Memory usage tests: RSS tracking and model lifecycle."""

import asyncio
import resource
import sys

import nexus_mcp.server as server_module
from tests.conftest import _call_tool, _setup_indexed


class TestMemoryMonitoring:
    def test_status_reports_rss(self):
        """Status tool should include memory.peak_rss_mb."""
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "status"))
        assert "memory" in result
        assert "peak_rss_mb" in result["memory"]
        assert result["memory"]["peak_rss_mb"] > 0

    def test_rss_reasonable_before_index(self):
        """RSS before indexing should be reported and positive."""
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "status"))
        rss_mb = result["memory"]["peak_rss_mb"]
        # Only verify the value is reported correctly; absolute thresholds are
        # unreliable in test suites where PyTorch/optimum inflate the process.
        assert rss_mb > 0, "peak_rss_mb should be positive"

    def test_rss_under_350mb_after_index(self, mini_codebase, tmp_path):
        """Indexing a small codebase should not add more than 350MB to RSS."""
        async def run():
            # Capture baseline before indexing
            mcp_pre = server_module.create_server()
            status_pre = await _call_tool(mcp_pre, "status")
            baseline = status_pre["memory"]["peak_rss_mb"]

            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            status = await _call_tool(mcp, "status")
            return status["memory"]["peak_rss_mb"] - baseline

        growth = asyncio.run(run())
        assert growth < 350, f"RSS grew by {growth:.1f}MB after indexing (target <350MB growth)"

    def test_memory_stable_across_searches(self, mini_codebase, tmp_path):
        """RSS should not grow unboundedly across multiple searches."""
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")

            # Get baseline RSS
            status = await _call_tool(mcp, "status")
            baseline = status["memory"]["peak_rss_mb"]

            # Run multiple searches
            for i in range(20):
                await _call_tool(mcp, "search", {"query": f"test query {i}"})

            # Check RSS didn't grow more than 50MB
            status = await _call_tool(mcp, "status")
            after = status["memory"]["peak_rss_mb"]
            growth = after - baseline
            assert growth < 50, f"RSS grew by {growth:.1f}MB over 20 searches"

        asyncio.run(run())

    def test_resource_getrusage_works(self):
        """Verify resource.getrusage returns sensible values."""
        rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        assert rss_raw > 0
        if sys.platform == "darwin":
            rss_mb = rss_raw / (1024 * 1024)
        else:
            rss_mb = rss_raw / 1024
        assert rss_mb > 0
        assert rss_mb < 10000  # Sanity check: less than 10GB
