"""`search` and `find_symbol` look in the linked repos of the project (ADR-027, phase 2.2).

The stores are real (LanceDB, BM25, graph). Only the embedding model is replaced, so all
vectors are equal and the BM25 list and the exact names decide the order.
"""

import logging

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.engines.bm25_engine import LanceDBBM25Engine
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import get_state, reset_state

pytestmark = pytest.mark.usefixtures("fake_embeddings")

PRIMARY_UI = "def render_page():\n    return 1\n\n\ndef shared_name():\n    return 'ui'\n"
BACKEND_API = (
    "API_LIMIT = 5\n\n\n"
    "def handle_request():\n    return load_orders()\n\n\n"
    "def load_orders():\n    return [1, 2]\n\n\n"
    "def shared_name():\n    return 'api'\n"
)


def _snapshot(store):
    return {
        str(p.relative_to(store)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in store.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An indexed primary project with one linked, indexed backend; all memory then dropped."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary = tmp_path / "primary"
    backend = tmp_path / "backend"
    primary.mkdir()
    backend.mkdir()
    (primary / "ui.py").write_text(PRIMARY_UI)
    (backend / "api.py").write_text(BACKEND_API)
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(backend)]) == 0
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()
    yield primary, backend
    reset_settings()


def _files(result):
    return [r["filepath"] for r in result["results"]]


# --- search -------------------------------------------------------------------------------


def test_search_finds_code_in_a_linked_repo(world):
    primary, backend = world
    result = core_api.search("handle_request")
    first = result["results"][0]
    assert first["filepath"] == "backend/api.py"
    assert first["symbol_name"] == "handle_request"
    assert "load_orders" in first["code_snippet"]


def test_search_full_detail_gives_the_absolute_path_of_a_linked_result(world):
    primary, backend = world
    first = core_api.search("handle_request", detail="full")["results"][0]
    assert first["absolute_path"] == str(backend / "api.py")


def test_search_still_finds_code_of_the_primary_project(world):
    result = core_api.search("render_page")
    assert result["results"][0]["filepath"] == "ui.py"


def test_search_without_a_link_gives_the_same_paths_as_before(world):
    links.remove_link(world[0], "backend")
    result = core_api.search("handle_request")
    assert all(not f.startswith("backend/") for f in _files(result))
    assert get_state().linked == []


def test_search_full_detail_shows_the_repo_path_and_no_internal_field(world):
    result = core_api.search("handle_request", detail="full")
    first = result["results"][0]
    assert first["filepath"] == "backend/api.py"
    assert not any(key.startswith("_repo") for key in first)


def test_search_embeds_the_query_one_time(world, fake_embeddings):
    core_api.restore_session()
    fake_embeddings.embed.reset_mock()
    core_api.search("handle_request")
    queries = [c for c in fake_embeddings.embed.call_args_list if c.kwargs.get("is_query")]
    assert len(queries) == 1


def test_search_filters_by_language_in_a_linked_repo(world):
    assert core_api.search("handle_request", language="python")["results"][0][
        "filepath"
    ] == "backend/api.py"
    assert all(
        not f.startswith("backend/")
        for f in _files(core_api.search("handle_request", language="go"))
    )


def test_search_does_not_write_to_the_linked_store(world):
    primary, backend = world
    core_api.restore_session()
    before = _snapshot(backend / ".nexus")
    core_api.search("handle_request")
    core_api.search("load_orders", detail="full")
    assert _snapshot(backend / ".nexus") == before


def test_search_marks_a_changed_linked_file_and_says_how_to_refresh(world):
    primary, backend = world
    (backend / "api.py").write_text(BACKEND_API + "\n\ndef added():\n    return 3\n")
    before = _snapshot(backend / ".nexus")
    result = core_api.search("handle_request")
    first = result["results"][0]
    assert first["filepath"] == "backend/api.py"
    assert first["changed_since_index"] is True
    assert "code_snippet" not in first
    assert "backend" in result["warning"]
    assert "nexus-mcp-ci link --refresh" in result["warning"]
    assert _snapshot(backend / ".nexus") == before  # a stale linked store is never reindexed


def test_search_has_no_warning_for_a_fresh_linked_repo(world):
    assert "warning" not in core_api.search("handle_request")


def test_search_skips_a_linked_repo_that_is_not_attached(world):
    primary, backend = world
    (backend / ".nexus" / "graph.db").unlink()
    core_api.restore_session()
    assert get_state().linked[0].skip_reason
    result = core_api.search("render_page")
    assert result["results"][0]["filepath"] == "ui.py"
    assert all(not f.startswith("backend/") for f in _files(result))


