"""More paths of `graph` over HTTP_CALLS edges (ADR-027, phase 2.4).

Real stores, as in `test_graph_cross_repo.py`. Only the embedding model is replaced.
"""

import json
import os

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state

pytestmark = pytest.mark.usefixtures("fake_embeddings")

CLIENT_TS = (
    "export async function loadOrder(id: string) {\n"
    "  const res = await fetch(`/orders/${id}`);\n"
    "  return res.json();\n"
    "}\n"
)
SERVER_PY = (
    "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
    '@app.get("/orders/{order_id}")\n'
    "def get_order(order_id):\n    return load_row(order_id)\n\n\n"
    "def load_row(order_id):\n    return {}\n"
)


def _restart(monkeypatch):
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()


def make_world(tmp_path, monkeypatch, primary_files, backend_files, restart=True):
    """Index a primary project, link a backend, and (by default) start a new process."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary, backend = tmp_path / "primary", tmp_path / "backend"
    for root, files in ((primary, primary_files), (backend, backend_files)):
        root.mkdir()
        for name, source in files.items():
            (root / name).write_text(source)
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(backend)]) == 0
    if restart:
        _restart(monkeypatch)
    return primary, backend


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_settings()


# --- the same symbol in the primary project and in a linked repo ---------------------------


def test_graph_starts_a_symbol_in_both_projects_lists_the_callers_of_both(tmp_path, monkeypatch):
    primary_api = (
        "def get_order(order_id):\n    return 1\n\n\ndef local_caller():\n    return get_order(1)\n"
    )
    make_world(
        tmp_path, monkeypatch, {"ui.ts": CLIENT_TS, "api.py": primary_api}, {"api.py": SERVER_PY}
    )
    result = core_api.graph("get_order", "callers")
    # Both files are named `api.py`: the repo name tells them apart in `defined_in`.
    assert sorted(result["defined_in"]) == ["api.py", "backend/api.py"]
    callers = result["callers"]
    assert [n.split(":")[0] for n in callers["api.py"]] == ["local_caller"]
    [via] = callers["ui.ts"]
    assert via.startswith("loadOrder:") and via.endswith(" via GET /orders/{}")
    assert result["total"] == 2


def test_graph_starts_a_symbol_in_both_projects_keeps_the_hops_of_both_in_full_detail(
    tmp_path, monkeypatch
):
    primary_api = (
        "def get_order(order_id):\n    return 1\n\n\ndef local_caller():\n    return get_order(1)\n"
    )
    make_world(
        tmp_path, monkeypatch, {"ui.ts": CLIENT_TS, "api.py": primary_api}, {"api.py": SERVER_PY}
    )
    full = core_api.graph("get_order", "callers", detail="full")["callers"]
    assert sorted(e["name"] for e in full) == ["loadOrder", "local_caller"]
    assert [e for e in full if "via" in e][0]["name"] == "loadOrder"


def test_graph_starts_a_symbol_in_both_projects_keeps_overrides_and_references_of_the_primary(
    tmp_path, monkeypatch
):
    primary_api = (
        "def get_order(order_id):\n    return 1\n\n\ndef local_caller():\n    return get_order(1)\n"
    )
    make_world(tmp_path, monkeypatch, {"api.py": primary_api}, {"api.py": SERVER_PY})
    result = core_api.graph("get_order", "callers")
    assert [n.split(":")[0] for n in result["callers"]["api.py"]] == ["local_caller"]


def test_graph_transitive_a_symbol_in_both_projects_walks_from_both(tmp_path, monkeypatch):
    primary_api = (
        "def get_order(order_id):\n    return 1\n\n\ndef local_caller():\n    return get_order(1)\n"
    )
    make_world(
        tmp_path, monkeypatch, {"ui.ts": CLIENT_TS, "api.py": primary_api}, {"api.py": SERVER_PY}
    )
    result = core_api.graph("get_order", "callers", transitive=True)
    assert set(result["impacted_files"]) == {"api.py", "ui.ts"}
    assert result["total_impacted"] == 2
    assert sorted(result["defined_in"]) == ["api.py", "backend/api.py"]


# --- caps ---------------------------------------------------------------------------------


@pytest.fixture
def crowd(tmp_path, monkeypatch):
    """Seventy client files in the primary project call the route of the linked repo."""
    files = {
        f"c{i:02d}.ts": (
            f"export function f{i:02d}(id: string) {{ return fetch(`/orders/${{id}}`); }}\n"
        )
        for i in range(70)
    }
    return make_world(tmp_path, monkeypatch, files, {"api.py": SERVER_PY})


def test_graph_compact_caps_the_files_of_the_hops_over_an_http_edge(crowd):
    result = core_api.graph("get_order", "callers")
    assert result["total"] == 70
    assert len(result["callers"]) == core_api.MAX_GRAPH_SOURCE_FILES
    omitted = result["callers_not_listed"]
    assert omitted["files"] == 10
    assert omitted["symbols"] == 10
    assert all(n.endswith(" via GET /orders/{}") for v in result["callers"].values() for n in v)


def test_graph_full_caps_the_hops_over_an_http_edge(crowd):
    result = core_api.graph("get_order", "callers", detail="full")
    assert result["total"] == 70
    assert len(result["callers"]) == core_api.MAX_FULL_NODES
    assert "truncated" in result
    assert all(e["via"] == "GET /orders/{}" for e in result["callers"])


def test_graph_transitive_compact_and_full_cap_the_hops_over_an_http_edge(crowd):
    compact = core_api.graph("get_order", "callers", transitive=True)
    assert compact["total_impacted"] == 70
    assert not compact.get("impacted_symbols")
    assert len(compact["impacted_files"]) == core_api.MAX_GRAPH_SOURCE_FILES
    assert compact["impacted_not_listed"]["files"] == 10
    full = core_api.graph("get_order", "callers", transitive=True, detail="full")
    assert len(full["impacted_symbols"]) == core_api.MAX_FULL_NODES
    assert full["total_impacted"] == 70
    assert "truncated" in full
    # impacted_files stays complete in full detail.
    assert len(full["impacted_files"]) == 70


# --- the bridge after a restore, and when the build fails ---------------------------------------


def test_rebuild_bridge_the_bridge_exists_after_a_restore_before_any_graph_call(
    tmp_path, monkeypatch
):
    make_world(tmp_path, monkeypatch, {"ui.ts": CLIENT_TS}, {"api.py": SERVER_PY})
    assert core_api.restore_session() is True
    state = get_state()
    assert state.bridge is not None
    assert len(state.bridge) == 1
    assert state.bridge.edges[0].route_repo == "backend"


def test_rebuild_bridge_the_bridge_is_none_before_an_index_or_restore(tmp_path, monkeypatch):
    reset_state()
    assert get_state().bridge is None


def test_rebuild_bridge_a_failure_leaves_no_bridge_and_graph_still_answers(tmp_path, monkeypatch):
    from nexus_mcp.indexing import service_resolver

    def boom(*args, **kwargs):
        raise RuntimeError("the resolver failed")

    make_world(tmp_path, monkeypatch, {"ui.ts": CLIENT_TS}, {"api.py": SERVER_PY})
    monkeypatch.setattr(service_resolver, "resolve_services", boom)
    assert core_api.restore_session() is True
    state = get_state()
    assert state.bridge is None
    result = core_api.graph("get_order", "callees")
    assert "error" not in result
    assert [n.split(":")[0] for n in result["callees"]["backend/api.py"]] == ["load_row"]
    callers = core_api.graph("get_order", "callers")
    assert "error" not in callers
    assert callers["total"] == 0
    assert core_api.graph("get_order", "callers", transitive=True)["total_impacted"] == 0


def test_rebuild_bridge_a_failure_after_a_good_bridge_drops_the_old_bridge(tmp_path, monkeypatch):
    """A stale bridge must not outlive a failed rebuild: its node ids may be gone."""
    from nexus_mcp.indexing import service_resolver

    make_world(tmp_path, monkeypatch, {"ui.ts": CLIENT_TS}, {"api.py": SERVER_PY})
    core_api.restore_session()
    state = get_state()
    assert len(state.bridge) == 1

    def boom(*args, **kwargs):
        raise RuntimeError("the resolver failed")

    monkeypatch.setattr(service_resolver, "resolve_services", boom)
    core_api._rebuild_bridge(state)
    assert state.bridge is None
    assert core_api.graph("loadOrder", "callees")["total"] == 0


def test_rebuild_bridge_a_session_with_no_graph_leaves_no_bridge(tmp_path, monkeypatch):
    reset_state()
    state = get_state()
    state.graph_engine = None
    core_api._rebuild_bridge(state)
    assert state.bridge is None


def test_rebuild_bridge_a_linked_repo_with_no_routes_gives_an_empty_bridge(tmp_path, monkeypatch):
    """A store from the first extractor (version 1) has no route facts in its graph."""
    plain = (
        "def get_order(order_id):\n    return load_row(order_id)\n\n\n"
        "def load_row(order_id):\n    return {}\n"
    )
    primary, backend = make_world(
        tmp_path, monkeypatch, {"ui.ts": CLIENT_TS}, {"api.py": plain}, restart=False
    )
    meta_file = links.store_dir(backend) / "index_metadata.json"
    meta = json.loads(meta_file.read_text())
    meta["extractor_version"] = 1
    stamp = meta_file.stat()
    meta_file.write_text(json.dumps(meta))
    os.utime(meta_file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))  # the graph must not look older
    _restart(monkeypatch)
    assert core_api.restore_session() is True
    state = get_state()
    assert [(r.name, r.skip_reason) for r in state.linked] == [("backend", None)]
    assert not state.bridge
    callees = core_api.graph("loadOrder", "callees")
    assert "error" not in callees
    assert callees["total"] == 0
    callers = core_api.graph("get_order", "callers")
    assert callers["total"] == 0
    assert "via" not in str(callers)
    inner = core_api.graph("get_order", "callees")
    assert inner["callees"] == {"backend/api.py": [inner["callees"]["backend/api.py"][0]]}
    assert inner["callees"]["backend/api.py"][0].startswith("load_row:")


# --- audit of phase 2.4: a linked repo that goes away while a call runs ------------------


def test_a_hop_of_a_repo_that_is_no_longer_attached_does_not_crash(tmp_path, monkeypatch):
    """`index()` replaces `state.linked` in another thread: a hop can name a gone repo."""
    make_world(tmp_path, monkeypatch, {"ui.ts": CLIENT_TS}, {"api.py": SERVER_PY})
    core_api.graph("get_order", "callers")
    state = get_state()
    graph = state.linked[0].graph_engine
    node = next(n for n in graph.nodes.values() if n.name == "get_order")
    hop = ("gone", node, None)
    assert core_api._compact_hop(state, hop)[1].startswith("get_order")
    assert core_api._serialize_hop(state, hop)["name"] == "get_order"
