"""`IndexingPipeline.rebuild_graph` parses the files again for the graph only (ADR-027, 2.3).

It is for an index whose `extractor_version` is old: the vectors and the full-text index stay,
and no embedding model runs. The stores are real; only the embedding model is replaced.
"""

import json

import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.core.graph_models import NodeType
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import EXTRACTOR_VERSION, IndexingPipeline

pytestmark = pytest.mark.usefixtures("fake_embeddings")

SERVER = (
    "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
    '@app.get("/orders/{order_id}")\ndef get_order(order_id):\n    return {}\n'
)
CLIENT = 'import requests\n\n\ndef pay(order_id):\n    return requests.post("/payments")\n'


def _meta(root):
    return json.loads((root / ".nexus" / "index_metadata.json").read_text())


def _write_meta(root, meta):
    (root / ".nexus" / "index_metadata.json").write_text(json.dumps(meta))


def _routes(pipeline):
    return {
        n.name: n.metadata["routes"]
        for n in pipeline.graph_engine.nodes.values()
        if n.node_type == NodeType.FUNCTION and "routes" in n.metadata
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "links.json"))
    root = tmp_path / "svc"
    root.mkdir()
    (root / "api.py").write_text(SERVER)
    (root / "client.py").write_text(CLIENT)
    reset_settings()
    settings = Settings()
    settings.storage_dir = str(root / ".nexus")
    settings.storage_base = ""
    IndexingPipeline(settings).index(root)
    return root, settings


def test_the_index_stores_routes_and_calls_in_the_saved_graph(repo):
    root, settings = repo
    fresh = IndexingPipeline(settings)
    assert fresh._restore_graph()
    assert _routes(fresh) == {"get_order": [{"method": "GET", "path": "/orders/{}"}]}
    [pay] = [n for n in fresh.graph_engine.nodes.values() if n.name == "pay"]
    assert pay.metadata["http_calls"] == [{"method": "POST", "path": "/payments"}]


def test_the_extractor_version_is_two_or_more():
    assert EXTRACTOR_VERSION >= 2


def test_rebuild_graph_adds_routes_to_a_graph_from_an_older_extractor(repo):
    root, settings = repo
    # Make the store look like one from the older extractor: no routes in the saved graph.
    old = IndexingPipeline(settings)
    old._restore_graph()
    for node in old.graph_engine.nodes.values():
        node.metadata.pop("routes", None)
        node.metadata.pop("http_calls", None)
    old._persist_graph()
    meta = _meta(root)
    meta["extractor_version"] = EXTRACTOR_VERSION - 1
    _write_meta(root, meta)

    pipeline = IndexingPipeline(settings)
    count = pipeline.rebuild_graph()
    assert count == 2  # two files parsed again
    assert _routes(pipeline) == {"get_order": [{"method": "GET", "path": "/orders/{}"}]}
    assert _meta(root)["extractor_version"] == EXTRACTOR_VERSION
    restored = IndexingPipeline(settings)
    assert restored._restore_graph()  # the saved graph is newer than the metadata
    assert _routes(restored) == _routes(pipeline)


def test_rebuild_graph_runs_no_embedding_and_keeps_the_vectors(repo, fake_embeddings):
    root, settings = repo
    pipeline = IndexingPipeline(settings)
    before = pipeline.vector_engine.count()
    fake_embeddings.embed.reset_mock()
    pipeline.rebuild_graph()
    assert fake_embeddings.embed.call_count == 0
    assert IndexingPipeline(settings).vector_engine.count() == before


def test_rebuild_graph_keeps_the_repo_identity(repo):
    root, settings = repo
    before = _meta(root)
    IndexingPipeline(settings).rebuild_graph()
    after = _meta(root)
    assert after["repo_id"] == before["repo_id"]
    assert after["mtimes"] == before["mtimes"]


def test_rebuild_graph_leaves_out_a_file_that_is_gone(repo):
    root, settings = repo
    (root / "client.py").unlink()
    pipeline = IndexingPipeline(settings)
    assert pipeline.rebuild_graph() == 1
    assert not [n for n in pipeline.graph_engine.nodes.values() if n.name == "pay"]


def test_rebuild_graph_with_no_index_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    reset_settings()
    settings = Settings()
    settings.storage_dir = str(tmp_path / "empty" / ".nexus")
    settings.storage_base = ""
    assert IndexingPipeline(settings).rebuild_graph() == 0
    assert not (tmp_path / "empty" / ".nexus" / "index_metadata.json").exists()


def test_link_rebuilds_the_graph_of_an_older_extractor_and_does_not_embed(
    repo, tmp_path, monkeypatch, fake_embeddings
):
    root, settings = repo
    primary = tmp_path / "front"
    primary.mkdir()
    monkeypatch.chdir(primary)
    meta = _meta(root)
    meta["extractor_version"] = EXTRACTOR_VERSION - 1
    _write_meta(root, meta)
    fake_embeddings.embed.reset_mock()
    assert cli.dispatch(["link", str(root)]) == 0
    assert fake_embeddings.embed.call_count == 0
    after = _meta(root)
    assert after["extractor_version"] == EXTRACTOR_VERSION
    assert after["repo_id"] == meta["repo_id"]


def test_an_incremental_reindex_keeps_an_older_extractor_version(repo):
    """Only the changed files get the new facts, so the store must not claim the new version."""
    root, settings = repo
    meta = _meta(root)
    meta["extractor_version"] = EXTRACTOR_VERSION - 1
    _write_meta(root, meta)
    settings.graph_path.touch()  # the graph file must be newer than the metadata
    (root / "api.py").write_text(SERVER + "\n\ndef extra():\n    return 1\n")
    IndexingPipeline(settings).incremental_index(root)
    assert _meta(root)["extractor_version"] == EXTRACTOR_VERSION - 1


def test_a_full_index_writes_the_current_extractor_version(repo):
    root, settings = repo
    meta = _meta(root)
    meta["extractor_version"] = EXTRACTOR_VERSION - 1
    _write_meta(root, meta)
    IndexingPipeline(settings).index(root)
    assert _meta(root)["extractor_version"] == EXTRACTOR_VERSION


def test_an_incremental_reindex_does_not_stamp_a_store_that_has_no_version(repo):
    """A store with no `extractor_version` is from before links: `link` must build it again."""
    root, settings = repo
    meta = _meta(root)
    del meta["extractor_version"]
    _write_meta(root, meta)
    settings.graph_path.touch()
    (root / "api.py").write_text(SERVER + "\n\ndef extra():\n    return 1\n")
    IndexingPipeline(settings).incremental_index(root)
    assert _meta(root).get("extractor_version", 0) < 1


def test_rebuild_graph_refuses_a_read_only_pipeline(repo):
    root, settings = repo
    with pytest.raises(RuntimeError, match="read-only"):
        IndexingPipeline(settings, read_only=True).rebuild_graph()