def test_one_failing_linked_engine_does_not_stop_the_search(world, caplog):
    core_api.restore_session()
    repo = get_state().linked[0]

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    repo.vector_engine.search = broken
    repo.bm25_engine.search = broken
    with caplog.at_level(logging.WARNING):
        result = core_api.search("render_page")
    assert result["results"][0]["filepath"] == "ui.py"
    assert "backend" in caplog.text


# --- the BM25 engine in read-only mode ----------------------------------------------------


def _plain_table(tmp_path):
    """A LanceDB folder with a `chunks` table and no full-text index."""
    import lancedb

    db = lancedb.connect(str(tmp_path / "lancedb"))
    db.create_table(
        "chunks", data=[{"id": "1", "text": "alpha beta", "language": "python", "symbol_type": "x"}]
    )
    return tmp_path / "lancedb"


def test_bm25_read_only_leaves_a_store_without_a_full_text_index_as_it_is(tmp_path):
    store = _plain_table(tmp_path)
    before = _snapshot(store)
    engine = LanceDBBM25Engine(str(store), read_only=True)
    assert engine.search("alpha") == []
    assert engine.ensure_fts_index() is False
    assert _snapshot(store) == before


def test_bm25_read_only_uses_the_full_text_index_that_the_index_run_made(world):
    primary, backend = world
    store = backend / ".nexus"
    before = _snapshot(store)
    engine = LanceDBBM25Engine(str(store / "lancedb"), read_only=True)
    assert engine.search("handle_request")[0]["symbol_name"] == "handle_request"
    assert _snapshot(store) == before


def test_bm25_default_still_creates_the_full_text_index(tmp_path):
    """The default engine is unchanged: the first search makes the index (a write)."""
    store = _plain_table(tmp_path)
    before = _snapshot(store)
    engine = LanceDBBM25Engine(str(store))
    assert engine.search("alpha")[0]["id"] == "1"
    assert engine._fts_index_created is True
    assert _snapshot(store) != before


# --- find_symbol --------------------------------------------------------------------------


def test_find_symbol_finds_a_symbol_of_a_linked_repo(world):
    primary, backend = world
    result = core_api.find_symbol("handle_request")
    [entry] = result["symbols"]
    assert entry["file"] == "backend/api.py"
    assert "load_orders" in entry["code"]
    assert entry["callees"] == {"backend/api.py": ["load_orders:8"]}


def test_find_symbol_full_detail_names_the_repo(world):
    result = core_api.find_symbol("handle_request", detail="full")
    [entry] = result["symbols"]
    assert entry["repo"] == "backend"
    assert entry["location"]["file"] == "backend/api.py"


def test_find_symbol_lists_the_same_name_of_each_repo_primary_first(world):
    result = core_api.find_symbol("shared_name")
    assert result["total"] == 2
    assert [e["file"] for e in result["symbols"]] == ["ui.py", "backend/api.py"]
    assert "'api'" in result["symbols"][1]["code"]


def test_find_symbol_looks_up_a_constant_of_a_linked_repo_in_the_chunk_table(world):
    result = core_api.find_symbol("API_LIMIT")
    [entry] = result["symbols"]
    assert entry["file"] == "backend/api.py"


def test_find_symbol_of_a_name_that_no_repo_has_is_not_found(world):
    assert "not found" in core_api.find_symbol("no_such_name")["error"]


def test_find_symbol_several_names_in_one_call_cross_the_repos(world):
    result = core_api.find_symbol("render_page, load_orders")
    assert sorted(e["file"] for e in result["symbols"]) == ["backend/api.py", "ui.py"]


def test_find_symbol_with_a_changed_linked_file_leaves_out_the_code(world):
    primary, backend = world
    (backend / "api.py").write_text("# moved\n" * 3 + BACKEND_API)
    result = core_api.find_symbol("handle_request")
    [entry] = result["symbols"]
    assert "code" not in entry
    assert "warning" in result


def test_find_symbol_does_not_write_to_the_linked_store(world):
    primary, backend = world
    core_api.restore_session()
    before = _snapshot(backend / ".nexus")
    core_api.find_symbol("handle_request, API_LIMIT, shared_name", detail="full")
    assert _snapshot(backend / ".nexus") == before


def test_find_symbol_without_a_link_gives_the_same_files_as_before(world):
    links.remove_link(world[0], "backend")
    reset_state()
    core_api._pipeline = None
    result = core_api.find_symbol("shared_name")
    assert [e["file"] for e in result["symbols"]] == ["ui.py"]
    assert "repo" not in result["symbols"][0]


