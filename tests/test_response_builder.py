"""Tests for response builder with verbosity levels."""

from nexus_mcp.formatting.response_builder import ResponseBuilder


def _make_search_result(id_="a", name="func_a", score=0.9):
    return {
        "id": id_,
        "symbol_name": name,
        "symbol_type": "function",
        "score": score,
        "filepath": "src/main.py",
        "line_start": 1,
        "line_end": 10,
        "signature": "def func_a():",
        "docstring": "A test function.",
        "text": "def func_a():\n    pass",
    }


class TestSearchResponse:
    def test_summary_fields(self):
        builder = ResponseBuilder("summary")
        results = [_make_search_result()]
        response = builder.build_search_response(results, "test query")
        assert response["verbosity"] == "summary"
        entry = response["results"][0]
        assert "id" in entry
        assert "symbol_name" in entry
        assert "score" in entry
        assert "filepath" in entry
        # Summary should NOT include these
        assert "signature" not in entry
        assert "text" not in entry

    def test_detailed_fields(self):
        builder = ResponseBuilder("detailed")
        results = [_make_search_result()]
        response = builder.build_search_response(results, "test query")
        entry = response["results"][0]
        assert "signature" in entry
        assert "docstring" in entry
        assert "line_start" in entry

    def test_full_fields(self):
        builder = ResponseBuilder("full")
        results = [_make_search_result()]
        response = builder.build_search_response(results, "test query")
        entry = response["results"][0]
        assert "text" in entry
        assert "signature" in entry

    def test_total_matches_results(self):
        builder = ResponseBuilder("detailed")
        results = [_make_search_result(f"r{i}") for i in range(3)]
        response = builder.build_search_response(results, "query")
        assert response["total"] == len(response["results"])

    def test_scores_rounded(self):
        builder = ResponseBuilder("summary")
        results = [_make_search_result(score=0.123456789)]
        response = builder.build_search_response(results, "query")
        assert response["results"][0]["score"] == 0.1235


class TestExplainResponse:
    def test_summary_explain(self):
        builder = ResponseBuilder("summary")
        symbol = {"name": "parse", "type": "function"}
        search = [_make_search_result()]
        analysis = {"quality": {"overall_score": 85}}
        response = builder.build_explain_response(symbol, search, analysis)
        assert response["symbol"] == symbol
        assert response["related_count"] == 1
        assert response["quality_score"] == 85
        assert "related_code" not in response

    def test_detailed_explain(self):
        builder = ResponseBuilder("detailed")
        symbol = {"name": "parse", "type": "function"}
        search = [_make_search_result()]
        analysis = {"quality": {"overall_score": 85}}
        response = builder.build_explain_response(symbol, search, analysis)
        assert "related_code" in response
        assert "analysis" in response

    def test_full_explain(self):
        builder = ResponseBuilder("full")
        symbol = {"name": "parse", "type": "function"}
        search = [_make_search_result(f"r{i}") for i in range(15)]
        analysis = {"quality": {"overall_score": 85}}
        response = builder.build_explain_response(symbol, search, analysis)
        # Full caps at 10 related
        assert len(response["related_code"]) <= 10
