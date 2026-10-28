"""`find_symbol` after a code review of 2026-10-26: file nodes, changed files, budgets."""

import asyncio

import pytest

from nexus_mcp import core_api
from tests.conftest import _setup_indexed

BODY = "\n".join(f"    step_{i} = {i}  # padding of the function body" for i in range(40))


@pytest.fixture
def mains(tmp_path):
    """Three `main` functions in three files, two of them in files that are named `main.py`."""
    for rel in ("src/cli/main.py", "src/web/main.py", "src/run.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def main():\n{BODY}\n    return 0\n")
    return tmp_path


def _index(codebase, tmp_path):
    asyncio.run(_setup_indexed(codebase, tmp_path / ".nexus"))


class TestFileNodes:
    def test_functions_come_before_the_node_of_a_file_with_the_same_name(self, mains, tmp_path):
        _index(mains, tmp_path)
        result = core_api.find_symbol("main")
        types = [s["type"] for s in result["symbols"]]
        assert "module" in types, types  # the graph holds one node for each file
        assert types.index("module") > max(i for i, t in enumerate(types) if t == "function")

    def test_all_three_functions_get_code_and_a_file_node_gets_none(self, mains, tmp_path):
        _index(mains, tmp_path)
        symbols = core_api.find_symbol("main")["symbols"]
        functions = [s for s in symbols if s["type"] == "function"]
        assert len(functions) == 3
        assert all("def main()" in s["code"] for s in functions)
        assert all("code" not in s for s in symbols if s["type"] == "module")


class TestChangedFile:
    def test_code_is_left_out_and_a_warning_given_when_the_file_changed(self, tmp_path):
        run = tmp_path / "run.py"
        run.write_text("def main():\n    return 1\n\n\ndef helper_fn():\n    return 2\n")
        _index(tmp_path, tmp_path)
        run.write_text("import os\nimport re\nimport sys\nimport json\n" + run.read_text())
        result = core_api.find_symbol("helper_fn")
        symbol = result["symbols"][0]
        assert "code" not in symbol  # line 5 is not `helper_fn` any more
        assert "changed" in result["warning"]

    def test_an_unchanged_file_gives_code_and_no_warning(self, tmp_path):
        (tmp_path / "run.py").write_text("def helper_fn():\n    return 2\n")
        _index(tmp_path, tmp_path)
        result = core_api.find_symbol("helper_fn")
        assert "return 2" in result["symbols"][0]["code"]
        assert "warning" not in result


class TestBudgets:
    def test_a_small_leftover_budget_gives_no_stub(self, mains, tmp_path, monkeypatch):
        _index(mains, tmp_path)
        one = len(core_api.find_symbol("main")["symbols"][0]["code"])
        monkeypatch.setattr(core_api, "SYMBOL_CODE_BUDGET", one + 50)
        symbols = core_api.find_symbol("main")["symbols"]
        with_code = [s for s in symbols if "code" in s]
        assert len(with_code) == 1
        assert all("(truncated)" not in s["code"] for s in with_code)

    def test_a_fuzzy_name_with_many_matches_is_capped(self, tmp_path):
        (tmp_path / "many.py").write_text(
            "".join(f"def get_item_{i}():\n    return {i}\n\n\n" for i in range(25))
        )
        _index(tmp_path, tmp_path)
        result = core_api.find_symbol("get_item", exact=False)
        assert len(result["symbols"]) == core_api.MAX_MATCHES_PER_NAME
        assert result["more_matches"] == {"get_item": 25 - core_api.MAX_MATCHES_PER_NAME}


