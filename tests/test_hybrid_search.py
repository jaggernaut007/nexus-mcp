"""Integration tests for hybrid search (vector + BM25 + graph + fusion + rerank)."""

import asyncio

import pytest

from tests.conftest import _call_tool, _setup_indexed


@pytest.fixture
def search_codebase(tmp_path):
    """Create a codebase with diverse content for search testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "parser.py").write_text(
        'def parse_tokens(text):\n'
        '    """Parse text into tokens."""\n'
        '    return text.split()\n\n\n'
        'def parse_json(data):\n'
        '    """Parse JSON string."""\n'
        '    import json\n'
        '    return json.loads(data)\n'
    )
    (src / "validator.py").write_text(
        'from parser import parse_tokens\n\n\n'
        'def validate_input(text):\n'
        '    """Validate user input by parsing tokens."""\n'
        '    tokens = parse_tokens(text)\n'
        '    return len(tokens) > 0\n'
    )
    (src / "analyzer.py").write_text(
        'from parser import parse_tokens, parse_json\n\n\n'
        'def analyze_data(raw):\n'
        '    """Analyze raw data by parsing."""\n'
        '    tokens = parse_tokens(raw)\n'
        '    return {"count": len(tokens)}\n'
    )
    return tmp_path


class TestHybridSearch:
    def test_hybrid_search_returns_results(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse tokens",
                "limit": 5,
            })
            assert "error" not in result
            assert result["total"] >= 1
            assert len(result["results"]) >= 1

        asyncio.run(_test())

    def test_hybrid_search_includes_mode(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse",
                "mode": "hybrid",
            })
            assert result["search_mode"] == "hybrid"
            assert "engines_used" in result
            assert "vector" in result["engines_used"]

        asyncio.run(_test())

    def test_vector_only_mode(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "validate input",
                "mode": "vector",
            })
            assert result["search_mode"] == "vector"
            assert "vector" in result["engines_used"]
            assert "bm25" not in result["engines_used"]

        asyncio.run(_test())

    def test_bm25_only_mode(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse tokens",
                "mode": "bm25",
            })
            assert result["search_mode"] == "bm25"
            # BM25 should be present if FTS index was created
            if result["total"] > 0:
                assert "bm25" in result["engines_used"]

        asyncio.run(_test())

    def test_search_with_language_filter(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse",
                "language": "python",
            })
            assert "error" not in result
            for r in result["results"]:
                assert r.get("language") == "python"

        asyncio.run(_test())

    def test_search_limit_respected(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse",
                "limit": 2,
            })
            assert len(result["results"]) <= 2

        asyncio.run(_test())

    def test_search_relative_paths(self, search_codebase, tmp_path):
        async def _test():
            storage = tmp_path / "storage"
            mcp, _, _ = await _setup_indexed(search_codebase, storage)
            result = await _call_tool(mcp, "search", {
                "query": "parse",
                "limit": 5,
            })
            for r in result["results"]:
                fp = r.get("filepath", "")
                assert not fp.startswith("/"), f"Path should be relative: {fp}"

        asyncio.run(_test())

    def test_search_before_index(self):
        async def _test():
            import nexus_mcp.server as server_module
            mcp = server_module.create_server()
            result = await _call_tool(mcp, "search", {"query": "test"})
            assert "error" in result

        asyncio.run(_test())
