"""Tests for Pydantic v2 input/output schemas (Phase 5c)."""

import pytest
from pydantic import ValidationError

from nexus_mcp.schemas.inputs import (
    ForgetInput,
    ImpactInput,
    IndexInput,
    RecallInput,
    RememberInput,
    SearchInput,
    SymbolNameInput,
)
from nexus_mcp.schemas.responses import (
    CalleesResponse,
    CallersResponse,
    ErrorResponse,
    FindSymbolResponse,
    ForgetResponse,
    HealthResponse,
    ImpactResponse,
    IndexResponse,
    MemoryResponse,
    RecallResponse,
    SearchResponse,
    StatusResponse,
)

# --- Input validation ---


class TestIndexInput:
    def test_valid_path(self):
        inp = IndexInput(path="/some/path")
        assert inp.path == "/some/path"

    def test_null_bytes_rejected(self):
        with pytest.raises(ValidationError, match="null bytes"):
            IndexInput(path="/some/\x00path")

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            IndexInput(path="   ")


class TestSearchInput:
    def test_valid_query(self):
        inp = SearchInput(query="find auth handler")
        assert inp.query == "find auth handler"
        assert inp.limit == 10
        assert inp.mode == "hybrid"

    def test_null_bytes_rejected(self):
        with pytest.raises(ValidationError, match="null bytes"):
            SearchInput(query="test\x00query")

    def test_query_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            SearchInput(query="x" * 10001)

    def test_empty_query_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            SearchInput(query="   ")

    def test_limit_clamped(self):
        assert SearchInput(query="test", limit=0).limit == 1
        assert SearchInput(query="test", limit=200).limit == 100

    def test_invalid_mode(self):
        with pytest.raises(ValidationError, match="Invalid mode"):
            SearchInput(query="test", mode="invalid")


class TestSymbolNameInput:
    def test_valid_name(self):
        inp = SymbolNameInput(name="my_function")
        assert inp.name == "my_function"

    def test_null_bytes_rejected(self):
        with pytest.raises(ValidationError, match="null bytes"):
            SymbolNameInput(name="func\x00name")

    def test_too_long(self):
        with pytest.raises(ValidationError, match="too long"):
            SymbolNameInput(name="x" * 501)

    def test_no_alphanumeric(self):
        with pytest.raises(ValidationError, match="alphanumeric"):
            SymbolNameInput(name="!!!")


class TestImpactInput:
    def test_max_depth_clamped(self):
        assert ImpactInput(symbol_name="foo", max_depth=0).max_depth == 1
        assert ImpactInput(symbol_name="foo", max_depth=100).max_depth == 50


class TestRememberInput:
    def test_valid(self):
        inp = RememberInput(content="remember this")
        assert inp.content == "remember this"

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            RememberInput(content="   ")


class TestRecallInput:
    def test_limit_clamped(self):
        assert RecallInput(query="test", limit=0).limit == 1
        assert RecallInput(query="test", limit=100).limit == 50


class TestForgetInput:
    def test_all_optional(self):
        inp = ForgetInput()
        assert inp.memory_id == ""
        assert inp.tags == ""
        assert inp.memory_type == ""


# --- Response models ---


class TestErrorResponse:
    def test_basic_error(self):
        resp = ErrorResponse(error="Something went wrong")
        d = resp.model_dump()
        assert d["error"] == "Something went wrong"
        assert d["code"] is None

    def test_error_with_code(self):
        resp = ErrorResponse(error="Denied", code="PERMISSION_DENIED")
        assert resp.code == "PERMISSION_DENIED"


class TestStatusResponse:
    def test_round_trip(self):
        resp = StatusResponse(
            version="0.1.0",
            indexed=True,
            codebase_path="/some/path",
            memory={"peak_rss_mb": 123.4},
            vector_chunks=500,
        )
        d = resp.model_dump()
        assert d["version"] == "0.1.0"
        assert d["indexed"] is True
        assert d["vector_chunks"] == 500


class TestHealthResponse:
    def test_round_trip(self):
        resp = HealthResponse(
            status="healthy",
            uptime_seconds=42.5,
            indexed=False,
            engines={"vector": False, "bm25": False, "graph": False, "memory": False},
        )
        d = resp.model_dump()
        assert d["status"] == "healthy"
        assert d["engines"]["vector"] is False


class TestIndexResponse:
    def test_round_trip(self):
        resp = IndexResponse(
            total_files=10,
            total_symbols=50,
            total_chunks=30,
            time_seconds=1.5,
        )
        d = resp.model_dump()
        assert d["total_files"] == 10


class TestSearchResponse:
    def test_round_trip(self):
        resp = SearchResponse(
            query="test",
            total=2,
            search_mode="hybrid",
            engines_used=["vector", "bm25"],
            results=[{"id": "1", "score": 0.9}],
        )
        d = resp.model_dump()
        assert d["total"] == 2
        assert len(d["results"]) == 1


class TestFindSymbolResponse:
    def test_round_trip(self):
        resp = FindSymbolResponse(total=1, symbols=[{"name": "foo"}])
        d = resp.model_dump()
        assert d["total"] == 1


class TestCallersCalleesResponse:
    def test_callers(self):
        resp = CallersResponse(symbol="foo", total=0, callers=[])
        assert resp.model_dump()["symbol"] == "foo"

    def test_callees(self):
        resp = CalleesResponse(symbol="bar", total=1, callees=[{"name": "baz"}])
        assert resp.model_dump()["total"] == 1


class TestImpactResponse:
    def test_round_trip(self):
        resp = ImpactResponse(
            symbol="foo",
            max_depth=10,
            total_impacted=0,
            impacted_symbols=[],
            impacted_files={},
        )
        d = resp.model_dump()
        assert d["symbol"] == "foo"


class TestMemoryResponses:
    def test_memory_response(self):
        resp = MemoryResponse(id="abc", status="stored")
        assert resp.model_dump()["id"] == "abc"

    def test_recall_response(self):
        resp = RecallResponse(query="test", total=0, memories=[])
        assert resp.model_dump()["total"] == 0

    def test_forget_response(self):
        resp = ForgetResponse(deleted_count=3)
        assert resp.model_dump()["deleted_count"] == 3
