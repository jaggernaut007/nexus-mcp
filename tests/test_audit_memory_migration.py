"""Audit tests for MemoryStore._open_or_migrate failure paths."""

import json
from pathlib import Path

import pytest

from tests.test_embedding_loading import _memory_store, _note

BACKUP = "memories-before-model-change.json"


def test_open_or_migrate_empty_table_of_another_width_is_recreated(tmp_path):
    _memory_store(tmp_path, 384)._get_or_create_table()  # table exists, no rows
    second = _memory_store(tmp_path, 768)
    second.remember(_note("m1", "stored after the switch"))

    assert [m.content for m in second.recall("stored", limit=5)] == ["stored after the switch"]
    assert json.loads((tmp_path / BACKUP).read_text()) == []


def test_open_or_migrate_backup_write_failure_raises_and_keeps_old_rows(tmp_path, monkeypatch):
    first = _memory_store(tmp_path, 384)
    first.remember(_note("m1", "keep me"))

    def disk_full(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError):
        _memory_store(tmp_path, 768).remember(_note("m2", "new"))
    monkeypatch.undo()

    assert [m.content for m in _memory_store(tmp_path, 384).recall("keep", limit=5)] == ["keep me"]


def test_open_or_migrate_embedding_failure_keeps_old_table(tmp_path):
    _memory_store(tmp_path, 384).remember(_note("m1", "keep me"))
    second = _memory_store(tmp_path, 768)
    second._embedding_service.embed_batch.side_effect = RuntimeError("model down")

    with pytest.raises(RuntimeError):
        second.remember(_note("m2", "new"))

    assert [m.content for m in _memory_store(tmp_path, 384).recall("keep", limit=5)] == ["keep me"]


def test_open_or_migrate_failure_after_drop_leaves_the_json_backup(tmp_path):
    """The old table is dropped before the new rows are added. The backup is the only copy."""
    _memory_store(tmp_path, 384).remember(_note("m1", "keep me"))
    second = _memory_store(tmp_path, 768)
    second._embedding_service.embed_batch.side_effect = lambda texts, **kw: [
        [0.1] * 5 for _ in texts  # wrong width: the add fails after the drop
    ]

    with pytest.raises(Exception):  # noqa: B017 - the lancedb error type is not public
        second.remember(_note("m2", "new"))

    backup = json.loads((tmp_path / BACKUP).read_text())
    assert [row["content"] for row in backup] == ["keep me"]


def test_open_or_migrate_failure_while_refilling_keeps_rows_after_restart(tmp_path):
    """The refill of the new table fails. The next start must restore the rows from the temp table."""
    first = _memory_store(tmp_path, 384)
    first.remember(_note("m1", "keep me"))
    first.remember(_note("m2", "keep me too"))
    second = _memory_store(tmp_path, 768)
    original = second._db.create_table

    def create(name, *args, **kwargs):
        table = original(name, *args, **kwargs)
        if name == "memories":
            table.add = lambda rows: (_ for _ in ()).throw(OSError("disk full"))
        return table

    second._db.create_table = create
    with pytest.raises(OSError):
        second._get_or_create_table()

    restarted = _memory_store(tmp_path, 768)
    assert sorted(m.content for m in restarted.recall("keep", limit=5)) == [
        "keep me", "keep me too",
    ]
    assert "memories__migrating" not in restarted._table_names()


def test_open_or_migrate_leftover_temp_table_adds_no_duplicates(tmp_path):
    """A temp table that holds the same rows as the main table only gets dropped."""
    store = _memory_store(tmp_path, 384)
    store.remember(_note("m1", "keep me"))
    from nexus_mcp.memory.memory_store import _make_memory_schema

    rows = store._db.open_table("memories").to_arrow().to_pylist()
    store._db.create_table(
        "memories__migrating", schema=_make_memory_schema(384)
    ).add(rows)

    reopened = _memory_store(tmp_path, 384)
    assert [m.content for m in reopened.recall("keep", limit=5)] == ["keep me"]
    assert "memories__migrating" not in reopened._table_names()