class TestDottedNameFuzzy:
    """From the django benchmark (2026-10-28): `find_symbol("Field.validate", exact=False)`
    returned "not found" in 5 of 9 calls, and the agent went on with Grep and Read."""

    @pytest.fixture
    def ledger(self, tmp_path):
        (tmp_path / "ledger.py").write_text(
            "class Ledger:\n    def post(self, amount):\n        total = amount * 2\n"
            "        return total\n\n    def close(self):\n        return None\n"
        )
        _index(tmp_path, tmp_path)

    def test_find_symbol_dotted_name_with_exact_false_gives_code(self, ledger):
        result = core_api.find_symbol("Ledger.post", exact=False)
        assert "error" not in result, result
        assert "total = amount * 2" in result["symbols"][0]["code"]

    def test_find_symbol_dotted_name_gives_the_same_symbol_exact_or_not(self, ledger):
        fuzzy = core_api.find_symbol("Ledger.post", exact=False)["symbols"]
        exact = core_api.find_symbol("Ledger.post")["symbols"]
        assert [(s["file"], s["start_line"]) for s in fuzzy] == [
            (s["file"], s["start_line"]) for s in exact
        ]

    def test_find_symbol_unknown_dotted_name_with_exact_false_is_not_found(self, ledger):
        result = core_api.find_symbol("Ledger.refund", exact=False)
        assert "not found" in result["error"]


class TestDottedNameChunkFallback:
    """Gaps found by the PR 12 testing audit (2026-10-28) in the chunk-table fallback."""

    @pytest.fixture
    def ledger(self, tmp_path):
        (tmp_path / "ledger.py").write_text(
            "def main():\n    return Ledger().post(1)\n\n\n"
            "class Ledger:\n    def post(self, amount):\n        total = amount * 2\n"
            "        return total\n"
        )
        _index(tmp_path, tmp_path)

    def test_find_symbol_mixed_graph_and_dotted_names(self, ledger):
        result = core_api.find_symbol("main, Ledger.post", exact=False)
        assert "not_found" not in result, result
        codes = " ".join(s.get("code", "") for s in result["symbols"])
        assert "return Ledger().post(1)" in codes and "total = amount * 2" in codes

    def test_find_symbol_partial_dotted_name_exact_false_is_not_found(self, ledger):
        result = core_api.find_symbol("Ledger.pos", exact=False)
        assert "not found" in result["error"]

    def test_find_symbol_dotted_name_detail_full_gives_code(self, ledger):
        result = core_api.find_symbol("Ledger.post", detail="full")
        assert "total = amount * 2" in result["symbols"][0]["code"]

    def test_find_symbol_chunk_lookup_failure_is_not_found(self, ledger, monkeypatch):
        from nexus_mcp.state import get_state

        def broken(names):
            raise RuntimeError("table gone")

        monkeypatch.setattr(get_state().vector_engine, "find_by_symbol_names", broken)
        result = core_api.find_symbol("Ledger.post", exact=False)
        assert "not found" in result["error"]

    def test_find_symbol_nested_class_method(self, tmp_path):
        (tmp_path / "outer.py").write_text(
            "class Outer:\n    class Inner:\n        def go(self):\n"
            "            return 41 + 1\n"
        )
        _index(tmp_path, tmp_path)
        result = core_api.find_symbol("Inner.go", exact=False)
        assert "error" not in result, result
        assert "41 + 1" in result["symbols"][0]["code"]


class TestFindSymbolNameErrors:
    def test_find_symbol_empty_name_is_an_error(self, mains, tmp_path):
        _index(mains, tmp_path)
        assert "error" in core_api.find_symbol("")
        assert "error" in core_api.find_symbol("   ")

    def test_find_symbol_only_separators_is_not_found(self, mains, tmp_path):
        _index(mains, tmp_path)
        assert "error" in core_api.find_symbol(", ,")

    def test_find_symbol_more_names_than_the_cap_are_cut(self, tmp_path):
        names = [f"fn_{i}" for i in range(core_api.FIND_SYMBOL_MAX_NAMES + 3)]
        (tmp_path / "many.py").write_text(
            "".join(f"def {n}():\n    return {i}\n\n" for i, n in enumerate(names))
        )
        _index(tmp_path, tmp_path)
        result = core_api.find_symbol(", ".join(names))
        assert result["total"] == core_api.FIND_SYMBOL_MAX_NAMES
