"""BM25 scores of linked stores are scaled together, not each by its own best (ADR-027, 2.5).

A store divides its BM25 scores by its best score, so the best keyword result of every store
has the score 1, however weak it is. Merged as they were, a weak match in a linked repo
outranked the second result of the project. The 2.5 eval (`evals/linked/with_link.py`)
showed it: hit@1 of `shop_repo` fell from 0.75 to 0.625 with a linked bookshop. The merge
now divides every list by the best raw score of all lists.
"""

import pytest

import nexus_mcp.core_api as core_api
from nexus_mcp import links
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import reset_state


def _hit(name, raw, score=1.0):
    return {"id": name, "score": score, "_raw_score": raw}


def test_merge_bm25_scales_every_list_by_the_best_raw_score():
    primary = [_hit("p1", 10.0, 1.0), _hit("p2", 5.0, 0.5)]
    linked = [_hit("l1", 3.0, 1.0), _hit("l2", 2.0, 0.667)]
    merged = core_api._merge_bm25([primary, linked])
    assert [r["id"] for r in merged] == ["p1", "p2", "l1", "l2"]
    assert [round(r["score"], 2) for r in merged] == [1.0, 0.5, 0.3, 0.2]


def test_merge_bm25_a_strong_linked_match_still_comes_first():
    primary = [_hit("p1", 2.0, 1.0)]
    linked = [_hit("l1", 8.0, 1.0)]
    assert [r["id"] for r in core_api._merge_bm25([primary, linked])] == ["l1", "p1"]


def test_merge_bm25_one_list_is_returned_as_it_is():
    only = [{"id": "a", "score": 1.0}]
    assert core_api._merge_bm25([only]) is only


def test_merge_bm25_a_hit_with_no_raw_score_keeps_its_score():
    merged = core_api._merge_bm25([[{"id": "a", "score": 0.7}], [_hit("b", 4.0)]])
    assert {r["id"]: r["score"] for r in merged} == {"a": 0.7, "b": 1.0}


@pytest.mark.usefixtures("fake_embeddings")
def test_the_bm25_engine_keeps_the_raw_score_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_STORAGE_DIR", raising=False)
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.py").write_text("def settle_invoice():\n    return 'settle the invoice'\n")
    reset_settings()
    settings = Settings()
    settings.storage_dir = str(root / ".nexus")
    settings.storage_base = ""
    pipeline = IndexingPipeline(settings)
    pipeline.index(root)
    plain = pipeline.bm25_engine.search("settle invoice")
    kept = pipeline.bm25_engine.search("settle invoice", keep_raw=True)
    assert plain and "_raw_score" not in plain[0]
    assert kept[0]["_raw_score"] > 0
    assert kept[0]["score"] == 1.0


STRONG = "def charge_webhook():\n    return 'charge webhook charge webhook signature'\n"
MODERATE = "def webhook_note():\n    return 'a webhook note'\n"
WEAK = "def linked_helper():\n    return 'one webhook mention'\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    primary, backend = tmp_path / "primary", tmp_path / "backend"
    primary.mkdir()
    backend.mkdir()
    (primary / "strong.py").write_text(STRONG)
    (primary / "moderate.py").write_text(MODERATE)
    (backend / "weak.py").write_text(WEAK)
    monkeypatch.chdir(primary)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(primary / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    assert cli.dispatch(["link", str(backend)]) == 0
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    reset_settings()
    yield primary, backend
    reset_settings()


@pytest.mark.usefixtures("fake_embeddings")
def test_a_weak_linked_match_does_not_outrank_a_stronger_result_of_the_project(world):
    result = core_api.search("charge webhook signature", mode="bm25", limit=5)
    files = [r["filepath"] for r in result["results"]]
    assert files[0] == "strong.py"
    assert files.index("moderate.py") < files.index("backend/weak.py")
    assert not any("_raw_score" in r for r in result["results"])


@pytest.mark.usefixtures("fake_embeddings")
def test_a_strong_linked_match_is_found_first_when_the_project_has_none(world):
    primary, _ = world
    links_before = links.links_for(primary)
    assert links_before
    result = core_api.search("one webhook mention", mode="bm25", limit=5)
    assert result["results"][0]["filepath"] == "backend/weak.py"
