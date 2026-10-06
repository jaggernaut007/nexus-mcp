"""`detail="compact"` (the default) for `search` and `graph`.

The live benchmark measured a median 16,000-character `search` result and a 46,000-character
`graph` result. These tests pin the smaller default shape and the `detail="full"` escape hatch.
"""

import asyncio
import json

import pytest

from nexus_mcp import core_api
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

INTERNAL_FIELDS = {"id", "score", "rrf_score", "_fusion_sources", "absolute_path"}


@pytest.fixture
def long_function_codebase(tmp_path):
    """A codebase with one function whose source is well over 2,000 characters."""
    src = tmp_path / "src"
    src.mkdir()
    body = "\n".join(f"    value_{i} = compute_step_number_{i}(data) + {i}" for i in range(70))
    (src / "big.py").write_text(
        f'def big_function(data):\n    """Run seventy steps."""\n{body}\n    return value_0\n'
    )
    (src / "small.py").write_text('def small_function():\n    """Tiny."""\n    return 1\n')
    return tmp_path


def _search(codebase, tmp_path, **args):
    async def run():
        mcp, _, _ = await _setup_indexed(codebase, tmp_path / ".nexus")
        return await _call_tool(mcp, "search", {"query": "big_function seventy steps", **args})

    return asyncio.run(run())


class TestTrimSnippet:
    def test_trim_snippet_short_text_is_unchanged(self):
        assert core_api._trim_snippet("a = 1\nb = 2", 600) == "a = 1\nb = 2"

    def test_trim_snippet_cuts_at_a_line_boundary(self):
        code = "\n".join(f"line_{i:03d} = {i}" for i in range(100))
        out = core_api._trim_snippet(code, 200)
        assert out.endswith("\n... (truncated)")
        kept = out.removesuffix("\n... (truncated)")
        assert len(kept) <= 200
        assert all(line.startswith("line_") and " = " in line for line in kept.splitlines())

    def test_trim_snippet_without_a_newline_cuts_at_the_limit(self):
        out = core_api._trim_snippet("x" * 1000, 100)
        assert out == "x" * 100 + "\n... (truncated)"


class TestStripChunkHeader:
    def test_strip_chunk_header_removes_the_path_line(self):
        text = "# src/a.py:12\nfunction: a.f\n\ndef f(): ..."
        assert core_api._strip_chunk_header(text) == "function: a.f\n\ndef f(): ..."

    def test_strip_chunk_header_keeps_text_without_a_header(self):
        assert core_api._strip_chunk_header("x = 1\ny = 2") == "x = 1\ny = 2"

    def test_strip_chunk_header_keeps_a_comment_that_is_not_a_header(self):
        text = "# just a comment\nx = 1"
        assert core_api._strip_chunk_header(text) == text


class TestCompactSearchResult:
    def test_compact_search_result_drops_internal_fields_and_empty_values(self):
        row = {
            "id": "c1", "score": 0.4, "rrf_score": 0.03, "_fusion_sources": ["vector"],
            "absolute_path": "/x/a.py", "filepath": "a.py", "docstring": "", "parent": None,
            "line_start": 0, "symbol_name": "f",
        }
        assert core_api._compact_search_result(row) == {
            "filepath": "a.py", "line_start": 0, "symbol_name": "f",
        }


