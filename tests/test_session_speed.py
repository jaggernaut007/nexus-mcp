"""Tests for the in-session changes: source before tests, text references, warm start."""

import shutil

import pytest

from nexus_mcp import core_api
from nexus_mcp.engines import fusion
from nexus_mcp.engines.live_grep import LiveGrepEngine
from nexus_mcp.server import SERVER_INSTRUCTIONS


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_orders.py",
        "src/app/tests/helpers.py",
        "pkg/orders_test.go",
        "web/src/cart.test.ts",
        "web/__tests__/cart.js",
        "django/dispatch/tests.py",
        "conftest.py",
        "C:\\repo\\tests\\test_a.py",
    ],
)
def test_is_test_path_test_files(path):
    assert fusion.is_test_path(path)


@pytest.mark.parametrize(
    "path",
    ["src/orders.py", "src/contest.py", "src/latest/api.py", "src/attestation.py", "testing.md"],
)
def test_is_test_path_source_files(path):
    assert not fusion.is_test_path(path)


@pytest.mark.parametrize("query", ["test_login", "the fixture for orders", "pytest setup", "Tests"])
def test_query_mentions_tests_true(query):
    assert fusion.query_mentions_tests(query)


@pytest.mark.parametrize("query", ["where is the latest order", "contest rules", "attest a key"])
def test_query_mentions_tests_false(query):
    assert not fusion.query_mentions_tests(query)


def _results(*paths):
    return [{"filepath": p, "id": i} for i, p in enumerate(paths)]


def test_demote_test_files_moves_tests_last_and_keeps_order():
    results = _results("tests/test_a.py", "src/a.py", "tests/test_b.py", "src/b.py")
    out = fusion.demote_test_files("how are orders priced", results)
    assert [r["filepath"] for r in out] == [
        "src/a.py", "src/b.py", "tests/test_a.py", "tests/test_b.py",
    ]


def test_demote_test_files_keeps_order_when_query_asks_for_tests():
    results = _results("tests/test_a.py", "src/a.py")
    assert fusion.demote_test_files("test for order pricing", results) == results


def test_demote_test_files_empty_and_no_path():
    assert fusion.demote_test_files("orders", []) == []
    assert fusion.demote_test_files("orders", [{"id": 1}]) == [{"id": 1}]


@pytest.fixture
def ref_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "budget.py").write_text(
        "def ensure_budget(n):\n    return n\n\n\ndef ensure_budget_strict(n):\n    return n\n"
    )
    (tmp_path / "src" / "runtime.py").write_text(
        "from budget import ensure_budget\n\nCHECK = ensure_budget\nensure_budget(1)\n"
    )
    (tmp_path / "tests" / "test_budget.py").write_text("ensure_budget(2)\n")
    return tmp_path


def _engines(root):
    """One engine per available backend, so both command lines are tested."""
    engines = []
    full = LiveGrepEngine(str(root))
    if full.rg_path:
        engines.append(full)
    if shutil.which("grep"):
        grep_only = LiveGrepEngine(str(root))
        grep_only.rg_path = None
        engines.append(grep_only)
    return engines


def test_references_whole_word_grouped_source_first(ref_repo):
    engines = _engines(ref_repo)
    assert engines, "ripgrep or grep must exist on the test machine"
    for engine in engines:
        refs = engine.references("ensure_budget")
        assert list(refs["files"]) == ["src/budget.py", "src/runtime.py", "tests/test_budget.py"]
        # `ensure_budget_strict` on line 5 is another word and must not match.
        assert refs["files"]["src/budget.py"] == [1]
        assert refs["files"]["src/runtime.py"] == [1, 3, 4]
        assert refs["total_files"] == 3
        assert refs["total_lines"] == 5
        assert refs["truncated"] is False


def test_references_caps_and_reports_totals(ref_repo):
    for engine in _engines(ref_repo):
        refs = engine.references("ensure_budget", max_files=1, max_lines_per_file=2)
        assert list(refs["files"]) == ["src/budget.py"]
        assert refs["total_files"] == 3
        assert refs["truncated"] is True
        refs = engine.references("ensure_budget", max_files=5, max_lines_per_file=2)
        assert refs["files"]["src/runtime.py"] == [1, 3]
        assert refs["truncated"] is True


def test_references_qualified_name_and_option_like_name(ref_repo):
    for engine in _engines(ref_repo):
        assert engine.references("budget.ensure_budget")["total_lines"] == 5
        # A name that looks like an option must be searched, not parsed as a flag.
        assert engine.references("--files")["total_lines"] == 0
        assert engine.references("  ") is None


