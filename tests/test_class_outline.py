"""`find_symbol` shows an outline of a long class, not its body (django cost split, 2026-10-27)."""

import asyncio

import pytest

from nexus_mcp import core_api
from tests.conftest import _setup_indexed

PAD = "\n".join(f"        step_{{i}}_{j} = {j}  # padding of the method body" for j in range(8))


def _method(i: int) -> str:
    body = PAD.replace("{i}", str(i))
    return (
        f"    def method_{i}(self, value):\n"
        f"        def inner_{i}():\n"
        f"            return value\n"
        f"{body}\n"
        f"        return inner_{i}()\n"
    )


LONG_CLASS = (
    "class Ledger(Base):\n"
    '    """Keeps the credits of each provider."""\n'
    "\n"
    "    limit = 10\n"
    "\n"
    "    @property\n"
    + "\n".join(_method(i) for i in range(12))
)

SHORT_CLASS = (
    "class Small:\n"
    '    """A short class."""\n'
    "\n"
    "    def one(self):\n"
    "        return 1\n"
)


@pytest.fixture
def project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ledger.py").write_text("class Base:\n    pass\n\n\n" + LONG_CLASS)
    (src / "small.py").write_text(SHORT_CLASS)
    asyncio.run(_setup_indexed(tmp_path, tmp_path / ".nexus"))
    return tmp_path


class TestLongClass:
    def test_find_symbol_long_class_gets_an_outline_and_no_body(self, project):
        result = core_api.find_symbol("Ledger")
        symbol = result["symbols"][0]
        assert "code" not in symbol
        outline = symbol["outline"]
        assert outline.startswith("class Ledger(Base):")
        assert "Keeps the credits of each provider." in outline
        assert "limit = 10" in outline
        assert "# 12 methods:" in outline
        assert "def method_0(self, value):" in outline
        assert "def method_11(self, value):" in outline
        assert "padding of the method body" not in outline
        assert "Class.method" in result["hint"]

    def test_find_symbol_outline_leaves_out_nested_functions(self, project):
        outline = core_api.find_symbol("Ledger")["symbols"][0]["outline"]
        assert "inner_0" not in outline

    def test_find_symbol_outline_head_ends_before_the_first_decorator(self, project):
        outline = core_api.find_symbol("Ledger")["symbols"][0]["outline"]
        head = outline.split("# 12 methods:")[0]
        assert "@property" not in head

    def test_find_symbol_outline_gives_the_lines_of_each_method(self, project):
        symbols = core_api.find_symbol("Ledger, Ledger.method_3")["symbols"]
        outline = symbols[0]["outline"]
        method = next(s for s in symbols if s["name"].endswith("method_3"))
        line = next(x for x in outline.split("\n") if "method_3" in x)
        assert line.strip().startswith(f"{method['start_line']}-")

    def test_find_symbol_outline_is_much_smaller_than_the_class(self, project):
        outline = core_api.find_symbol("Ledger")["symbols"][0]["outline"]
        assert len(outline) * 3 < len(LONG_CLASS)

    def test_find_symbol_class_method_still_gives_code(self, project):
        symbols = core_api.find_symbol("Ledger.method_3")["symbols"]
        assert "step_3_7 = 7" in symbols[0]["code"]


class TestShortClassAndFallback:
    def test_find_symbol_short_class_keeps_its_code(self, project):
        result = core_api.find_symbol("Small")
        assert "def one(self):" in result["symbols"][0]["code"]
        assert "outline" not in result["symbols"][0]
        assert "hint" not in result

    def test_find_symbol_long_class_with_no_methods_keeps_its_code(self, tmp_path):
        fields = "\n".join(
            f"    field_{i}: int = {i}  # a field with a long comment" for i in range(60)
        )
        (tmp_path / "data.py").write_text(f"class Record:\n{fields}\n")
        asyncio.run(_setup_indexed(tmp_path, tmp_path / ".nexus"))
        symbol = core_api.find_symbol("Record")["symbols"][0]
        assert "field_0: int = 0" in symbol["code"]
        assert "outline" not in symbol

    def test_class_outline_outside_the_roots_is_empty(self, project, tmp_path_factory):
        from nexus_mcp.state import get_state

        state = get_state()
        node = core_api._resolve_symbol(state.graph_engine, "Ledger")[0]
        other = tmp_path_factory.mktemp("elsewhere")
        assert core_api._class_outline(state, node, [other]) == ""


class TestOutlineWithSharedBudget:
    def test_find_symbol_long_class_after_big_symbols_still_gets_an_outline(self, tmp_path):
        """Earlier names use most of the budget. The class must not become a cut body."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "ledger.py").write_text("class Base:\n    pass\n\n\n" + LONG_CLASS)
        for name in ("big_one", "big_two"):
            lines = "\n".join(f"    v_{j} = {j}  # a line of padding text" for j in range(140))
            (src / f"{name}.py").write_text(f"def {name}():\n{lines}\n    return 0\n")
        asyncio.run(_setup_indexed(tmp_path, tmp_path / ".nexus"))

        result = core_api.find_symbol("big_one, big_two, Ledger")
        ledger = next(s for s in result["symbols"] if s["name"] == "Ledger")

        assert "code" not in ledger
        assert ledger["outline"].startswith("class Ledger(Base):")
