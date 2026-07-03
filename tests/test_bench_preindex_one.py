"""Tests for benchmarks._preindex_one: one-shot pre-index helper.

nexus_mcp.server.create_server is monkeypatched with a stub exposing just
enough of the FastMCP tool-registry shape (_local_provider._components) for
main() to find and call the "index" tool — no real indexing happens.
"""

import json
import os
import sys

import pytest

import nexus_mcp.server as nexus_server
from benchmarks import _preindex_one


class _FakeComponent:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn


class _FakeProvider:
    def __init__(self, components):
        self._components = components


class _FakeMCP:
    def __init__(self, components):
        self._local_provider = _FakeProvider(components)


def _stub_create_server(index_result):
    def factory():
        return _FakeMCP({"tool:index": _FakeComponent("index", lambda repo_path: index_result)})

    return factory


@pytest.fixture(autouse=True)
def _restore_storage_dir_env():
    """_preindex_one.main() mutates os.environ directly via setdefault(),
    which pytest's monkeypatch fixture does not track/revert (it only
    reverts its own setenv/delenv calls). Restore manually so a test run
    doesn't leak NEXUS_STORAGE_DIR into later tests in the same process."""
    had = "NEXUS_STORAGE_DIR" in os.environ
    old = os.environ.get("NEXUS_STORAGE_DIR")
    yield
    if had:
        os.environ["NEXUS_STORAGE_DIR"] = old
    else:
        os.environ.pop("NEXUS_STORAGE_DIR", None)


def test_main_writes_meta_on_success(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "setup_meta.json"

    monkeypatch.setattr(
        nexus_server, "create_server", _stub_create_server({"files_indexed": 10})
    )
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    _preindex_one.main()

    entries = json.loads(meta_file.read_text())
    assert "myrepo" in entries
    assert entries["myrepo"]["result"] == {"files_indexed": 10}
    assert entries["myrepo"]["index_seconds"] >= 0


def test_main_sets_storage_dir_inside_repo(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    monkeypatch.setattr(nexus_server, "create_server", _stub_create_server({"ok": True}))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    _preindex_one.main()

    assert os.environ.get("NEXUS_STORAGE_DIR") == str(repo_dir / ".nexus")


def test_main_does_not_override_existing_storage_dir(tmp_path, monkeypatch):
    # setdefault semantics: an operator-provided NEXUS_STORAGE_DIR is respected.
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    monkeypatch.setattr(nexus_server, "create_server", _stub_create_server({"ok": True}))
    monkeypatch.setenv("NEXUS_STORAGE_DIR", "/explicit/override")
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    _preindex_one.main()

    assert os.environ.get("NEXUS_STORAGE_DIR") == "/explicit/override"


def test_main_exits_nonzero_on_index_error(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    monkeypatch.setattr(
        nexus_server, "create_server", _stub_create_server({"error": "boom"})
    )
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    with pytest.raises(SystemExit) as exc_info:
        _preindex_one.main()
    assert exc_info.value.code == 1
    assert not meta_file.exists()


def test_main_resets_corrupt_meta_file(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "meta.json"
    meta_file.write_text("{not valid json")

    monkeypatch.setattr(nexus_server, "create_server", _stub_create_server({"ok": True}))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    _preindex_one.main()

    entries = json.loads(meta_file.read_text())
    assert "myrepo" in entries


def test_main_merges_with_existing_meta_entries(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps({"other-repo": {"index_seconds": 5.0}}))

    monkeypatch.setattr(nexus_server, "create_server", _stub_create_server({"ok": True}))
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["_preindex_one.py", str(repo_dir), "myrepo", str(meta_file)]
    )

    _preindex_one.main()

    entries = json.loads(meta_file.read_text())
    assert "other-repo" in entries
    assert "myrepo" in entries
