"""Names in a query, `find_symbol` with code for several names, and the `explain` size.

In the live benchmark of 2026-10-07 the agent sent exact names to grep after a `search`
(16 grep calls in 12 tasks) and read 190 to 270 lines of a file to see one function. Each
of those calls is one more turn, and a turn reads the whole prompt again.
"""

import asyncio
import json

import pytest

from nexus_mcp import core_api
from nexus_mcp.engines.fusion import exact_names_first, query_identifiers
from tests.conftest import _call_tool, _setup_indexed


class TestQueryIdentifiers:
    def test_query_identifiers_finds_snake_case_and_camel_case(self):
        assert query_identifiers("where is should_keep and MailClassification used") == [
            "should_keep", "MailClassification",
        ]

    def test_query_identifiers_takes_constants_with_an_underscore(self):
        assert query_identifiers("AUTO_APPLY_CATEGORIES") == ["AUTO_APPLY_CATEGORIES"]

    def test_query_identifiers_ignores_plain_words(self):
        assert query_identifiers("How does the Governor limit API calls in the UK?") == []

    def test_query_identifiers_uses_each_part_of_a_dotted_name(self):
        assert query_identifiers("core_api.find_symbol") == ["core_api", "find_symbol"]

    def test_query_identifiers_has_no_duplicates_and_a_maximum(self):
        names = query_identifiers("a_b a_b " + " ".join(f"name_{i}" for i in range(20)))
        assert names[0] == "a_b" and names.count("a_b") == 1
        assert len(names) == 5


class TestExactNamesFirst:
    def test_exact_names_first_returns_results_when_there_is_no_definition(self):
        results = [{"id": "1"}, {"id": "2"}]
        assert exact_names_first("q", results, []) is results

    def test_exact_names_first_puts_a_definition_before_the_other_results(self):
        results = [{"id": "1", "filepath": "a.py"}, {"id": "2", "filepath": "b.py"}]
        definition = {"id": "9", "filepath": "c.py", "symbol_name": "parse_config"}
        out = exact_names_first("parse_config", results, [definition])
        assert [r["id"] for r in out] == ["9", "1", "2"]

    def test_exact_names_first_prefers_the_file_that_defines_more_names(self):
        definitions = [
            {"id": "x", "filepath": "other.py", "symbol_name": "score_job"},
            {"id": "a", "filepath": "scorer.py", "symbol_name": "score_job"},
            {"id": "b", "filepath": "scorer.py", "symbol_name": "Scorer.veto_rung"},
        ]
        out = exact_names_first("score_job veto_rung", [], definitions)
        assert [r["id"] for r in out] == ["a", "b", "x"]

    def test_exact_names_first_keeps_the_scores_of_a_result_and_has_no_duplicate(self):
        results = [
            {"id": "1", "filepath": "a.py", "score": 0.5},
            {"id": "2", "filepath": "b.py", "symbol_name": "parse_config", "score": 0.25},
        ]
        definition = {"id": "2", "filepath": "b.py", "symbol_name": "parse_config"}
        out = exact_names_first("parse_config", results, [definition])
        assert [r["id"] for r in out] == ["2", "1"]
        assert out[0]["score"] == 0.25

    def test_exact_names_first_puts_a_test_file_after_a_source_file(self):
        definitions = [
            {"id": "t", "filepath": "tests/test_a.py", "symbol_name": "parse_config"},
            {"id": "s", "filepath": "src/a.py", "symbol_name": "parse_config"},
        ]
        out = exact_names_first("parse_config", [], definitions)
        assert [r["id"] for r in out] == ["s", "t"]


@pytest.fixture
def names_repo(tmp_path):
    """Twelve modules with one function each, one constant and one long function."""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"step_{i:02d}.py").write_text(
            f'def run_step_{i:02d}(data):\n    """Run step {i}."""\n    return data + {i}\n'
        )
    (src / "limits.py").write_text(
        '"""Limits of the service."""\n\nMAX_RETRY_COUNT = 7\n\n\n'
        "class RetryPolicy:\n    def wait_seconds(self, attempt):\n"
        "        return attempt * MAX_RETRY_COUNT\n"
    )
    body = "\n".join(
        f"    total_{i} = data + {i}  # step {i} of the long function" for i in range(250)
    )
    (src / "long.py").write_text(f"def very_long_function(data):\n{body}\n    return total_0\n")
    return tmp_path


def _run(codebase, tmp_path, tool, args):
    async def run():
        mcp, _, _ = await _setup_indexed(codebase, tmp_path / ".nexus")
        return await _call_tool(mcp, tool, args)

    return asyncio.run(run())


