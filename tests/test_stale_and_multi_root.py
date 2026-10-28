"""Stale snippets are not served, and every root of a multi-root index is searched."""

import asyncio
import os

import pytest

from nexus_mcp import core_api
from tests.conftest import _setup_indexed


@pytest.fixture
def no_background_reindex(monkeypatch):
    """The background reindex would race the assertions; the staleness check stays real."""
    monkeypatch.setattr(core_api, "_trigger_background_reindex", lambda *a, **k: None)


def test_search_changed_file_has_no_stored_snippet(tmp_path, no_background_reindex):
    """A stored snippet of a file that changed on disk (or a planted index) is not served."""
    project = tmp_path / "project"
    project.mkdir()
    target = project / "planted.py"
    target.write_text("def planted_handler():\n    return 'PLANTED_TEXT'\n")
    asyncio.run(_setup_indexed(project, tmp_path / ".nexus"))

    target.write_text("def planted_handler():\n    return 'real'\n")
    stat = target.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime + 100))

    result = core_api.search("planted_handler", live_grep=False)

    assert "Results in changed files have no snippet." in result["warning"]
    hits = [r for r in result["results"] if r.get("filepath") == "planted.py"]
    assert hits, result
    assert all("PLANTED_TEXT" not in r.get("code_snippet", "") for r in hits)
    assert any(r.get("changed_since_index") for r in hits)


def test_search_unchanged_file_keeps_its_snippet(tmp_path, no_background_reindex):
    project = tmp_path / "project"
    project.mkdir()
    (project / "kept.py").write_text("def kept_handler():\n    return 'KEPT_TEXT'\n")
    asyncio.run(_setup_indexed(project, tmp_path / ".nexus"))

    result = core_api.search("kept_handler", live_grep=False)

    assert any("KEPT_TEXT" in r.get("code_snippet", "") for r in result["results"])


@pytest.fixture
def two_roots(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("def shared_name():\n    return 1\n")
    (second / "b.py").write_text("from a import shared_name\n\nVALUE = shared_name()\n")
    (second / "notes.txt").write_text("only live grep sees zebra_marker_42\n")  # not indexed
    monkeypatch.chdir(tmp_path)
    asyncio.run(_setup_indexed(f"{first},{second}", tmp_path / ".nexus"))
    return first, second


def test_add_references_covers_every_root(two_roots):
    from nexus_mcp.state import get_state

    result: dict = {}
    core_api._add_references(result, get_state(), "shared_name")

    files = set(result["references"]["files"])
    assert "first/a.py" in files and "second/b.py" in files


def test_search_live_grep_covers_every_root(two_roots):
    _, second = two_roots
    result = core_api.search("zebra_marker_42", live_grep=True, detail="full")

    assert any(
        r["absolute_path"].endswith("second/notes.txt")
        and str(r.get("search_mode", "")).startswith("live_grep")
        for r in result["results"]
    ), result
