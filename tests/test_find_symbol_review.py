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
