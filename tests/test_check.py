"""Tests for the post-change check (`nexus-mcp-ci check`).

Each end-to-end test makes a real git repository, saves the call graph that the real
parsers build (the stored graph of an index), edits the files, and runs the check. A
Python case and a TypeScript case cover each rule where the rule is not language-bound.
"""

import json
import subprocess
from pathlib import Path

import pytest

from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.evaluate import check
from nexus_mcp.evaluate.cli import dispatch, run_check
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.persistence.store import GraphPersistence

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [*_GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def _write(repo: Path, files: dict) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _index(repo: Path) -> Path:
    """Save the graph of the working tree as the stored graph of an index."""
    universal, parser = UniversalGraph(), AstGrepParser()
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts or ".nexus" in path.parts:
            continue
        if path.is_file() and parser.can_parse(str(path)):
            parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    store = repo / ".nexus"
    store.mkdir(exist_ok=True)
    GraphPersistence(str(store / "graph.db")).save(graph)
    return store


@pytest.fixture
def repo(tmp_path):
    repo = (tmp_path / "proj").resolve()
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _commit_and_index(repo: Path, files: dict) -> Path:
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return _index(repo)


def _run(repo: Path, store: Path, **kwargs):
    return check.run_check(repo, store, base="HEAD", **kwargs)


def _kinds(report):
    return {(f.kind, f.file, f.severity) for f in report.findings}


PY_BASE = {
    "lib.py": "def total(a, b):\n    return a + b\n",
    "app.py": "from lib import total\n\n\ndef run():\n    return total(1, 2)\n",
    "other.py": "from lib import total\n\n\ndef side():\n    return total(3, 4)\n",
    "tests/test_app.py": "from app import run\n\n\ndef test_run():\n    assert run()\n",
}
TS_BASE = {
    "lib.ts": "export const total = (a: number, b: number) => a + b;\n",
    "app.ts": "import { total } from './lib';\n\nexport const run = () => total(1, 2);\n",
    "other.ts": "import { total } from './lib';\n\nexport const side = () => total(3, 4);\n",
    "app.test.ts": "import { run } from './app';\n\ndescribe('run', () => { run(); });\n",
}


class TestParseUnifiedDiff:
    def test_parse_unified_diff_both_sides_of_a_hunk(self):
        text = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -3,2 +3,3 @@ def f():\n-a\n-b\n+c\n+d\n+e\n"
            "@@ -10 +11,0 @@\n-gone\n"
        )
        (diff,) = check.parse_unified_diff(text)
        assert (diff.path, diff.status) == ("x.py", "M")
        first, second = diff.hunks
        assert list(first.old_lines()) == [3, 4] and list(first.new_lines()) == [3, 4, 5]
        assert list(second.old_lines()) == [10] and list(second.new_lines()) == []

    def test_parse_unified_diff_added_and_deleted_files(self):
        text = (
            "diff --git a/n.py b/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/n.py\n"
            "@@ -0,0 +1,2 @@\n+a\n+b\n"
            "diff --git a/d.py b/d.py\ndeleted file mode 100644\n--- a/d.py\n+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n-a\n-b\n-c\n"
        )
        added, deleted = check.parse_unified_diff(text)
        assert (added.path, added.status) == ("n.py", "A")
        assert (deleted.path, deleted.status) == ("d.py", "D")
        assert list(deleted.hunks[0].old_lines()) == [1, 2, 3]

    def test_parse_unified_diff_empty_text(self):
        assert check.parse_unified_diff("") == []


