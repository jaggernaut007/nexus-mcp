"""Tests for scripts/build_llms_full.py, which generates llms-full.txt."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "build_llms_full", ROOT / "scripts" / "build_llms_full.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_header_and_intro():
    text = _load_script().build()
    assert text.startswith("# Nexus-MCP\n\n> ")
    assert "concatenated for LLM consumption." in text


def test_build_sections_in_order():
    script = _load_script()
    text = script.build()
    positions = [
        text.index(f"# Section {n}: {title}")
        for n, (title, _) in enumerate(script.SECTIONS, start=1)
    ]
    assert positions == sorted(positions)
    assert len(positions) == len(script.SECTIONS) == 5


def test_build_includes_source_doc_bodies():
    script = _load_script()
    text = script.build()
    for _, rel_path in script.SECTIONS:
        first_line = (ROOT / rel_path).read_text(encoding="utf-8").splitlines()[0]
        assert first_line in text


def test_build_ends_with_single_newline():
    text = _load_script().build()
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_llms_full_is_up_to_date():
    """Fails when a source doc changed and llms-full.txt was not regenerated."""
    committed = (ROOT / "llms-full.txt").read_text(encoding="utf-8")
    assert committed == _load_script().build(), (
        "llms-full.txt is stale. Run: python scripts/build_llms_full.py"
    )