def test_references_no_backend_returns_none(ref_repo):
    engine = LiveGrepEngine(str(ref_repo))
    engine.rg_path = engine.grep_path = None
    assert engine.references("ensure_budget") is None


@pytest.fixture
def priced_repo(tmp_path):
    """One source function and three tests that repeat its words."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "pricing.py").write_text(
        'def price_order(order):\n    """Price an order with its discount."""\n'
        "    return order.total - order.discount\n"
    )
    tests = "\n\n".join(
        f"def test_price_order_discount_{i}():\n"
        f'    """Price an order with its discount, case {i}."""\n'
        f"    assert price_order(order_{i}) == {i}"
        for i in range(3)
    )
    (tmp_path / "tests" / "test_pricing.py").write_text(tests + "\n")
    return tmp_path


def _search_tool(codebase, tmp_path, query, graph_calls):
    import asyncio

    from tests.conftest import _call_tool, _setup_indexed

    async def run():
        mcp, _, _ = await _setup_indexed(codebase, tmp_path / ".nexus")
        get_graph = get_state().graph_engine
        graph_calls.append(get_graph is not None)
        return await _call_tool(mcp, "search", {"query": query})

    from nexus_mcp.state import get_state

    return asyncio.run(run())


def test_search_puts_source_before_tests(priced_repo, tmp_path):
    out = _search_tool(priced_repo, tmp_path, "price an order with its discount", [])
    paths = [r["filepath"] for r in out["results"]]
    assert paths[0] == "src/pricing.py"
    first_test = next(i for i, p in enumerate(paths) if p.startswith("tests/"))
    assert all(p.startswith("tests/") for p in paths[first_test:])
    assert len(paths) == 4, "no result may be removed"


def test_search_keeps_engine_order_when_the_query_asks_for_tests(priced_repo, tmp_path):
    out = _search_tool(priced_repo, tmp_path, "test for price order discount", [])
    assert out["results"][0]["filepath"] == "tests/test_pricing.py"


def test_search_does_not_use_the_graph_list_by_default(priced_repo, tmp_path, monkeypatch):
    has_graph = []
    out = _search_tool(priced_repo, tmp_path, "price_order", has_graph)
    assert has_graph == [True]
    assert "graph" not in out["engines_used"]


def test_search_uses_the_graph_list_when_its_weight_is_positive(
    priced_repo, tmp_path, monkeypatch
):
    monkeypatch.setenv("NEXUS_FUSION_WEIGHT_GRAPH", "0.2")
    from nexus_mcp.config import reset_settings

    reset_settings()
    out = _search_tool(priced_repo, tmp_path, "price_order", [])
    assert "graph" in out["engines_used"]


class _State:
    def __init__(self, root):
        self.codebase_path = root


def test_references_only_for_a_name_without_graph_node(ref_repo):
    out = core_api._references_only(_State(ref_repo), "CHECK", "callers")
    assert out["total"] == 0 and out["callers"] == []
    assert out["references"]["files"] == {"src/runtime.py": [3]}
    assert "No graph node" in out["note"]


def test_references_only_unknown_name_and_callees_stay_errors(ref_repo):
    state = _State(ref_repo)
    assert "error" in core_api._references_only(state, "no_such_name_anywhere", "callers")
    assert "error" in core_api._references_only(state, "ensure_budget", "callees")


def test_add_references_survives_a_failing_search(ref_repo, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("no grep")

    monkeypatch.setattr(LiveGrepEngine, "references", boom)
    result = {"symbol": "x"}
    core_api._add_references(result, _State(ref_repo), "x")
    assert result == {"symbol": "x"}


def test_warm_up_without_index_returns_false():
    # conftest sets NEXUS_AUTO_RESTORE=false, so there is nothing to attach.
    assert core_api.warm_up() is False


def test_warm_up_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("bad index")

    monkeypatch.setattr(core_api, "restore_session", boom)
    assert core_api.warm_up() is False


def test_warm_up_runs_one_search(monkeypatch):
    calls = []

    class _Engine:
        def search(self, query, limit):
            calls.append((query, limit))
            return []

    from nexus_mcp.state import get_state

    monkeypatch.setattr(core_api, "restore_session", lambda: True)
    monkeypatch.setattr(get_state(), "vector_engine", _Engine())
    assert core_api.warm_up() is True
    assert calls == [("warm up", 1)]


def test_instructions_do_not_ask_for_a_status_call_first():
    text = SERVER_INSTRUCTIONS.lower()
    assert "no setup call is needed" in text
    assert "call `status`" not in text
    assert len(SERVER_INSTRUCTIONS) <= 2048