class TestSignatureOf:
    @pytest.mark.parametrize(
        "old,new,same",
        [
            ("def f(a, b):\n    pass", "def f(a, b):\n    return 1", True),
            ("def f(a, b):\n    pass", "def f(a, b, c):\n    pass", False),
            ("def f(a, b):\n    pass", "def f(a, b) -> int:\n    pass", False),
            ("def f(a: int = 1):\n    pass", "def f(a: int = 2):\n    pass", False),
            ("def f(a,\n      b):\n    pass", "def f(a, b):\n    pass", True),
            ("const f = (a) => a;", "const f = (a, b) => a;", False),
            ("function f(a): number {\n}", "function f(a): string {\n}", False),
            ("function f(a): number {\n}", "function f(a): number {\n  g();\n}", True),
        ],
    )
    def test_signature_of_detects_parameter_and_return_changes(self, old, new, same):
        assert (check.signature_of(old) == check.signature_of(new)) is same

    def test_signature_of_text_without_parentheses(self):
        assert check.signature_of("class A:\n    pass") == ("", "")

    def test_signature_of_string_with_parenthesis_inside(self):
        params, _ = check.signature_of('def f(a=")"):\n    pass')
        assert params == 'a=")"'


class TestInnermost:
    def test_innermost_prefers_the_method_over_its_class_and_skips_the_module_body(
        self, tmp_path
    ):
        path = tmp_path / "a.py"
        path.write_text(
            "class A:\n    def m(self):\n        return 1\n\n\nx = start()\n"
        )
        graph = UniversalGraph()
        AstGrepParser().parse_file(str(path), graph)
        nodes = [n for n in graph.nodes.values() if n.node_type != NodeType.MODULE]
        assert check.innermost(nodes, 3).name == "m"
        assert check.innermost(nodes, 1).name == "A"
        assert check.innermost(nodes, 6).name == "<top-level>"

    def test_innermost_line_outside_every_node_is_none(self, tmp_path):
        path = tmp_path / "a.py"
        path.write_text("X = 1\n\n\ndef f():\n    pass\n")
        graph = UniversalGraph()
        AstGrepParser().parse_file(str(path), graph)
        nodes = [n for n in graph.nodes.values() if n.node_type != NodeType.MODULE]
        assert check.innermost(nodes, 1) is None


