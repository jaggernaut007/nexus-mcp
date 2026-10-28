"""Tests for CALLS edge extraction and resolution.

These tests parse the real shop_repo fixture with the real ast-grep parser and
build the graph the same way the pipeline does. No CALLS edge is built by hand.
"""

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus_mcp.config import Settings
from nexus_mcp.core.graph_models import NodeType, RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import _import_to_path, _split_call, resolve_calls
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
        [("shop.cache", "shop/cache"), ("lib/b", "lib/b"), ("crate::util", "crate/util")],
    )
    def test_import_to_path(self, imp, expected):
        assert _import_to_path(imp) == expected



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

    def test_common_method_name_on_unknown_receiver_is_not_linked(self, shop_graph):
        # `product_cache.get(sku)`: the receiver type is unknown and `get` is also a
        # dict method, so the resolver makes no edge rather than guess.
        assert _caller_names(shop_graph, "get", "cache.py") == set()

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


def _graph_from(tmp_path, files):
    """Write {relative path: source} under tmp_path, parse, resolve, return the graph."""
    universal = UniversalGraph()
    parser = AstGrepParser()
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _edges(graph):
    return {
        (graph.nodes[r.source_id].name, graph.nodes[r.target_id].name,
         graph.nodes[r.target_id].location.file_path.rsplit("/", 2)[-2])
        for r in graph.get_relationships_by_type(RelationshipType.CALLS)
    }


class TestPrecision:
    """A call gets an edge only when exactly one callee fits."""

    def test_relative_import_picks_the_sibling_not_a_same_named_file(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "pkg_a/store.py": "def make_store():\n    return 1\n",
            "pkg_b/store.py": "def make_store():\n    return 2\n",
            "pkg_a/app.py": "from .store import make_store\n\n\ndef run():\n    make_store()\n",
        })
        assert _edges(graph) == {("run", "make_store", "pkg_a")}

    def test_module_qualified_call_requires_the_import(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "pkg_a/utils.py": "def helper():\n    return 1\n",
            "pkg_b/utils.py": "def helper():\n    return 2\n",
            "main.py": "from pkg_a import utils\n\n\ndef run():\n    utils.helper()\n",
        })
        # `from pkg_a import utils` imports only module "pkg_a", so no module match.
        assert _edges(graph) == set()

    def test_same_method_name_in_two_classes_makes_no_edge(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "a.py": (
                "class Queue:\n    def process(self):\n        pass\n\n\n"
                "class Worker:\n    def process(self):\n        pass\n\n\n"
                "def run(q):\n    q.process()\n"
            ),
        })
        assert _edges(graph) == set()

    def test_common_method_name_inside_the_same_file_makes_no_edge(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "a.py": (
                "class Store:\n    def __init__(self):\n        self._data = {}\n\n"
                "    def get(self, key):\n        return self._data.get(key)\n"
            ),
        })
        assert _edges(graph) == set()

    def test_super_call_links_to_the_parent_method(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "a.py": (
                "class Base:\n    def save(self):\n        pass\n\n\n"
                "class Child(Base):\n    def save(self):\n        super().save()\n"
            ),
        })
        by_class = {n.metadata["parent_class"]: n for n in graph.find_nodes_by_name("save")}
        # Child.save -> Base.save, and Child.save is not linked to itself
        assert [c.metadata["parent_class"] for c in graph.get_callers(by_class["Base"].id)] \
            == ["Child"]
        assert graph.get_callers(by_class["Child"].id) == []

    def test_dunder_calls_make_no_edge(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "a.py": (
                "class A:\n    def __init__(self):\n        pass\n\n\n"
                "class B(A):\n    def __init__(self):\n        super().__init__()\n"
            ),
        })
        assert _edges(graph) == set()

    def test_python_call_never_links_to_a_typescript_function(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "web/orders.ts": "export function handler() { return 1; }\n",
            "svc/main.py": "from web.orders import handler\n\n\ndef run():\n    handler()\n",
        })
        assert _edges(graph) == set()

    def test_class_qualified_call_prefers_the_class_in_scope(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "a/gw.py": "class Gateway:\n    def charge(self):\n        pass\n",
            "b/gw.py": "class Gateway:\n    def charge(self):\n        pass\n",
            "a/use.py": "from .gw import Gateway\n\n\ndef pay():\n    Gateway().charge()\n",
        })
        assert _edges(graph) == {("pay", "charge", "a"), ("pay", "Gateway", "a")}


