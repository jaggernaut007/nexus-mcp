"""`graph` follows HTTP_CALLS edges between repos and inside one repo (ADR-027, phase 2.4).

The stores are real (LanceDB, BM25, graph). Only the embedding model is replaced.
A TypeScript client in the primary project calls a FastAPI route of a linked repo.
"""

import asyncio

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.core.graph_models import RelationshipType
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state

pytestmark = pytest.mark.usefixtures("fake_embeddings")

CLIENT_TS = (
    "export async function loadOrder(id: string) {\n"
    "  const res = await fetch(`/orders/${id}`);\n"
    "  return res.json();\n"
    "}\n\n"
    "export function showPage(id: string) {\n"
    "  return loadOrder(id);\n"
    "}\n"
)
SERVER_PY = (
    "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
    '@app.get("/orders/{order_id}")\n'
    "def get_order(order_id):\n    return load_row(order_id)\n\n\n"
    "def load_row(order_id):\n    return {}\n"
)


def _restart(monkeypatch):
    """Drop all memory of the server, as a new process starts with the stores on disk."""
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A primary project with a TypeScript client, and a linked backend with the route."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary = tmp_path / "primary"
    backend = tmp_path / "backend"
    primary.mkdir()
    backend.mkdir()
    (primary / "ui.ts").write_text(CLIENT_TS)
    (backend / "api.py").write_text(SERVER_PY)
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(backend)]) == 0
    _restart(monkeypatch)
    yield primary, backend
    reset_settings()


