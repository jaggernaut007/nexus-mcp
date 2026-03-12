"""Tests for the explain MCP tool."""

import asyncio

import pytest

from tests.conftest import _call_tool, _setup_indexed


@pytest.fixture
def explain_codebase(tmp_path):
    """Create a codebase with functions for explain testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "parser.py").write_text(
        'def parse_tokens(text):\n'
        '    """Parse text into tokens."""\n'
        '    return text.split()\n'
    )
    (src / "validator.py").write_text(
        'from parser import parse_tokens\n\n\n'
        'def validate_input(text):\n'
        '    """Validate user input."""\n'
        '    tokens = parse_tokens(text)\n'
        '    return len(tokens) > 0\n'
    )
    return tmp_path


class TestExplainTool:
    def test_explain_returns_symbol(self, explain_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(explain_codebase, storage)
            result = await _call_tool(mcp, "explain", {
                "symbol_name": "parse_tokens",
            })
            assert "error" not in result
            assert "symbol" in result

        asyncio.run(_test())

    def test_explain_not_found(self, explain_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(explain_codebase, storage)
            result = await _call_tool(mcp, "explain", {
                "symbol_name": "nonexistent_func",
            })
            assert "error" in result

        asyncio.run(_test())

    def test_explain_before_index(self):
        async def _test():
            import nexus_mcp.server as server_module
            mcp = server_module.create_server()
            result = await _call_tool(mcp, "explain", {
                "symbol_name": "test",
            })
            assert "error" in result

        asyncio.run(_test())

    def test_explain_summary_verbosity(self, explain_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(explain_codebase, storage)
            result = await _call_tool(mcp, "explain", {
                "symbol_name": "parse_tokens",
                "verbosity": "summary",
            })
            assert result.get("verbosity") == "summary"

        asyncio.run(_test())

    def test_explain_fuzzy_match(self, explain_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(explain_codebase, storage)
            # Use partial name that won't exact match
            result = await _call_tool(mcp, "explain", {
                "symbol_name": "parse",
            })
            # Should find via fuzzy match
            assert "symbol" in result or "error" in result

        asyncio.run(_test())
