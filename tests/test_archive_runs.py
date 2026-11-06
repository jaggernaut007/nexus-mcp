"""scripts/archive_runs.py keeps the small log files of each run and nothing else."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive_runs.py"
spec = importlib.util.spec_from_file_location("archive_runs", SCRIPT)
archive_runs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_runs)


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_archive_copies_logs_and_skips_index_folders(tmp_path):
    results, runs = tmp_path / "results", tmp_path / "runs"
    _write(results / "languages-final" / "vite-live.jsonl")
    _write(results / "languages-final.json")
    _write(results / "languages-final" / "retrieval-work" / "x" / "data.json")
    _write(results / "languages-final" / "notes.txt")
    archive_runs.archive(results, runs)
    kept = sorted(p.relative_to(runs).as_posix() for p in runs.rglob("*") if p.is_file())
    assert kept == ["languages-final/languages-final.json", "languages-final/vite-live.jsonl"]


def test_archive_keeps_a_console_log_under_its_label(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("[vite] done: live\n")
    archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"v2.3.0": str(log)})
    assert (tmp_path / "runs" / "v2.3.0" / "console.log").read_text() == "[vite] done: live\n"


def test_archive_stops_on_a_credential_like_string(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("+ CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abcdefghijklmnop\n")
    with pytest.raises(SystemExit):
        archive_runs.archive(tmp_path / "none", tmp_path / "runs", {"x": str(log)})
    assert not (tmp_path / "runs" / "x").exists()
