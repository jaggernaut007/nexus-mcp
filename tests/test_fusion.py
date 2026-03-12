"""Tests for Reciprocal Rank Fusion and graph relevance scoring."""

import pytest

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.fusion import ReciprocalRankFusion, graph_relevance_search
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph


def _make_node(name, filepath="/a.py", line=1, node_type=NodeType.FUNCTION, language="python"):
    loc = UniversalLocation(file_path=filepath, start_line=line, end_line=line + 5)
    return UniversalNode(
        id=f"{node_type.value}:{name}:{filepath}:{line}",
        name=name,
        node_type=node_type,
        location=loc,
        language=language,
    )


def _make_rel(source_id, target_id, rel_type=RelationshipType.CALLS):
    return UniversalRelationship(
        id=f"rel:{source_id}->{target_id}",
        source_id=source_id,
        target_id=target_id,
        relationship_type=rel_type,
    )


@pytest.fixture
def graph_with_calls():
    """Graph: main -> parse -> validate, analyze -> parse, parse -> log"""
    g = RustworkxCodeGraph()
    nodes = {
        "main": _make_node("main", line=1),
        "parse": _make_node("parse", line=10),
        "validate": _make_node("validate", line=20),
        "analyze": _make_node("analyze", line=30),
        "log": _make_node("log", line=40),
    }
    for n in nodes.values():
        g.add_node(n)

    # main -> parse, analyze -> parse, parse -> validate, parse -> log
    g.add_relationship(_make_rel(nodes["main"].id, nodes["parse"].id))
    g.add_relationship(_make_rel(nodes["analyze"].id, nodes["parse"].id))
    g.add_relationship(_make_rel(nodes["parse"].id, nodes["validate"].id))
    g.add_relationship(_make_rel(nodes["parse"].id, nodes["log"].id))

    return g, nodes


class TestGraphRelevance:
    def test_basic_search(self, graph_with_calls):
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "parse")
        assert len(results) >= 1
        assert results[0]["symbol_name"] == "parse"
        assert results[0]["score"] > 0

    def test_parse_has_highest_centrality(self, graph_with_calls):
        """parse has 2 in-edges + 2 out-edges = highest centrality."""
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "parse main validate analyze log", limit=5)
        # parse: in_degree=2, out_degree=2 → score = 2*2+2 = 6
        parse_result = next(r for r in results if r["symbol_name"] == "parse")
        assert parse_result["score"] == 1.0  # Normalized max

    def test_empty_query(self, graph_with_calls):
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "")
        assert results == []

    def test_no_matches(self, graph_with_calls):
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "nonexistent_symbol")
        assert results == []

    def test_limit_respected(self, graph_with_calls):
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "parse main validate", limit=2)
        assert len(results) <= 2

    def test_result_has_chunk_id(self, graph_with_calls):
        g, _ = graph_with_calls
        results = graph_relevance_search(g, "parse")
        assert results[0]["id"]  # Non-empty chunk ID
        assert len(results[0]["id"]) == 16  # SHA256[:16]


class TestRRFAlgorithm:
    def test_basic_fusion(self):
        rrf = ReciprocalRankFusion(k=60)
        results = rrf.fuse({
            "vector": [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.5}],
            "bm25": [{"id": "b", "score": 0.8}, {"id": "c", "score": 0.3}],
        })
        ids = [r["id"] for r in results]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids

    def test_rrf_score_calculation(self):
        """Verify RRF formula: weight / (k + rank)."""
        rrf = ReciprocalRankFusion(weights={"engine": 1.0}, k=60)
        results = rrf.fuse({
            "engine": [{"id": "a"}, {"id": "b"}],
        })
        # rank 1: 1.0 / (60 + 1) = 0.01639...
        # rank 2: 1.0 / (60 + 2) = 0.01613...
        assert abs(results[0]["rrf_score"] - 1.0 / 61) < 1e-6
        assert abs(results[1]["rrf_score"] - 1.0 / 62) < 1e-6

    def test_deduplication(self):
        """Same ID from multiple engines accumulates score."""
        rrf = ReciprocalRankFusion(
            weights={"vector": 0.5, "bm25": 0.5}, k=60
        )
        results = rrf.fuse({
            "vector": [{"id": "shared", "score": 0.9, "text": "vector_text"}],
            "bm25": [{"id": "shared", "score": 0.8, "text": "bm25_text"}],
        })
        assert len(results) == 1
        assert results[0]["id"] == "shared"
        # Score from both engines
        expected = 0.5 / 61 + 0.5 / 61
        assert abs(results[0]["rrf_score"] - expected) < 1e-6

    def test_metadata_from_highest_weight(self):
        """Metadata comes from the highest-weight engine."""
        rrf = ReciprocalRankFusion(
            weights={"vector": 0.7, "bm25": 0.3}, k=60
        )
        results = rrf.fuse({
            "vector": [{"id": "x", "text": "from_vector"}],
            "bm25": [{"id": "x", "text": "from_bm25"}],
        })
        assert results[0]["text"] == "from_vector"

    def test_fusion_sources_tracked(self):
        rrf = ReciprocalRankFusion(k=60)
        results = rrf.fuse({
            "vector": [{"id": "a"}],
            "bm25": [{"id": "a"}, {"id": "b"}],
        })
        a_result = next(r for r in results if r["id"] == "a")
        assert "vector" in a_result["_fusion_sources"]
        assert "bm25" in a_result["_fusion_sources"]

    def test_missing_engine_skipped(self):
        rrf = ReciprocalRankFusion(
            weights={"vector": 0.5, "bm25": 0.3, "graph": 0.2}, k=60
        )
        # Only vector results provided
        results = rrf.fuse({"vector": [{"id": "a"}]})
        assert len(results) == 1

    def test_empty_lists(self):
        rrf = ReciprocalRankFusion(k=60)
        results = rrf.fuse({"vector": [], "bm25": []})
        assert results == []

    def test_zero_weight_skipped(self):
        rrf = ReciprocalRankFusion(
            weights={"vector": 1.0, "bm25": 0.0}, k=60
        )
        results = rrf.fuse({
            "vector": [{"id": "a"}],
            "bm25": [{"id": "b"}],
        })
        ids = [r["id"] for r in results]
        assert "a" in ids
        assert "b" not in ids

    def test_custom_weights(self):
        rrf = ReciprocalRankFusion(
            weights={"a": 0.8, "b": 0.2}, k=60
        )
        results = rrf.fuse({
            "a": [{"id": "x"}],
            "b": [{"id": "y"}],
        })
        # x should have higher score due to higher weight
        assert results[0]["id"] == "x"
        assert results[0]["rrf_score"] > results[1]["rrf_score"]