# --- added: helpers, modes, error paths (coverage audit of phase 2.2) -------------------------


class _Repo:
    """A bare stand-in for a LinkedRepo: only the fields that the helpers read."""

    def __init__(self, name, vector=None, bm25=None, attached=True, path="/r"):
        from pathlib import Path

        self.name = name
        self.vector_engine = vector
        self.bm25_engine = bm25
        self.attached = attached
        self.path = Path(path)


class _Engine:
    def __init__(self, found=None, error=None):
        self.found = found or []
        self.error = error
        self.calls = []

    def search(self, query, limit=10, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return [dict(r) for r in self.found]

    def find_by_symbol_names(self, names):
        if self.error:
            raise self.error
        return [dict(r) for r in self.found]


def test_merge_by_score_returns_one_list_as_it_is():
    only = [{"id": "a", "score": 0.1}, {"id": "b", "score": 0.9}]
    assert core_api._merge_by_score([only]) is only


def test_merge_by_score_sorts_best_first_and_keeps_primary_first_on_a_tie():
    primary = [{"id": "p1", "score": 0.5}, {"id": "p2", "score": 0.2}]
    linked_list = [{"id": "l1", "score": 0.9}, {"id": "l2", "score": 0.5}]
    merged = core_api._merge_by_score([primary, linked_list])
    assert [r["id"] for r in merged] == ["l1", "p1", "l2", "p2"]


def test_merge_by_score_treats_a_missing_score_as_zero():
    merged = core_api._merge_by_score([[{"id": "x"}], [{"id": "y", "score": 0.1}]])
    assert [r["id"] for r in merged] == ["y", "x"]


def test_attached_linked_without_the_field_gives_an_empty_list():
    class Bare:
        pass

    assert core_api._attached_linked(Bare()) == []


def test_attached_linked_leaves_out_a_repo_that_is_not_attached():
    class S:
        linked = [_Repo("a"), _Repo("b", attached=False)]

    assert [r.name for r in core_api._attached_linked(S())] == ["a"]


def test_linked_stale_warning_without_names_gives_none():
    assert core_api._linked_stale_warning([]) is None


def test_linked_stale_warning_one_name_is_singular():
    text = core_api._linked_stale_warning(["backend"])
    assert text.startswith("Linked repo 'backend' changed")
    assert "nexus-mcp-ci link --refresh" in text


def test_linked_stale_warning_two_names_are_plural():
    text = core_api._linked_stale_warning(["a", "b"])
    assert text.startswith("Linked repos 'a', 'b' changed")


def test_search_linked_vector_mode_skips_bm25():
    bm25 = _Engine([{"id": "k", "score": 1.0}])
    vector = _Engine([{"id": "v", "score": 0.5}])
    repo = _Repo("r", vector=vector, bm25=bm25)
    vec, kw = core_api._search_linked([repo], "q", 5, {}, "vector", [0.1])
    assert [r["id"] for r in vec[0]] == ["v"] and kw == []
    assert vec[0][0]["_repo"] == "r"
    assert bm25.calls == []
    assert vector.calls == [{"query_vector": [0.1]}]


def test_search_linked_bm25_mode_skips_vector():
    vector = _Engine([{"id": "v"}])
    repo = _Repo("r", vector=vector, bm25=_Engine([{"id": "k", "score": 1.0}]))
    vec, kw = core_api._search_linked([repo], "q", 5, {}, "bm25", [0.1])
    assert vec == [] and [r["id"] for r in kw[0]] == ["k"]
    assert vector.calls == []


def test_search_linked_without_a_query_vector_skips_vector():
    vector = _Engine([{"id": "v"}])
    repo = _Repo("r", vector=vector, bm25=None)
    assert core_api._search_linked([repo], "q", 5, {}, "hybrid", None) == ([], [])
    assert vector.calls == []


def test_search_linked_drops_an_empty_bm25_list_and_a_missing_bm25_engine():
    repos = [_Repo("a", bm25=_Engine([])), _Repo("b", bm25=None)]
    assert core_api._search_linked(repos, "q", 5, {}, "bm25", None) == ([], [])


def test_search_linked_failing_repo_is_left_out_and_logged(caplog):
    bad = _Repo("bad", vector=_Engine(error=RuntimeError("v")), bm25=_Engine(error=OSError("b")))
    good = _Repo("good", vector=_Engine([{"id": "v"}]), bm25=_Engine([{"id": "k"}]))
    with caplog.at_level(logging.WARNING):
        vec, kw = core_api._search_linked([bad, good], "q", 5, {}, "hybrid", [0.1])
    assert [r["_repo"] for r in vec[0]] == ["good"] and len(vec) == 1
    assert [r["_repo"] for r in kw[0]] == ["good"] and len(kw) == 1
    assert "Vector search of linked repo 'bad'" in caplog.text
    assert "BM25 search of linked repo 'bad'" in caplog.text


def test_search_linked_passes_the_filters_to_the_engines():
    vector = _Engine([{"id": "v"}])
    bm25 = _Engine([{"id": "k"}])
    repo = _Repo("r", vector=vector, bm25=bm25)
    core_api._search_linked([repo], "q", 5, {"language": "go"}, "hybrid", [0.1])
    # Requirement changed in 2.5: a linked BM25 search also asks for the raw score, so that
    # the lists of several stores are scaled together (`_merge_bm25`).
    assert vector.calls[0]["language"] == "go"
    assert bm25.calls[0] == {"language": "go", "keep_raw": True}


def test_linked_definitions_tags_the_repo_and_swallows_an_error():
    bad = _Repo("bad", vector=_Engine(error=RuntimeError("x")))
    good = _Repo("good", vector=_Engine([{"id": "d"}]))
    found = core_api._linked_definitions([bad, good], ["n"])
    assert found == [{"id": "d", "_repo": "good"}]


def test_prefix_repo_prefixes_relative_paths_and_leaves_absolute_ones():
    repo = _Repo("backend")
    entry = {
        "file": "src/app.py",
        "location": {"file": "/abs/app.py"},
        "callers": {"src/a.py": ["x:1"], "/abs/b.py": ["y:2"]},
        "callees": {"src/c.py": ["z:3"]},
    }
    core_api._prefix_repo(entry, repo, "compact")
    assert entry["file"] == "backend/src/app.py"
    assert entry["location"]["file"] == "/abs/app.py"
    assert entry["callers"] == {"backend/src/a.py": ["x:1"], "/abs/b.py": ["y:2"]}
    assert entry["callees"] == {"backend/src/c.py": ["z:3"]}
    assert "repo" not in entry


def test_prefix_repo_full_detail_sets_the_repo_field_and_ignores_odd_types():
    entry = {"file": None, "location": "x", "callers": [], "callees": None}
    core_api._prefix_repo(entry, _Repo("backend"), "full")
    assert entry["repo"] == "backend"
    assert entry["file"] is None and entry["callers"] == []


def test_vector_engine_search_uses_a_given_query_vector_and_does_not_embed(world, fake_embeddings):
    core_api.restore_session()
    engine = get_state().vector_engine
    fake_embeddings.embed.reset_mock()
    found = engine.search("render_page", limit=3, query_vector=[0.1] * 384)
    assert found
    fake_embeddings.embed.assert_not_called()


def test_vector_engine_search_without_a_query_vector_embeds_the_query_as_a_query(
    world, fake_embeddings
):
    core_api.restore_session()
    engine = get_state().vector_engine
    fake_embeddings.embed.reset_mock()
    assert engine.search("render_page", limit=3)
    fake_embeddings.embed.assert_called_once_with("render_page", is_query=True)


def test_vector_engine_embed_query_returns_the_vector_of_the_service(world, fake_embeddings):
    core_api.restore_session()
    vector = get_state().vector_engine.embed_query("anything")
    assert vector == fake_embeddings.embed.return_value
    fake_embeddings.embed.assert_called_with("anything", is_query=True)


def test_pipeline_read_only_makes_a_read_only_bm25_engine(world, tmp_path):
    primary, backend = world
    assert IndexingPipeline(Settings())._bm25_engine._read_only is False
    from nexus_mcp import links as _links

    settings = _links.settings_for(backend, Settings())
    assert IndexingPipeline(settings, read_only=True)._bm25_engine._read_only is True


def test_search_in_bm25_mode_finds_a_linked_result_and_embeds_nothing(world, fake_embeddings):
    core_api.restore_session()
    fake_embeddings.embed.reset_mock()
    result = core_api.search("handle_request", mode="bm25")
    assert result["results"][0]["filepath"] == "backend/api.py"
    fake_embeddings.embed.assert_not_called()


def test_search_in_vector_mode_reaches_a_linked_repo_with_the_one_query_vector(
    world, fake_embeddings
):
    core_api.restore_session()
    fake_embeddings.embed.reset_mock()
    result = core_api.search("handle_request", mode="vector", limit=20)
    assert any(f == "backend/api.py" for f in _files(result))
    queries = [c for c in fake_embeddings.embed.call_args_list if c.kwargs.get("is_query")]
    assert len(queries) == 1


def test_search_symbol_type_filter_applies_to_a_linked_exact_name(world):
    result = core_api.search("handle_request", symbol_type="class")
    assert all(r.get("symbol_name") != "handle_request" for r in result["results"])


def test_search_with_a_failing_linked_vector_engine_still_gives_the_bm25_result(world):
    core_api.restore_session()

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    get_state().linked[0].vector_engine.search = broken
    result = core_api.search("handle_request")
    assert result["results"][0]["filepath"] == "backend/api.py"


def test_search_with_a_failing_linked_name_lookup_still_answers(world):
    core_api.restore_session()

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    get_state().linked[0].vector_engine.find_by_symbol_names = broken
    result = core_api.search("handle_request")
    assert result["results"]  # an addition, never a failure


def test_search_keeps_the_primary_snippet_when_only_the_linked_file_changed(world):
    primary, backend = world
    (backend / "api.py").write_text(BACKEND_API + "\n# edit\n")
    result = core_api.search("render_page")
    first = result["results"][0]
    assert first["filepath"] == "ui.py"
    assert "code_snippet" in first and "changed_since_index" not in first


def test_search_adds_the_linked_warning_to_the_primary_warning(world):
    primary, backend = world
    (backend / "api.py").write_text(BACKEND_API + "\n# edit\n")
    (primary / "ui.py").write_text(PRIMARY_UI + "\n# edit\n")
    result = core_api.search("handle_request")
    assert "backend" in result["warning"]
    assert result["warning"].count("Linked repo") == 1


def test_find_symbol_keeps_the_code_of_a_primary_symbol_when_the_linked_file_changed(world):
    primary, backend = world
    (backend / "api.py").write_text("# moved\n" * 3 + BACKEND_API)
    [entry] = core_api.find_symbol("render_page")["symbols"]
    assert entry["file"] == "ui.py" and "code" in entry


def test_find_symbol_caps_the_matches_of_one_name_across_the_repos(world, monkeypatch):
    monkeypatch.setattr(core_api, "MAX_MATCHES_PER_NAME", 1)
    result = core_api.find_symbol("shared_name")
    assert len(result["symbols"]) == 1
    assert result["symbols"][0]["file"] == "ui.py"
    assert result["more_matches"] == {"shared_name": 1}


# --- fixes from the audit of phase 2.2 ------------------------------------------------------


def test_merge_by_score_keeps_one_copy_of_a_chunk_that_two_stores_hold():
    primary = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.5}]
    linked = [{"id": "a", "score": 0.95}, {"id": "c", "score": 0.4}]
    merged = core_api._merge_by_score([primary, linked])
    assert [r["id"] for r in merged] == ["a", "b", "c"]


