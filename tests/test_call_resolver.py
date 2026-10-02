"""Tests for CALLS edge extraction and resolution.

These tests parse the real shop_repo fixture with the real ast-grep parser and
build the graph the same way the pipeline does. No CALLS edge is built by hand.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus_mcp.config import Settings
from nexus_mcp.core.graph_models import NodeType, RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import (
    _import_to_path,
    _imports_file,
    _split_call,
    resolve_calls,
)
from nexus_mcp.indexing.pipeline import IndexingPipeline, _transfer_graph, discover_files
from nexus_mcp.parsing.astgrep_parser import AstGrepParser, normalize_call_text

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "shop_repo"


@pytest.fixture(scope="module")
def shop_graph():
    universal = UniversalGraph()
    parser = AstGrepParser()
    for path in sorted(FIXTURE.rglob("*")):
        if path.suffix in (".py", ".ts") and parser.can_parse(str(path)):
            parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _node(graph, name, file_hint=""):
    matches = [
        n for n in graph.find_nodes_by_name(name)
        if n.node_type in (NodeType.FUNCTION, NodeType.CLASS)
        and file_hint in n.location.file_path
    ]
    assert len(matches) == 1, f"{name}: {len(matches)} matches"
    return matches[0]


def _caller_names(graph, name, file_hint=""):
    return {c.name for c in graph.get_callers(_node(graph, name, file_hint).id)}


def _callee_names(graph, name, file_hint=""):
    return {c.name for c in graph.get_callees(_node(graph, name, file_hint).id)}


class TestNormalizeCallText:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("foo", "foo"),
            ("self.db.execute", "self.db.execute"),
            ("PaymentGateway().charge", "PaymentGateway.charge"),
            ("a?.b", "a.b"),
            ("Foo::new", "Foo.new"),
            ("get<T>", "get"),
            ("items[0].run", ""),
            ("(lambda x: x)", ""),
        ],
    )
    def test_normalize_call_text(self, raw, expected):
        assert normalize_call_text(raw) == expected


class TestHelpers:
    def test_split_call_plain(self):
        assert _split_call("foo") == (None, "foo")

    def test_split_call_chain_uses_first_and_last(self):
        assert _split_call("self.db.execute") == ("self", "execute")

    @pytest.mark.parametrize(
        "imp,expected",
        [("shop.cache", "shop/cache"), (".cache", "cache"), ("./cart", "cart"),
         ("../lib/b", "lib/b"), ("crate::util", "crate/util")],
    )
    def test_import_to_path(self, imp, expected):
        assert _import_to_path(imp) == expected

    def test_imports_file_matches_dotted_module(self):
        assert _imports_file(["shop.cache"], "/r/shop/cache.py") is True

    def test_imports_file_no_match(self):
        assert _imports_file(["shop.cache"], "/r/shop/orders.py") is False


class TestShopRepoCallGraph:
    def test_callers_of_reserve_stock(self, shop_graph):
        assert "create_order" in _caller_names(shop_graph, "reserve_stock")

    def test_callees_of_create_order(self, shop_graph):
        callees = _callee_names(shop_graph, "create_order")
        assert {"reserve_stock", "calculate_total", "charge", "send_order_email"} <= callees

    def test_constructor_call_links_to_class(self, shop_graph):
        assert "create_order" in _caller_names(shop_graph, "Order")

    def test_class_qualified_method_call(self, shop_graph):
        assert "cancel_order" in _caller_names(shop_graph, "refund")

    def test_self_method_call(self, shop_graph):
        assert "charge" in _caller_names(shop_graph, "_post")

    def test_call_through_import_alias_chain(self, shop_graph):
        assert "execute" in _caller_names(shop_graph, "connect")

    def test_method_call_on_imported_object(self, shop_graph):
        assert "get_stock" in _caller_names(shop_graph, "get", "cache.py")

    def test_typescript_import_with_double_quotes(self, shop_graph):
        assert "submitOrder" in _caller_names(shop_graph, "cartTotal")

    def test_builtin_method_name_does_not_link_to_project_method(self, shop_graph):
        # os.environ.get / payload.get must not point at TTLCache.get
        callers = _caller_names(shop_graph, "get", "cache.py")
        assert "load_settings" not in callers
        assert "_post" not in callers

    def test_plain_call_to_unimported_name_is_not_linked(self, tmp_path):
        # `callback` is a parameter here. A function with that name elsewhere
        # in the project must not receive an edge.
        (tmp_path / "a.py").write_text("def run(callback):\n    callback()\n")
        (tmp_path / "b.py").write_text("def callback():\n    return 1\n")
        universal = UniversalGraph()
        parser = AstGrepParser()
        for name in ("a.py", "b.py"):
            parser.parse_file(str(tmp_path / name), universal)
        graph = RustworkxCodeGraph()
        _transfer_graph(universal, graph)
        resolve_calls(graph)
        assert graph.get_callers(_node(graph, "callback").id) == []

    def test_method_name_shared_by_two_classes_is_skipped(self, tmp_path):
        (tmp_path / "a.py").write_text("def run(obj):\n    obj.save_all()\n")
        (tmp_path / "b.py").write_text(
            "class A:\n    def save_all(self):\n        pass\n\n\n"
            "class B:\n    def save_all(self):\n        pass\n"
        )
        universal = UniversalGraph()
        parser = AstGrepParser()
        for name in ("a.py", "b.py"):
            parser.parse_file(str(tmp_path / name), universal)
        graph = RustworkxCodeGraph()
        _transfer_graph(universal, graph)
        assert resolve_calls(graph) == 0

    def test_no_self_edges(self, shop_graph):
        for rel in shop_graph.get_relationships_by_type(RelationshipType.CALLS):
            assert rel.source_id != rel.target_id

    def test_transitive_callers_reach_api_layer(self, shop_graph):
        node = _node(shop_graph, "check_stock")
        names = {n.name for n in shop_graph.get_transitive_callers(node.id)}
        assert {"reserve_stock", "create_order", "post_order", "get_stock"} <= names

    def test_resolve_calls_is_idempotent(self, shop_graph):
        before = len(shop_graph.get_relationships_by_type(RelationshipType.CALLS))
        resolve_calls(shop_graph)
        after = len(shop_graph.get_relationships_by_type(RelationshipType.CALLS))
        assert before == after > 0

    def test_edges_carry_resolution_metadata(self, shop_graph):
        rel = shop_graph.get_relationships_by_type(RelationshipType.CALLS)[0]
        assert rel.metadata["resolution"] in {"same_file", "imported", "unique"}
        assert 0 < rel.strength <= 1.0


class TestRemoveRelationshipsByType:
    def test_remove_relationships_by_type_keeps_other_edges(self):
        universal = UniversalGraph()
        parser = AstGrepParser()
        parser.parse_file(str(FIXTURE / "shop" / "inventory.py"), universal)
        graph = RustworkxCodeGraph()
        _transfer_graph(universal, graph)
        resolve_calls(graph)
        contains = len(graph.get_relationships_by_type(RelationshipType.CONTAINS))
        calls = len(graph.get_relationships_by_type(RelationshipType.CALLS))
        assert calls > 0

        removed = graph.remove_relationships_by_type(RelationshipType.CALLS)

        assert removed == calls
        assert len(graph.get_relationships_by_type(RelationshipType.CONTAINS)) == contains
        assert graph.graph.num_edges() == contains

    def test_remove_relationships_by_type_none_present_returns_zero(self):
        assert RustworkxCodeGraph().remove_relationships_by_type(RelationshipType.CALLS) == 0


def _pipeline(tmp_path):
    settings = Settings(storage_dir=str(tmp_path / ".nexus"))
    from nexus_mcp.indexing.embedding_service import EMBEDDING_MODELS

    dims = EMBEDDING_MODELS[settings.embedding_model]["dimensions"]
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
        svc = MagicMock()
        svc.embed.return_value = [0.1] * dims
        svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
        get_svc.return_value = svc
        pipeline = IndexingPipeline(settings)
        pipeline._vector_engine._embedding_service = svc
        return pipeline


@pytest.fixture
def small_codebase(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "lib.py").write_text("def helper():\n    return 1\n\n\ndef other():\n    return 2\n")
    (root / "app.py").write_text(
        "from lib import helper, other\n\n\ndef run():\n    helper()\n    other()\n"
    )
    return root


class TestPipelineBuildsCallEdges:
    def test_index_creates_calls_edges(self, small_codebase, tmp_path):
        pipeline = _pipeline(tmp_path)
        pipeline.index(small_codebase)
        graph = pipeline.graph_engine
        helper = _node(graph, "helper")
        assert {c.name for c in graph.get_callers(helper.id)} == {"run"}

    def test_incremental_reindex_drops_removed_call(self, small_codebase, tmp_path):
        pipeline = _pipeline(tmp_path)
        pipeline.index(small_codebase)
        time.sleep(0.05)
        (small_codebase / "app.py").write_text(
            "from lib import helper\n\n\ndef run():\n    helper()\n"
        )
        pipeline.incremental_index(small_codebase)
        graph = pipeline.graph_engine
        assert {c.name for c in graph.get_callers(_node(graph, "helper").id)} == {"run"}
        assert graph.get_callers(_node(graph, "other").id) == []

    def test_incremental_reindex_relinks_callers_in_unchanged_files(self, small_codebase, tmp_path):
        pipeline = _pipeline(tmp_path)
        pipeline.index(small_codebase)
        time.sleep(0.05)
        (small_codebase / "lib.py").write_text(
            "def helper():\n    return 10\n\n\ndef other():\n    return 20\n"
        )
        pipeline.incremental_index(small_codebase)
        graph = pipeline.graph_engine
        assert {c.name for c in graph.get_callers(_node(graph, "helper").id)} == {"run"}

    def test_discover_files_sees_fixture(self):
        files = discover_files(FIXTURE, Settings())
        assert any(f.name == "orders.py" for f in files)
