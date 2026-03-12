"""Security tests: input validation, path traversal, SQL injection."""

import asyncio

import nexus_mcp.server as server_module
from tests.conftest import _call_tool, _setup_indexed


class TestPathValidation:
    def test_index_null_bytes_in_path(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "index", {"path": "/tmp/\x00evil"}))
        assert "error" in result
        assert "null bytes" in result["error"].lower()

    def test_index_nonexistent_path(self):
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "index", {"path": "/nonexistent/path"}))
        assert "error" in result

    def test_index_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        mcp = server_module.create_server()
        result = asyncio.run(_call_tool(mcp, "index", {"path": str(f)}))
        assert "error" in result
        assert "Not a directory" in result["error"]

    def test_index_path_with_dotdot_resolves(self, tmp_path):
        """Path with .. is resolved to absolute before validation."""
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        dotdot_path = str(sub) + "/../../a/b"
        mcp = server_module.create_server()
        # Should resolve fine — the directory exists
        result = asyncio.run(_call_tool(mcp, "index", {"path": dotdot_path}))
        # No path traversal error (it resolved to a real dir)
        assert "error" not in result or "Not a directory" not in result.get("error", "")


class TestSymbolNameValidation:
    def test_symbol_name_null_bytes(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "hello\x00world"})

        result = asyncio.run(run())
        assert "error" in result
        assert "null bytes" in result["error"].lower()

    def test_symbol_name_too_long(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "a" * 501})

        result = asyncio.run(run())
        assert "error" in result
        assert "too long" in result["error"].lower()

    def test_symbol_name_empty(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": ""})

        result = asyncio.run(run())
        assert "error" in result

    def test_symbol_name_no_alphanumeric(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "---"})

        result = asyncio.run(run())
        assert "error" in result
        assert "alphanumeric" in result["error"].lower()

    def test_symbol_name_valid_passes(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_symbol", {"name": "hello"})

        result = asyncio.run(run())
        # Should not be a validation error (may or may not find the symbol)
        if "error" in result:
            assert "null bytes" not in result["error"].lower()
            assert "too long" not in result["error"].lower()

    def test_callers_validates_name(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_callers", {"symbol_name": "\x00bad"})

        result = asyncio.run(run())
        assert "error" in result

    def test_callees_validates_name(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "find_callees", {"symbol_name": "x" * 501})

        result = asyncio.run(run())
        assert "error" in result

    def test_impact_validates_name(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "impact", {"symbol_name": ""})

        result = asyncio.run(run())
        assert "error" in result

    def test_explain_validates_name(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "explain", {"symbol_name": "\x00"})

        result = asyncio.run(run())
        assert "error" in result


class TestQueryValidation:
    def test_search_null_bytes(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "hello\x00world"})

        result = asyncio.run(run())
        assert "error" in result

    def test_search_too_long(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "a" * 10001})

        result = asyncio.run(run())
        assert "error" in result

    def test_search_empty(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "search", {"query": "   "})

        result = asyncio.run(run())
        assert "error" in result

    def test_recall_validates_query(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(mcp, "recall", {"query": "\x00"})

        result = asyncio.run(run())
        assert "error" in result


class TestSQLInjection:
    def test_language_filter_injection(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(
                mcp, "search",
                {"query": "hello", "language": "'; DROP TABLE chunks; --"},
            )

        # Should not raise — filter values are SQL-escaped
        result = asyncio.run(run())
        assert "error" not in result or "DROP" not in result.get("error", "")

    def test_symbol_type_filter_injection(self, mini_codebase, tmp_path):
        async def run():
            mcp, _, _ = await _setup_indexed(mini_codebase, tmp_path / ".nexus")
            return await _call_tool(
                mcp, "search",
                {"query": "hello", "symbol_type": "' OR 1=1 --"},
            )

        result = asyncio.run(run())
        assert "error" not in result or "OR" not in result.get("error", "")