def test_search_gives_one_result_for_a_linked_folder_inside_the_project(world, tmp_path):
    primary, backend = world
    inner = primary / "inner"
    inner.mkdir()
    (inner / "svc.py").write_text("def inner_service():\n    return 1\n")
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(inner)]) == 0
    reset_state()
    core_api._pipeline = None
    core_api.restore_session()
    result = core_api.search("inner_service")
    hits = [r for r in result["results"] if r["symbol_name"] == "inner_service"]
    assert len(hits) == 1


def test_find_symbol_with_a_changed_linked_file_tells_how_to_refresh(world):
    primary, backend = world
    (backend / "api.py").write_text("# moved\n" * 3 + BACKEND_API)
    result = core_api.find_symbol("handle_request")
    assert "nexus-mcp-ci link --refresh" in result["warning"]
    assert "backend" in result["warning"]


def test_find_symbol_with_a_changed_primary_file_does_not_name_a_link(world):
    primary, backend = world
    (primary / "ui.py").write_text("# moved\n" * 3 + PRIMARY_UI)
    result = core_api.find_symbol("render_page")
    assert "warning" in result
    assert "link --refresh" not in result["warning"]


def test_find_symbol_one_failing_linked_graph_does_not_stop_the_call(world, caplog):
    core_api.restore_session()
    repo = get_state().linked[0]

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    repo.pipeline.graph_engine.find_nodes_by_name = broken
    with caplog.at_level(logging.WARNING):
        result = core_api.find_symbol("render_page")
    assert [e["file"] for e in result["symbols"]] == ["ui.py"]
