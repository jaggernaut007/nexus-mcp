"""Tests for the follow-up fixes after the pre-merge audit.

Safe memory migration, auditing of rejected calls,
staleness on the graph tools, and a shared storage folder that holds another project.
"""

import asyncio
import logging
from unittest.mock import patch

import pytest

import nexus_mcp.core_api as core_api
import nexus_mcp.server as server_module
from nexus_mcp.config import reset_settings
from nexus_mcp.core.graph_models import identifier_words
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.state import get_state
from tests.conftest import _call_tool
from tests.test_embedding_loading import _memory_store, _note


class TestIdentifierWords:
    @pytest.mark.parametrize("name,words", [
        ("create_order", ("create", "order")),
        ("CreateOrder", ("create", "order")),
        ("HTTPServer", ("http", "server")),
        ("getHTTP2Client", ("get", "http", "2", "client")),
        ("OAuth2Token", ("o", "auth", "2", "token")),
        ("base64_encode", ("base", "64", "encode")),
    ])
    def test_identifier_words(self, name, words):
        assert identifier_words(name) == words


class TestMemoryMigrationIsSafe:
    def test_a_failed_copy_into_the_temp_table_leaves_the_old_table_untouched(self, tmp_path):
        _memory_store(tmp_path, 384).remember(_note("m1", "keep me"))
        second = _memory_store(tmp_path, 768)

        original = second._db.create_table

        def failing_create(name, *args, **kwargs):
            table = original(name, *args, **kwargs)
            if name.endswith("__migrating"):
                table.add = lambda rows: (_ for _ in ()).throw(RuntimeError("disk full"))
            return table

        second._db.create_table = failing_create
        with pytest.raises(RuntimeError):
            second.remember(_note("m2", "new"))

        names = second._table_names()
        assert "memories" in names and "memories__migrating" not in names
        assert [m.content for m in _memory_store(tmp_path, 384).recall("keep", limit=5)] == [
            "keep me"
        ]

    def test_a_migration_cut_off_after_the_drop_is_finished_on_the_next_open(self, tmp_path):
        first = _memory_store(tmp_path, 384)
        first.remember(_note("m1", "keep me"))
        second = _memory_store(tmp_path, 768)

        # Simulate a crash: the old table is dropped, the temp table holds the data,
        # and the main table was never refilled.
        old = second._db.open_table("memories")
        rows = old.to_arrow().to_pylist()
        from nexus_mcp.memory.memory_store import _make_memory_schema

        temp = second._db.create_table(
            "memories__migrating", schema=_make_memory_schema(768), mode="overwrite"
        )
        temp.add([{**r, "vector": [0.1] * 768} for r in rows])
        second._db.drop_table("memories")

        third = _memory_store(tmp_path, 768)
        assert [m.content for m in third.recall("keep", limit=5)] == ["keep me"]
        assert "memories__migrating" not in third._table_names()

    def test_a_leftover_temp_table_is_dropped_when_the_main_table_fits(self, tmp_path):
        store = _memory_store(tmp_path, 384)
        store.remember(_note("m1", "keep me"))
        from nexus_mcp.memory.memory_store import _make_memory_schema

        store._db.create_table("memories__migrating", schema=_make_memory_schema(384))
        reopened = _memory_store(tmp_path, 384)
        reopened.recall("keep", limit=5)
        assert "memories__migrating" not in reopened._table_names()


class TestRejectedCallsAreAudited:
    def test_an_invalid_enum_value_leaves_an_audit_record(self, caplog):
        from fastmcp.exceptions import ValidationError

        mcp = server_module.create_server()
        with caplog.at_level(logging.INFO, logger="nexus.audit"):
            with pytest.raises(ValidationError):
                asyncio.run(_call_tool(mcp, "graph", {"symbol_name": "x", "direction": "up"}))
        records = [r.getMessage() for r in caplog.records if r.name == "nexus.audit"]
        assert any('"invalid_arguments"' in m and '"graph"' in m for m in records), records

    def test_a_valid_call_is_not_logged_as_invalid(self, caplog):
        mcp = server_module.create_server()
        with caplog.at_level(logging.INFO, logger="nexus.audit"):
            asyncio.run(_call_tool(mcp, "status"))
        records = [r.getMessage() for r in caplog.records if r.name == "nexus.audit"]
        assert records and not any("invalid_arguments" in m for m in records)

    def test_an_error_raised_inside_a_tool_is_not_logged_as_invalid(self, caplog):
        mcp = server_module.create_server()
        with caplog.at_level(logging.INFO, logger="nexus.audit"):
            asyncio.run(_call_tool(mcp, "graph", {"symbol_name": "x"}))  # not indexed: error dict
        records = [r.getMessage() for r in caplog.records if r.name == "nexus.audit"]
        assert not any("invalid_arguments" in m for m in records)


class TestStalenessOnGraphTools:
    def test_require_indexed_starts_a_reindex_when_files_changed(self, tmp_path):
        state = get_state()
        state.codebase_path = tmp_path
        state.codebase_paths = [tmp_path]
        state.graph_engine = RustworkxCodeGraph()
        with patch.object(core_api, "_get_staleness", return_value={"stale": True}), \
                patch.object(core_api, "_trigger_background_reindex") as trigger:
            result, err = core_api.require_indexed()
        assert err is None and result is state
        trigger.assert_called_once_with(tmp_path, [tmp_path])

    def test_require_indexed_does_nothing_when_the_index_is_fresh(self, tmp_path):
        state = get_state()
        state.codebase_path = tmp_path
        state.graph_engine = RustworkxCodeGraph()
        with patch.object(core_api, "_get_staleness", return_value={"stale": False}), \
                patch.object(core_api, "_trigger_background_reindex") as trigger:
            core_api.require_indexed()
        trigger.assert_not_called()


class TestSharedStorageHoldingAnotherProject:
    def test_indexing_project_b_over_the_storage_of_project_a_does_not_mix_them(
        self, tmp_path, monkeypatch
    ):
        from tests.conftest import _mock_embedding_service

        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "alpha.py").write_text("def only_in_a():\n    return 1\n")
        (b / "beta.py").write_text("def only_in_b():\n    return 2\n")
        monkeypatch.setenv("NEXUS_STORAGE_DIR", str(tmp_path / "storage"))
        reset_settings()

        with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
            get_svc.return_value = _mock_embedding_service()
            mcp = server_module.create_server()
            asyncio.run(_call_tool(mcp, "index", {"path": str(a)}))
            asyncio.run(_call_tool(mcp, "index", {"path": str(b)}))

        names = {n.name for n in get_state().graph_engine.nodes.values()}
        assert "only_in_b" in names
        assert "only_in_a" not in names