class TestOtherLanguages:
    def test_typescript_class_methods_link_through_this(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "calc.ts": (
                "export class Calc {\n"
                "  add(a: number) { return this.recalc(a); }\n"
                "  recalc(a: number) { return a; }\n}\n"
            ),
        })
        assert _caller_names(graph, "recalc") == {"add"}

    def test_typescript_index_file_import(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "components/index.ts": "export function idx() { return 1; }\n",
            "app.ts": 'import { idx } from "./components";\nexport function run() { idx(); }\n',
        })
        assert _caller_names(graph, "idx") == {"run"}

    def test_java_implicit_this_call(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "A.java": (
                "class A {\n  void run() { helper(); }\n  void helper() { }\n}\n"
            ),
        })
        assert _caller_names(graph, "helper") == {"run"}

    def test_go_method_call_in_same_file(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "s.go": (
                "package main\n\ntype Server struct{}\n\n"
                "func (s *Server) Run() { s.helper() }\n\n"
                "func (s *Server) helper() {}\n"
            ),
        })
        assert _caller_names(graph, "helper") == {"Run"}

    def test_rust_self_call_stays_inside_its_impl(self, tmp_path):
        graph = _graph_from(tmp_path, {
            "lib.rs": (
                "struct Foo;\nstruct Bar;\n"
                "impl Foo { fn new() -> Foo { Foo } fn make() -> Foo { Self::new() } }\n"
                "impl Bar { fn new() -> Bar { Bar } }\n"
            ),
        })
        foo_new = next(n for n in graph.find_nodes_by_name("new")
                       if n.metadata.get("parent_class") == "Foo")
        bar_new = next(n for n in graph.find_nodes_by_name("new")
                       if n.metadata.get("parent_class") == "Bar")
        assert {c.name for c in graph.get_callers(foo_new.id)} == {"make"}
        assert graph.get_callers(bar_new.id) == []


class TestWarmStart:
    def test_restart_keeps_the_call_graph(self, small_codebase, tmp_path):
        first = _pipeline(tmp_path)
        first.index(small_codebase)

        second = _pipeline(tmp_path)  # new process: empty engine, same storage
        second.incremental_index(small_codebase)

        graph = second.graph_engine
        assert {c.name for c in graph.get_callers(_node(graph, "helper").id)} == {"run"}

    def test_restart_after_an_edit_relinks_unchanged_callers(self, small_codebase, tmp_path):
        _pipeline(tmp_path).index(small_codebase)
        time.sleep(0.05)
        (small_codebase / "lib.py").write_text(
            "def helper():\n    return 10\n\n\ndef other():\n    return 20\n"
        )
        second = _pipeline(tmp_path)
        second.incremental_index(small_codebase)

        graph = second.graph_engine
        assert {c.name for c in graph.get_callers(_node(graph, "helper").id)} == {"run"}

    def test_stale_graph_file_triggers_full_rebuild(self, small_codebase, tmp_path):
        first = _pipeline(tmp_path)
        first.index(small_codebase)
        graph_file = first._settings.graph_path
        old = graph_file.stat().st_mtime - 100
        os.utime(graph_file, (old, old))  # saved before the metadata: a crash left it stale

        second = _pipeline(tmp_path)
        result = second.incremental_index(small_codebase)

        assert result.total_files == 2  # a full index ran
        assert {c.name for c in second.graph_engine.get_callers(
            _node(second.graph_engine, "helper").id)} == {"run"}
