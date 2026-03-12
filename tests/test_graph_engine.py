"""Tests for engines/graph_engine.py."""

import pytest

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph


def _loc(fp="/test.py"):
    return UniversalLocation(file_path=fp, start_line=1, end_line=10)


def _node(id, name="func", node_type=NodeType.FUNCTION, language="python", **kw):
    return UniversalNode(
        id=id, name=name, node_type=node_type, location=_loc(), language=language, **kw
    )


def _rel(id, src, tgt, rtype=RelationshipType.CALLS):
    return UniversalRelationship(id=id, source_id=src, target_id=tgt, relationship_type=rtype)


@pytest.fixture
def graph():
    return RustworkxCodeGraph()


def test_add_node(graph):
    idx = graph.add_node(_node("n1"))
    assert idx >= 0
    assert graph.get_node("n1") is not None


def test_add_duplicate_node(graph):
    n = _node("n1")
    idx1 = graph.add_node(n)
    idx2 = graph.add_node(n)
    assert idx1 == idx2


def test_add_relationship(graph):
    graph.add_node(_node("a"))
    graph.add_node(_node("b"))
    edge = graph.add_relationship(_rel("r1", "a", "b"))
    assert edge is not None


def test_add_relationship_missing_node(graph):
    graph.add_node(_node("a"))
    result = graph.add_relationship(_rel("r1", "a", "nonexistent"))
    assert result is None


def test_nodes_by_type(graph):
    graph.add_node(_node("f1", node_type=NodeType.FUNCTION))
    graph.add_node(_node("c1", name="MyClass", node_type=NodeType.CLASS))
    assert len(graph.get_nodes_by_type(NodeType.FUNCTION)) == 1
    assert len(graph.get_nodes_by_type(NodeType.CLASS)) == 1


def test_nodes_by_language(graph):
    graph.add_node(_node("p1", language="python"))
    graph.add_node(_node("j1", language="javascript"))
    assert len(graph.get_nodes_by_language("python")) == 1
    assert len(graph.get_nodes_by_language("go")) == 0


def test_find_by_name(graph):
    graph.add_node(_node("n1", name="hello"))
    graph.add_node(_node("n2", name="hello_world"))
    assert len(graph.find_nodes_by_name("hello", exact=True)) == 1
    assert len(graph.find_nodes_by_name("hello", exact=False)) == 2


def test_predecessors_successors(graph):
    graph.add_node(_node("a"))
    graph.add_node(_node("b"))
    graph.add_relationship(_rel("r1", "a", "b"))
    assert len(graph.get_successors("a")) == 1
    assert len(graph.get_predecessors("b")) == 1
    assert len(graph.get_predecessors("a")) == 0


def test_callers_callees(graph):
    graph.add_node(_node("a", name="caller"))
    graph.add_node(_node("b", name="callee"))
    graph.add_relationship(_rel("r1", "a", "b", RelationshipType.CALLS))
    callers = graph.get_callers("b")
    assert len(callers) == 1
    assert callers[0].name == "caller"
    callees = graph.get_callees("a")
    assert len(callees) == 1
    assert callees[0].name == "callee"


def test_transitive_callers(graph):
    graph.add_node(_node("a"))
    graph.add_node(_node("b"))
    graph.add_node(_node("c"))
    graph.add_relationship(_rel("r1", "a", "b", RelationshipType.CALLS))
    graph.add_relationship(_rel("r2", "b", "c", RelationshipType.CALLS))
    transitive = graph.get_transitive_callers("c")
    ids = {n.id for n in transitive}
    assert "b" in ids
    assert "a" in ids


def test_relationships_from_to(graph):
    graph.add_node(_node("a"))
    graph.add_node(_node("b"))
    graph.add_relationship(_rel("r1", "a", "b"))
    assert len(graph.get_relationships_from("a")) == 1
    assert len(graph.get_relationships_to("b")) == 1
    assert len(graph.get_relationships_from("b")) == 0


def test_relationships_by_type(graph):
    graph.add_node(_node("a"))
    graph.add_node(_node("b"))
    graph.add_relationship(_rel("r1", "a", "b", RelationshipType.CALLS))
    graph.add_relationship(_rel("r2", "a", "b", RelationshipType.IMPORTS))
    assert len(graph.get_relationships_by_type(RelationshipType.CALLS)) == 1


def test_statistics(graph):
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    stats = graph.get_statistics()
    assert stats["total_nodes"] == 2
    assert stats["total_relationships"] == 0


def test_clear(graph):
    graph.add_node(_node("n1"))
    graph.clear()
    assert graph.get_node("n1") is None
    assert graph.get_statistics()["total_nodes"] == 0


def test_remove_file_nodes(graph):
    n1 = _node("n1")
    n1.location.file_path  # already "/test.py"
    graph.add_node(n1)
    removed = graph.remove_file_nodes("/test.py")
    assert removed == 1
    assert graph.get_node("n1") is None