class TestRunCheck:
    def test_run_check_python_signature_change_flags_untouched_callers(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return a + b + c\n"})
        report = _run(repo, store)
        assert [(s.key, s.status, s.signature_changed) for s in report.changed_symbols] == [
            ("lib.py::function::total", "modified", True)
        ]
        assert ("untouched_caller", "app.py", "high") in _kinds(report)
        assert ("untouched_caller", "other.py", "high") in _kinds(report)
        assert "tests/test_app.py" in report.affected_tests
        assert check.exit_code(report, "high") == 1

    def test_run_check_ts_signature_change_flags_untouched_callers(self, repo):
        store = _commit_and_index(repo, TS_BASE)
        _write(repo, {"lib.ts": "export const total = (a: number, b: number, c: number) => a;\n"})
        report = _run(repo, store)
        assert [(s.key, s.signature_changed) for s in report.changed_symbols] == [
            ("lib.ts::function::total", True)
        ]
        assert ("untouched_caller", "app.ts", "high") in _kinds(report)
        assert ("untouched_caller", "other.ts", "high") in _kinds(report)
        assert "app.test.ts" in report.affected_tests

    def test_run_check_a_caller_updated_in_the_same_diff_is_not_a_finding(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {
            "lib.py": "def total(a, b, c):\n    return a + b + c\n",
            "app.py": "from lib import total\n\n\ndef run():\n    return total(1, 2, 3)\n",
        })
        report = _run(repo, store)
        files = {
            f.file for f in report.findings
            if f.kind == "untouched_caller" and f.symbol == "lib.py::function::total"
        }
        assert files == {"other.py"}  # app.py was updated; other.py was not
        assert {s.key for s in report.changed_symbols} == {
            "lib.py::function::total", "app.py::function::run",
        }

    def test_run_check_caller_in_a_changed_file_but_not_changed_itself(self, repo):
        store = _commit_and_index(repo, {
            **PY_BASE,
            "app.py": (
                "from lib import total\n\n\ndef run():\n    return total(1, 2)\n\n\n"
                "def other_fn():\n    return 1\n"
            ),
        })
        _write(repo, {
            "lib.py": "def total(a, b, c):\n    return a + b + c\n",
            "app.py": (
                "from lib import total\n\n\ndef run():\n    return total(1, 2)\n\n\n"
                "def other_fn():\n    return 2\n"
            ),
        })
        report = _run(repo, store)
        assert ("caller_lines_unchanged", "app.py", "high") in _kinds(report)

    def test_run_check_body_only_change_is_medium_and_fails_only_at_medium(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b):\n    return a - b\n"})
        report = _run(repo, store)
        assert [s.signature_changed for s in report.changed_symbols] == [False]
        assert {f.severity for f in report.findings} == {"medium"}
        assert check.exit_code(report, "high") == 0
        assert check.exit_code(report, "medium") == 1
        assert check.exit_code(report, "never") == 0

    def test_run_check_removed_function_flags_its_callers(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def other(a):\n    return a\n"})
        report = _run(repo, store)
        statuses = {s.key: s.status for s in report.changed_symbols}
        assert statuses["lib.py::function::total"] == "removed"
        assert statuses["lib.py::function::other"] == "added"
        assert ("untouched_caller", "app.py", "high") in _kinds(report)

    def test_run_check_renamed_function_is_a_removal_of_the_old_name(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def grand_total(a, b):\n    return a + b\n"})
        report = _run(repo, store)
        assert ("untouched_caller", "app.py", "high") in _kinds(report)

    def test_run_check_new_untracked_file_is_added_and_has_no_findings(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"fresh.py": "def brand_new():\n    return 1\n"})
        report = _run(repo, store)
        assert [(s.key, s.status) for s in report.changed_symbols] == [
            ("fresh.py::function::brand_new", "added")
        ]
        assert report.findings == []

    def test_run_check_deleted_file_removes_all_its_symbols(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        (repo / "lib.py").unlink()
        report = _run(repo, store)
        assert [(s.key, s.status) for s in report.changed_symbols] == [
            ("lib.py::function::total", "removed")
        ]
        assert ("untouched_caller", "app.py", "high") in _kinds(report)

    def test_run_check_affected_tests_through_a_pytest_fixture(self, repo):
        store = _commit_and_index(repo, {
            "shop/__init__.py": "",
            "shop/db.py": "def connect(url):\n    return url\n",
            "tests/conftest.py": (
                "import pytest\nfrom shop.db import connect\n\n\n"
                "@pytest.fixture\ndef db():\n    return connect('x')\n"
            ),
            "tests/test_a.py": "def test_x(db):\n    assert db\n",
        })
        _write(repo, {"shop/db.py": "def connect(url, timeout):\n    return url\n"})
        report = _run(repo, store)
        assert {"tests/conftest.py", "tests/test_a.py"} <= set(report.affected_tests)

    def test_run_check_affected_tests_through_a_ts_module_body(self, repo):
        store = _commit_and_index(repo, {
            "lib.ts": "export const total = (a: number) => a;\n",
            "lib.test.ts": (
                "import { total } from './lib';\n\ndescribe('t', () => { total(1); });\n"
            ),
        })
        _write(repo, {"lib.ts": "export const total = (a: number, b: number) => a;\n"})
        report = _run(repo, store)
        assert report.affected_tests == ["lib.test.ts"]
        assert ("untouched_caller", "lib.test.ts", "high") in _kinds(report)

    def test_run_check_change_outside_any_function_is_listed(self, repo):
        store = _commit_and_index(repo, {"cfg.py": "LIMIT = 1\n\n\ndef f():\n    pass\n"})
        _write(repo, {"cfg.py": "LIMIT = 2\n\n\ndef f():\n    pass\n"})
        report = _run(repo, store)
        assert report.file_level_changes == ["cfg.py"]
        assert report.changed_symbols == []

    def test_run_check_unsupported_file_in_the_diff_is_counted(self, repo):
        store = _commit_and_index(repo, {**PY_BASE, "notes.txt": "a\n"})
        _write(repo, {"notes.txt": "b\n"})
        report = _run(repo, store)
        assert report.stats["unsupported_files"] == 1
        assert report.findings == []

    def test_run_check_text_reference_finds_a_use_without_a_call_edge(self, repo):
        store = _commit_and_index(repo, {
            **PY_BASE,
            "plugin.py": "REGISTRY = {'t': 'total_amount_for_order'}\n",
            "lib.py": "def total_amount_for_order(a):\n    return a\n",
        })
        _write(repo, {"lib.py": "def total_amount_for_order(a, b):\n    return a\n"})
        report = _run(repo, store)
        kinds = {(f.kind, f.file) for f in report.findings}
        assert ("reference_untouched", "plugin.py") in kinds

    def test_run_check_no_index_raises_exit_code_3(self, repo):
        _write(repo, PY_BASE)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        with pytest.raises(check.CheckError) as err:
            _run(repo, repo / ".nexus")
        assert err.value.code == 3

    def test_run_check_not_a_git_repo_raises_exit_code_2(self, tmp_path):
        (tmp_path / "a.py").write_text("def f():\n    pass\n")
        with pytest.raises(check.CheckError) as err:
            check.run_check(tmp_path, tmp_path / ".nexus", base="HEAD")
        assert err.value.code == 2

    def test_run_check_index_of_another_folder_raises_exit_code_3(self, repo, tmp_path):
        store = _commit_and_index(repo, PY_BASE)
        other = (tmp_path / "other").resolve()
        other.mkdir()
        _git(other, "init", "-q")
        _write(other, {"a.py": "def f():\n    pass\n"})
        _git(other, "add", "-A")
        _git(other, "commit", "-q", "-m", "base")
        with pytest.raises(check.CheckError) as err:
            _run(other, store)
        assert err.value.code == 3

    def test_run_check_no_change_is_clean(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        report = _run(repo, store)
        assert report.changed_symbols == [] and report.findings == []
        assert check.exit_code(report, "low") == 0

    def test_run_check_staged_change_only(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        _git(repo, "add", "lib.py")
        _write(repo, {"other.py": "def side():\n    return 0\n"})  # not staged
        report = check.run_check(repo, store, staged=True)
        assert [s.key for s in report.changed_symbols] == ["lib.py::function::total"]

    def test_run_check_max_findings_truncates_and_says_so(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        report = _run(repo, store, max_findings=1)
        assert len(report.findings) == 1 and report.stats["truncated"] is True


class TestGitBase:
    def test_git_base_explicit_ref(self, repo):
        _write(repo, {"a.py": "x = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one")
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert check.git_base(repo, "HEAD") == head

    def test_git_base_falls_back_to_head_without_a_default_branch(self, repo):
        _write(repo, {"a.py": "x = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one")
        _git(repo, "branch", "-m", "feature")
        assert check.git_base(repo) == _git(repo, "rev-parse", "HEAD").strip()

    def test_git_base_bad_ref_raises(self, repo):
        _write(repo, {"a.py": "x = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one")
        with pytest.raises(check.CheckError):
            check.git_base(repo, "no-such-ref")

    def test_git_show_missing_path_is_none(self, repo):
        _write(repo, {"a.py": "x = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one")
        assert check.git_show(repo, "HEAD", "a.py") == "x = 1\n"
        assert check.git_show(repo, "HEAD", "missing.py") is None


class TestRender:
    def _report(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        return _run(repo, store)

    def test_render_markdown_has_the_three_blocks(self, repo):
        text = check.render_markdown(self._report(repo))
        assert "## Not updated" in text and "## Tests to run" in text
        assert "## Changed symbols" in text and "`app.py::function::run`" in text
        assert "lower bound" in text

    def test_render_json_uses_keys_not_node_ids(self, repo):
        data = json.loads(check.render_json(self._report(repo)))
        assert data["changed_symbols"][0]["key"] == "lib.py::function::total"
        assert not any("func:" in f["caller"] for f in data["findings"])

    def test_render_markdown_clean_report(self, repo):
        store = _commit_and_index(repo, PY_BASE)
        text = check.render_markdown(_run(repo, store))
        assert "No caller or reference was left behind" in text


class TestCli:
    def test_dispatch_check_runs_and_returns_the_exit_code(self, repo, capsys):
        _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        code = dispatch(["check", str(repo), "--base", "HEAD"])
        assert code == 1
        assert "Not updated" in capsys.readouterr().out

    def test_run_check_fail_on_never_exits_zero(self, repo):
        _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        assert run_check([str(repo), "--base", "HEAD", "--fail-on", "never"]) == 0

    def test_run_check_json_format_prints_json(self, repo, capsys):
        _commit_and_index(repo, PY_BASE)
        _write(repo, {"lib.py": "def total(a, b, c):\n    return 1\n"})
        run_check([str(repo), "--base", "HEAD", "--format", "json", "--fail-on", "never"])
        assert json.loads(capsys.readouterr().out)["stats"]["changed_symbols"] == 1

    def test_run_check_no_index_exits_3(self, repo, capsys):
        _write(repo, PY_BASE)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        assert run_check([str(repo), "--base", "HEAD"]) == 3
        assert "index" in capsys.readouterr().err.lower()

    def test_run_check_bad_depth_exits_2(self, repo):
        assert run_check([str(repo), "--depth", "0"]) == 2

    def test_run_index_builds_the_index_of_the_project(self, repo, monkeypatch, capsys):
        # The indexer loads an embedding model, and its first run downloads it: that is
        # the one boundary replaced here.
        calls = []

        def fake_build_index(root, storage):
            calls.append((root, storage))
            return {"rows": [], "files": 7}

        monkeypatch.setattr("nexus_mcp.evaluate.agent.build_index", fake_build_index)
        assert dispatch(["index", str(repo)]) == 0
        assert calls == [(repo, repo / ".nexus")]
        assert "Indexed 7 files" in capsys.readouterr().out

    def test_run_index_failure_exits_1(self, repo, monkeypatch, capsys):
        def broken(root, storage):
            raise RuntimeError("No source code was indexed.")

        monkeypatch.setattr("nexus_mcp.evaluate.agent.build_index", broken)
        assert dispatch(["index", str(repo)]) == 1
        assert "No source code" in capsys.readouterr().err

    def test_run_index_not_a_folder_exits_2(self, tmp_path):
        assert dispatch(["index", str(tmp_path / "missing")]) == 2

    def test_run_check_bad_base_exits_2(self, repo, capsys):
        _commit_and_index(repo, PY_BASE)
        assert run_check([str(repo), "--base", "no-such-ref"]) == 2
        assert "error" in capsys.readouterr().err


class TestIndexAndGitErrors:
    """Exit code 3 for a bad stored graph, and a CheckError for a failed git call."""

    def test_load_graph_corrupt_file_exits_3(self, tmp_path):
        db = tmp_path / "graph.db"
        db.write_bytes(b"not a database")
        with pytest.raises(check.CheckError, match="Cannot read the stored graph") as err:
            check._load_graph(db)
        assert err.value.code == 3

    def test_load_graph_empty_graph_exits_3(self, tmp_path):
        db = tmp_path / "graph.db"
        GraphPersistence(str(db)).save(RustworkxCodeGraph())
        with pytest.raises(check.CheckError, match="empty") as err:
            check._load_graph(db)
        assert err.value.code == 3

    def test_git_nonzero_raises_with_truncated_stderr(self, tmp_path):
        class Done:
            returncode, stdout, stderr = 128, "", "x" * 500

        with pytest.raises(check.CheckError) as err:
            check._git(tmp_path, "rev-parse", "HEAD", run=lambda *a, **k: Done())
        assert err.value.code == 2
        assert len(str(err.value)) < 340

    def test_git_oserror_raises_checkerror(self, tmp_path):
        def boom(*a, **k):
            raise FileNotFoundError("git")

        with pytest.raises(check.CheckError, match="git failed"):
            check._git(tmp_path, "status", run=boom)

    def test_git_nonzero_with_check_false_gives_empty(self, tmp_path):
        class Done:
            returncode, stdout, stderr = 1, "out", "err"

        assert check._git(tmp_path, "x", run=lambda *a, **k: Done(), check=False) == ""