class TestSearchDetail:
    def test_default_search_is_compact(self, long_function_codebase, tmp_path):
        result = _search(long_function_codebase, tmp_path)
        assert result["results"], result
        for r in result["results"]:
            assert not INTERNAL_FIELDS & set(r), set(r)
            assert r["filepath"] and not r["filepath"].startswith("/")
            assert len(r.get("code_snippet", "")) <= core_api.COMPACT_SNIPPET_CHARS + 20

    def test_compact_snippet_has_no_path_header_and_no_duplicate_fields(
        self, long_function_codebase, tmp_path
    ):
        result = _search(long_function_codebase, tmp_path)
        for r in result["results"]:
            # A graph-list hit has no source text, in compact and in full mode alike.
            snippet = r.get("code_snippet", "")
            assert not snippet.startswith("# "), snippet[:60]
            assert "signature" not in r and "docstring" not in r

    def test_compact_snippets_below_the_top_results_are_shorter(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(8):
            body = "\n".join(f"    step_{j} = shared_step_{j}(data)" for j in range(60))
            (src / f"mod_{i}.py").write_text(f"def shared_function_{i}(data):\n{body}\n")
        result = _search(tmp_path, tmp_path, limit=8)
        lengths = [len(r["code_snippet"]) for r in result["results"]]
        assert len(lengths) == 8, lengths
        top = core_api.COMPACT_TOP_RESULTS
        assert max(lengths[:top]) <= core_api.COMPACT_SNIPPET_CHARS + 20
        assert max(lengths[top:]) <= core_api.COMPACT_TAIL_SNIPPET_CHARS + 20
        assert max(lengths[:top]) > core_api.COMPACT_TAIL_SNIPPET_CHARS + 20

    def test_compact_snippet_of_a_long_function_is_trimmed(self, long_function_codebase, tmp_path):
        result = _search(long_function_codebase, tmp_path)
        big = next(r for r in result["results"] if r["symbol_name"] == "big_function")
        assert big["code_snippet"].endswith("... (truncated)")

    def test_full_search_keeps_the_old_shape_and_longer_snippets(
        self, long_function_codebase, tmp_path
    ):
        result = _search(long_function_codebase, tmp_path, detail="full")
        big = next(r for r in result["results"] if r["symbol_name"] == "big_function")
        assert INTERNAL_FIELDS <= set(big)
        # Since 2026-10-06 the top compact results carry the whole symbol too (so that no
        # file read follows), so the full snippet is no longer the longer one.
        assert len(big["code_snippet"]) > core_api.COMPACT_TAIL_SNIPPET_CHARS + 20
        assert len(big["code_snippet"]) <= core_api.FULL_SNIPPET_CHARS + 20

    def test_compact_result_is_smaller_than_full(self, long_function_codebase, tmp_path):
        # Only three results here, all in the whole-symbol tier: the saving is the dropped
        # fields. `test_compact_snippets_below_the_top_results_are_shorter` tests the tail.
        compact = json.dumps(_search(long_function_codebase, tmp_path))
        full = json.dumps(_search(long_function_codebase, tmp_path, detail="full"))
        assert len(compact) < 0.8 * len(full), (len(compact), len(full))

    def test_search_rejects_an_unknown_detail(self, long_function_codebase, tmp_path):
        async def run():
            await _setup_indexed(long_function_codebase, tmp_path / ".nexus")
            return core_api.search("big_function", detail="verbose")

        assert "error" in asyncio.run(run())


def _graph_with_callers(codebase_path, n_callers):
    """`target` has `n_callers` direct callers, each with a docstring."""
    graph = RustworkxCodeGraph()
    names = ["target"] + [f"caller_{i:03d}" for i in range(n_callers)]
    for name in names:
        graph.add_node(UniversalNode(
            id=f"function:{name}",
            name=name,
            node_type=NodeType.FUNCTION,
            location=UniversalLocation(
                file_path=str(codebase_path / "src" / f"{name}.py"), start_line=3, end_line=9
            ),
            language="python",
            complexity=2,
            line_count=7,
            docstring=f"Docstring of {name}. " * 5,
        ))
    for name in names[1:]:
        graph.add_relationship(UniversalRelationship(
            id=f"calls:{name}->target",
            source_id=f"function:{name}",
            target_id="function:target",
            relationship_type=RelationshipType.CALLS,
        ))
    state = get_state()
    state.graph_engine = graph
    state.codebase_path = codebase_path


class TestGraphDetail:
    def test_graph_default_returns_compact_nodes(self, tmp_path):
        _graph_with_callers(tmp_path, 3)
        result = core_api.graph("target", direction="callers")
        assert result["total"] == 3
        for node in result["callers"]:
            assert set(node) == {"name", "type", "file", "start_line", "end_line"}
            assert node["file"] == f"src/{node['name']}.py"
            assert (node["start_line"], node["end_line"]) == (3, 9)

    def test_graph_full_keeps_docstring_and_id(self, tmp_path):
        _graph_with_callers(tmp_path, 3)
        node = core_api.graph("target", direction="callers", detail="full")["callers"][0]
        assert node["docstring"].startswith("Docstring of caller_")
        assert node["id"].startswith("function:caller_")
        assert node["location"]["file"].startswith("src/")

    def test_graph_rejects_an_unknown_detail(self, tmp_path):
        _graph_with_callers(tmp_path, 1)
        assert "error" in core_api.graph("target", detail="verbose")

    def test_transitive_result_is_capped_but_keeps_every_file(self, tmp_path):
        n = core_api.MAX_IMPACTED_LISTED + 50
        _graph_with_callers(tmp_path, n)
        result = core_api.graph("target", direction="callers", transitive=True)
        assert result["total_impacted"] == n
        assert len(result["impacted_symbols"]) == core_api.MAX_IMPACTED_LISTED
        assert result["truncated"] is True
        assert len(result["impacted_files"]) == n  # the blast radius is still complete
        assert set(result["impacted_symbols"][0]) == {
            "name", "type", "file", "start_line", "end_line"
        }

    def test_transitive_full_is_not_capped(self, tmp_path):
        n = core_api.MAX_IMPACTED_LISTED + 5
        _graph_with_callers(tmp_path, n)
        result = core_api.graph("target", transitive=True, detail="full")
        assert len(result["impacted_symbols"]) == n
        assert "truncated" not in result

    def test_transitive_below_the_cap_is_not_marked_truncated(self, tmp_path):
        _graph_with_callers(tmp_path, 5)
        result = core_api.graph("target", transitive=True)
        assert len(result["impacted_symbols"]) == 5
        assert "truncated" not in result

    def test_compact_graph_result_is_much_smaller_than_full(self, tmp_path):
        _graph_with_callers(tmp_path, 60)
        compact = json.dumps(core_api.graph("target", transitive=True))
        full = json.dumps(core_api.graph("target", transitive=True, detail="full"))
        assert len(compact) < 0.4 * len(full), (len(compact), len(full))
