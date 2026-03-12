"""Tests for core/graph_models.py."""

import pytest

from nexus_mcp.core.graph_models import (
    CacheConfig,
    NodeType,
    RelationshipType,
    UniversalGraph,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)

# --- UniversalLocation ---

def test_location_creation():
    loc = UniversalLocation(file_path="/test.py", start_line=1, end_line=10)
    assert loc.file_path == "/test.py"
    assert loc.start_column == 0


def test_location_empty_path_raises():
    with pytest.raises(ValueError, match="file_path cannot be empty"):
        UniversalLocation(file_path="", start_line=1, end_line=1)


def test_location_invalid_start_line():
    with pytest.raises(ValueError, match="start_line must be >= 1"):
        UniversalLocation(file_path="/t.py", start_line=0, end_line=1)


def test_location_end_before_start():
    with pytest.raises(ValueError):
        UniversalLocation(file_path="/t.py", start_line=5, end_line=3)


def test_location_negative_column():
    with pytest.raises(ValueError):
        UniversalLocation(file_path="/t.py", start_line=1, end_line=1, start_column=-1)


# --- UniversalNode ---

def _make_node(**overrides):
    defaults = {
        "id": "node-1",
        "name": "test_func",
        "node_type": NodeType.FUNCTION,
        "location": UniversalLocation(file_path="/test.py", start_line=1, end_line=5),
        "language": "python",
    }
    defaults.update(overrides)
    return UniversalNode(**defaults)


def test_node_creation():
    node = _make_node()
    assert node.name == "test_func"
    assert node.node_type == NodeType.FUNCTION
    assert node.visibility == "public"


def test_node_with_metadata():
    node = _make_node(complexity=15, is_async=True, return_type="int")
    assert node.complexity == 15
    assert node.is_async
    assert node.return_type == "int"


# --- UniversalRelationship ---

def test_relationship_creation():
    rel = UniversalRelationship(
        id="rel-1",
        source_id="a",
        target_id="b",
        relationship_type=RelationshipType.CALLS,
    )
    assert rel.strength == 1.0
    assert rel.location is None


# --- UniversalGraph ---

def test_graph_add_and_get_node():
    g = UniversalGraph()
    node = _make_node()
    g.add_node(node)
    assert g.get_node("node-1") is node
    assert g.get_node("nonexistent") is None


def test_graph_nodes_by_type():
    g = UniversalGraph()
    g.add_node(_make_node(id="f1", node_type=NodeType.FUNCTION))
    g.add_node(_make_node(id="c1", name="MyClass", node_type=NodeType.CLASS))
    assert len(g.get_nodes_by_type(NodeType.FUNCTION)) == 1
    assert len(g.get_nodes_by_type(NodeType.CLASS)) == 1


def test_graph_nodes_by_language():
    g = UniversalGraph()
    g.add_node(_make_node(id="p1", language="python"))
    g.add_node(_make_node(id="j1", language="javascript"))
    assert len(g.get_nodes_by_language("python")) == 1
    assert len(g.get_nodes_by_language("go")) == 0


def test_graph_add_relationship():
    g = UniversalGraph()
    g.add_node(_make_node(id="a"))
    g.add_node(_make_node(id="b"))
    rel = UniversalRelationship(
        id="r1", source_id="a", target_id="b",
        relationship_type=RelationshipType.CALLS,
    )
    g.add_relationship(rel)
    assert len(g.get_relationships_from("a")) == 1
    assert len(g.get_relationships_to("b")) == 1


def test_graph_relationships_by_type():
    g = UniversalGraph()
    g.add_relationship(UniversalRelationship(
        id="r1", source_id="a", target_id="b",
        relationship_type=RelationshipType.CALLS,
    ))
    g.add_relationship(UniversalRelationship(
        id="r2", source_id="a", target_id="c",
        relationship_type=RelationshipType.IMPORTS,
    ))
    assert len(g.get_relationships_by_type(RelationshipType.CALLS)) == 1
    assert len(g.get_relationships_by_type(RelationshipType.IMPORTS)) == 1


def test_graph_find_nodes_by_name():
    g = UniversalGraph()
    g.add_node(_make_node(id="n1", name="hello"))
    g.add_node(_make_node(id="n2", name="hello_world"))
    assert len(g.find_nodes_by_name("hello", exact_match=True)) == 1
    assert len(g.find_nodes_by_name("hello", exact_match=False)) == 2


def test_graph_connected_nodes():
    g = UniversalGraph()
    g.add_node(_make_node(id="a"))
    g.add_node(_make_node(id="b"))
    g.add_relationship(UniversalRelationship(
        id="r1", source_id="a", target_id="b",
        relationship_type=RelationshipType.CALLS,
    ))
    connected = g.get_connected_nodes("a")
    assert len(connected) == 1
    assert connected[0].id == "b"


def test_graph_statistics():
    g = UniversalGraph()
    g.add_node(_make_node(id="f1", complexity=20))
    g.add_node(_make_node(id="f2", complexity=5))
    stats = g.get_statistics()
    assert stats["total_nodes"] == 2
    assert stats["complexity_stats"]["max_complexity"] == 20
    assert stats["complexity_stats"]["high_complexity_functions"] == 1


def test_graph_export():
    g = UniversalGraph()
    g.add_node(_make_node(id="n1"))
    data = g.export_graph_data()
    assert len(data["nodes"]) == 1
    assert "statistics" in data


def test_cache_config():
    assert CacheConfig.SMALL_CACHE == 1000
    assert CacheConfig.XLARGE_CACHE == 100000