class TestSearchWithNames:
    def test_search_puts_the_definition_of_a_name_in_the_query_first(self, names_repo, tmp_path):
        # The embedding service of the tests gives each text the same vector, so the
        # vector order says nothing. Only the name lookup can put this result first.
        result = _run(names_repo, tmp_path, "search", {"query": "where is run_step_07 defined"})
        first = result["results"][0]
        assert (first["symbol_name"], first["filepath"]) == ("run_step_07", "src/step_07.py")
        assert "return data + 7" in first["code_snippet"]

    def test_search_finds_a_constant_and_a_class_by_name(self, names_repo, tmp_path):
        result = _run(
            names_repo, tmp_path, "search", {"query": "MAX_RETRY_COUNT RetryPolicy", "limit": 5}
        )
        top = {r["symbol_name"] for r in result["results"][:2]}
        assert top == {"MAX_RETRY_COUNT", "RetryPolicy"}
        assert result["total"] == 5

    def test_search_full_names_the_exact_name_engine(self, names_repo, tmp_path):
        result = _run(
            names_repo, tmp_path, "search", {"query": "run_step_03", "detail": "full"}
        )
        assert "exact_name" in result["engines_used"]
        assert all({"score", "rrf_score", "_fusion_sources"} <= set(r) for r in result["results"])

    def test_search_hint_points_to_find_symbol(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "search", {"query": "run a step"})
        assert "find_symbol" in result["hint"]


class TestFindSymbolCode:
    def test_find_symbol_returns_the_code_and_no_docstring_copy(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": "run_step_04"})
        symbol = result["symbols"][0]
        assert symbol["code"] == (
            'def run_step_04(data):\n    """Run step 4."""\n    return data + 4'
        )
        assert "docstring" not in symbol

    def test_find_symbol_takes_several_names_in_one_call(self, names_repo, tmp_path):
        result = _run(
            names_repo, tmp_path, "find_symbol",
            {"symbol_name": "run_step_01, RetryPolicy  wait_seconds|no_such_name"},
        )
        names = [s["name"].rsplit(".", 1)[-1] for s in result["symbols"]]
        assert names == ["run_step_01", "RetryPolicy", "wait_seconds"]
        assert all(s["code"] for s in result["symbols"])
        assert result["not_found"] == ["no_such_name"]

    def test_find_symbol_dedents_the_code_of_a_method(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": "wait_seconds"})
        assert result["symbols"][0]["code"].startswith("def wait_seconds(self, attempt):")

    def test_find_symbol_finds_a_constant_that_has_no_graph_node(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": "MAX_RETRY_COUNT"})
        symbol = result["symbols"][0]
        assert (symbol["file"], symbol["type"]) == ("src/limits.py", "variable")
        assert symbol["code"] == "MAX_RETRY_COUNT = 7"

    def test_find_symbol_cuts_the_code_of_a_long_symbol(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": "very_long_function"})
        code = result["symbols"][0]["code"]
        assert code.endswith("... (truncated)")
        assert len(code) <= core_api.SYMBOL_CODE_CHARS + 20
        assert result["symbols"][0]["end_line"] == 252  # the place of the rest is still there

    def test_find_symbol_keeps_one_call_inside_the_code_budget(
        self, names_repo, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(core_api, "SYMBOL_CODE_BUDGET", 100)
        names = "run_step_01 run_step_02 run_step_03"
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": names})
        with_code = [s for s in result["symbols"] if "code" in s]
        assert len(result["symbols"]) == 3
        assert 1 <= len(with_code) < 3
        assert sum(len(s["code"]) for s in with_code) <= 100 + 20

    def test_find_symbol_full_has_the_code_too(self, names_repo, tmp_path):
        result = _run(
            names_repo, tmp_path, "find_symbol", {"symbol_name": "run_step_05", "detail": "full"}
        )
        assert "return data + 5" in result["symbols"][0]["code"]
        assert "relationships_in" in result["symbols"][0]

    def test_find_symbol_without_a_match_is_an_error(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "find_symbol", {"symbol_name": "nothing_a, nothing_b"})
        assert "not found" in result["error"]

    def test_symbol_source_of_a_missing_file_is_empty(self, tmp_path):
        assert core_api._symbol_source(str(tmp_path / "gone.py"), 1, 5, 100) == ""

    def test_split_symbol_names_has_a_maximum_and_no_duplicates(self):
        names = core_api._split_symbol_names("a, a | b " + " ".join(f"n{i}" for i in range(20)))
        assert names[:2] == ["a", "b"]
        assert len(names) == core_api.FIND_SYMBOL_MAX_NAMES


class TestExplainSize:
    def test_explain_detailed_has_the_code_and_no_project_report(self, names_repo, tmp_path):
        result = _run(names_repo, tmp_path, "explain", {"symbol_name": "run_step_06"})
        assert "return data + 6" in result["symbol"]["code"]
        assert "analysis" not in result
        assert all(r["symbol_name"] != "run_step_06" for r in result["related_code"])
        assert not any(r["filepath"].startswith("/") for r in result["related_code"])

    def test_explain_summary_has_no_code(self, names_repo, tmp_path):
        result = _run(
            names_repo, tmp_path, "explain", {"symbol_name": "run_step_06", "verbosity": "summary"}
        )
        assert "code" not in result["symbol"]
        assert "quality_score" in result

    @pytest.mark.parametrize("verbosity,limit", [("detailed", 8000), ("full", 16000)])
    def test_explain_of_a_long_symbol_stays_small(self, names_repo, tmp_path, verbosity, limit):
        result = _run(
            names_repo, tmp_path, "explain",
            {"symbol_name": "very_long_function", "verbosity": verbosity},
        )
        text = json.dumps(result)
        assert len(text) < limit, len(text)
        assert '"vector"' not in text