@pytest.fixture
def monorepo(tmp_path, monkeypatch):
    """One repo with a frontend folder and a backend folder, and no link."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    root = tmp_path / "mono"
    (root / "frontend").mkdir(parents=True)
    (root / "backend").mkdir()
    (root / "frontend" / "ui.ts").write_text(CLIENT_TS)
    (root / "backend" / "api.py").write_text(SERVER_PY)
    monkeypatch.chdir(root)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(root / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(root)
    _restart(monkeypatch)
    yield root
    reset_settings()


def _names(result, key):
    return {f: names for f, names in result[key].items()}


# --- between repos ------------------------------------------------------------------------


def test_graph_callers_of_a_route_handler_include_the_client_of_another_repo(world):
    result = core_api.graph("get_order", "callers")
    assert "error" not in result
    [(file, entries)] = _names(result, "callers").items()
    assert file == "ui.ts"
    assert len(entries) == 1
    assert entries[0].startswith("loadOrder:")
    assert entries[0].endswith(" via GET /orders/{}")
    assert result["total"] == 1


def test_graph_names_the_linked_repo_in_defined_in(world):
    result = core_api.graph("get_order", "callers")
    assert list(result["defined_in"]) == ["backend/api.py"]


def test_graph_callees_of_a_client_function_include_the_route_handler(world):
    result = core_api.graph("loadOrder", "callees")
    [(file, entries)] = _names(result, "callees").items()
    assert file == "backend/api.py"
    assert entries[0].startswith("get_order:")
    assert entries[0].endswith(" via GET /orders/{}")


def test_graph_transitive_callers_cross_into_the_other_repo(world):
    result = core_api.graph("get_order", "callers", transitive=True)
    files = result["impacted_files"]
    assert sorted(files["ui.ts"]) == ["loadOrder via GET /orders/{}", "showPage"]
    assert result["total_impacted"] == 2


def test_graph_transitive_callers_stop_at_max_depth_across_the_edge(world):
    result = core_api.graph("get_order", "callers", transitive=True, max_depth=1)
    assert result["impacted_files"]["ui.ts"] == ["loadOrder via GET /orders/{}"]


def test_graph_transitive_callers_of_a_helper_cross_into_the_other_repo(world):
    """`load_row` is called by `get_order`, which a client of the primary calls."""
    result = core_api.graph("load_row", "callers", transitive=True)
    assert sorted(result["impacted_files"]) == ["backend/api.py", "ui.ts"]


def test_graph_full_detail_marks_the_hop_and_the_repo(world):
    callees = core_api.graph("loadOrder", "callees", detail="full")["callees"]
    [entry] = callees
    assert entry["name"] == "get_order"
    assert entry["repo"] == "backend"
    assert entry["location"]["file"] == "backend/api.py"
    assert entry["via"] == "GET /orders/{}"
    assert entry["resolution"] == "exact"
    callers = core_api.graph("get_order", "callers", detail="full")["callers"]
    [caller] = callers
    assert caller["location"]["file"] == "ui.ts"
    assert caller["via"] == "GET /orders/{}"
    assert "repo" not in caller


def test_graph_still_lists_the_calls_of_a_symbol_in_a_linked_repo(world):
    result = core_api.graph("get_order", "callees")
    [(file, entries)] = _names(result, "callees").items()
    assert file == "backend/api.py"
    assert entries[0].startswith("load_row:")
    assert "via" not in entries[0]


def test_graph_does_not_add_overrides_or_text_references_for_a_linked_only_symbol(world):
    result = core_api.graph("get_order", "callers")
    assert "references" not in result
    assert "overrides" not in result


def test_graph_gives_no_http_hop_after_the_link_is_removed(world):
    primary, _ = world
    links.remove_link(primary, "backend")
    result = core_api.graph("loadOrder", "callees")
    assert "backend/api.py" not in str(result)
    assert "via" not in str(result)


def test_graph_gives_no_http_hop_when_the_linked_folder_is_gone(world):
    primary, backend = world
    backend.rename(backend.parent / "moved")
    result = core_api.graph("loadOrder", "callees")
    assert "error" not in result
    assert result["total"] == 0
    assert "via" not in str(result)
    [skipped] = [r for r in get_state().linked if r.skip_reason]
    assert skipped.name == "backend"


def test_the_bridge_holds_the_edge_and_no_graph_does(world):
    core_api.graph("get_order", "callers")
    state = get_state()
    assert len(state.bridge) == 1
    for graph in (state.graph_engine, state.linked[0].graph_engine):
        assert not graph.get_relationships_by_type(RelationshipType.HTTP_CALLS)


def test_the_saved_graph_has_no_http_edge(world, tmp_path):
    core_api.graph("get_order", "callers")
    state = get_state()
    pipeline = core_api._pipeline
    assert pipeline is not None
    pipeline._persist_graph()
    fresh = IndexingPipeline(state.settings)
    assert fresh._restore_graph()
    assert not fresh.graph_engine.get_relationships_by_type(RelationshipType.HTTP_CALLS)


def test_the_bridge_is_built_again_after_a_reindex(world):
    primary, _ = world
    assert core_api.graph("get_order", "callers")["total"] == 1
    (primary / "ui.ts").write_text("export function loadOrder(id: string) { return id; }\n")
    asyncio.run(core_api.index(str(primary)))
    result = core_api.graph("get_order", "callers")
    assert result["total"] == 0
    assert "via" not in str(result)
    assert len(get_state().bridge) == 0


# --- inside one repo ----------------------------------------------------------------------


def test_a_monorepo_gets_the_http_edge_with_no_link(monorepo):
    result = core_api.graph("get_order", "callers")
    assert get_state().linked == []
    [(file, entries)] = _names(result, "callers").items()
    assert file == "frontend/ui.ts"
    assert entries[0].endswith(" via GET /orders/{}")


def test_a_monorepo_callees_name_the_backend_file(monorepo):
    result = core_api.graph("loadOrder", "callees")
    [(file, entries)] = _names(result, "callees").items()
    assert file == "backend/api.py"
    assert entries[0].endswith(" via GET /orders/{}")


def test_a_repo_with_no_http_facts_gets_the_same_answer_as_before(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    return g()\n\n\ndef g():\n    return 1\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(root / ".nexus"))
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "links.json"))
    reset_settings()
    IndexingPipeline(Settings()).index(root)
    _restart(monkeypatch)
    result = core_api.graph("g", "callers")
    assert result["callers"] == {"a.py": ["f:1"]}
    assert "via" not in str(result)
    assert not get_state().bridge
