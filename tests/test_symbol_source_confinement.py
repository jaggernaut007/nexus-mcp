"""`find_symbol` and `explain` read the source of a symbol from a path in the index.

The index is data from disk, and a repository can ship its own `.nexus` folder. A path in it
must never make the server read a file outside the indexed roots (a security review of
2026-10-26 showed `/etc/passwd`-style reads through `find_symbol` before this fix).
"""

import json
import os

import pytest

from nexus_mcp import core_api
from nexus_mcp.config import reset_settings
from nexus_mcp.core.graph_models import NodeType, UniversalLocation, UniversalNode
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.state import get_state

SECRET = "SECRET_TOKEN_VALUE_9f2c"


def _node(name, file_path, start=1, end=3):
    return UniversalNode(
        id=f"function:{name}",
        name=name,
        node_type=NodeType.FUNCTION,
        location=UniversalLocation(file_path=file_path, start_line=start, end_line=end),
        language="python",
    )


@pytest.fixture
def project(tmp_path):
    """A project root with one real file, and a secret file outside it."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "good.py").write_text("def good():\n    return 1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(f"{SECRET}\nline two\nline three\n")
    state = get_state()
    state.codebase_path = root
    state.codebase_paths = [root]
    state.graph_engine = RustworkxCodeGraph()
    return root, outside, state


def _find(names):
    return core_api.find_symbol(names)


def _blob(result):
    return json.dumps(result)


class TestFindSymbolConfinement:
    def test_a_file_inside_the_root_gives_its_code(self, project):
        root, _, state = project
        state.graph_engine.add_node(_node("good", str(root / "src" / "good.py"), 1, 2))
        result = _find("good")
        assert "return 1" in result["symbols"][0]["code"]

    def test_an_absolute_path_outside_the_root_gives_no_code(self, project):
        _, outside, state = project
        state.graph_engine.add_node(_node("evil", str(outside / "secret.txt")))
        result = _find("evil")
        assert "code" not in result["symbols"][0]
        assert SECRET not in _blob(result)

    def test_a_dotdot_path_out_of_the_root_gives_no_code(self, project):
        root, _, state = project
        sneaky = str(root / "src" / ".." / ".." / "outside" / "secret.txt")
        state.graph_engine.add_node(_node("evil", sneaky))
        assert SECRET not in _blob(_find("evil"))

    def test_a_symlink_inside_the_root_that_points_outside_gives_no_code(self, project):
        root, outside, state = project
        link = root / "src" / "link.py"
        os.symlink(outside / "secret.txt", link)
        state.graph_engine.add_node(_node("evil", str(link)))
        assert SECRET not in _blob(_find("evil"))

    def test_a_symlink_inside_the_root_that_points_inside_still_works(self, project):
        root, _, state = project
        link = root / "src" / "alias.py"
        os.symlink(root / "src" / "good.py", link)
        # the node is named like the function in the file: the name check needs that
        state.graph_engine.add_node(_node("good", str(link), 1, 2))
        assert "return 1" in _find("good")["symbols"][0]["code"]

    def test_a_directory_or_missing_path_gives_no_code(self, project):
        root, _, state = project
        state.graph_engine.add_node(_node("adir", str(root / "src")))
        state.graph_engine.add_node(_node("gone", str(root / "src" / "gone.py")))
        result = _find("adir, gone")
        assert all("code" not in s for s in result["symbols"])

    def test_a_root_next_door_with_the_same_prefix_is_not_inside(self, project, tmp_path):
        root, _, state = project
        twin = tmp_path / "project-evil"
        twin.mkdir()
        (twin / "x.py").write_text(f"{SECRET}\n")
        state.graph_engine.add_node(_node("evil", str(twin / "x.py"), 1, 1))
        assert SECRET not in _blob(_find("evil"))

    def test_every_root_of_a_multi_folder_index_is_allowed(self, project, tmp_path):
        root, _, state = project
        second = tmp_path / "second"
        second.mkdir()
        (second / "m.py").write_text("def m():\n    return 2\n")
        state.codebase_paths = [root, second]
        state.graph_engine.add_node(_node("m", str(second / "m.py"), 1, 2))
        assert "return 2" in _find("m")["symbols"][0]["code"]

    def test_a_file_over_the_size_limit_gives_no_code(self, project, monkeypatch):
        root, _, state = project
        big = root / "src" / "big.py"
        big.write_text("x = 1\n" * 200_000)  # about 1.2 MB
        state.graph_engine.add_node(_node("big", str(big), 1, 2))
        monkeypatch.setenv("NEXUS_MAX_FILE_SIZE_MB", "1")
        reset_settings()
        try:
            assert "code" not in _find("big")["symbols"][0]
        finally:
            monkeypatch.delenv("NEXUS_MAX_FILE_SIZE_MB")
            reset_settings()

    def test_a_huge_end_line_reads_only_a_bounded_number_of_lines(self, project):
        root, _, state = project
        long_file = root / "src" / "long.py"
        long_file.write_text("".join(f"value_{i} = {i}\n" for i in range(5000)))
        state.graph_engine.add_node(_node("value_0", str(long_file), 1, 10**9))
        code = _find("value_0")["symbols"][0]["code"]
        assert "value_0 = 0" in code
        assert f"value_{core_api.MAX_SYMBOL_SOURCE_LINES} " not in code


class TestExplainConfinement:
    def test_explain_gives_no_code_for_a_path_outside_the_root(self, project):
        _, outside, state = project
        state.graph_engine.add_node(_node("evil", str(outside / "secret.txt")))
        assert SECRET not in _blob(core_api.explain("evil"))


class TestSymbolSourceHelper:
    def test_symbol_source_without_roots_reads_nothing(self, project):
        root, _, _ = project
        assert core_api._symbol_source(str(root / "src" / "good.py"), 1, 2, 500, []) == ""

    def test_symbol_source_inside_the_root_dedents_the_code(self, project):
        root, _, _ = project
        (root / "src" / "m.py").write_text("class A:\n    def f(self):\n        return 1\n")
        out = core_api._symbol_source(str(root / "src" / "m.py"), 2, 3, 500, [root])
        assert out == "def f(self):\n    return 1"
