"""Tests for SQLite graph persistence."""

import pytest

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.persistence.store import GraphPersistence


def _make_node(name, filepath="/a.py", line=1, node_type=NodeType.FUNCTION, language="python"):
    loc = UniversalLocation(file_path=filepath, start_line=line, end_line=line + 5)
    return UniversalNode(
        id=f"{node_type.value}:{name}:{filepath}:{line}",
        name=name,
        node_type=node_type,
        location=loc,
        language=language,
        complexity=3,
        line_count=5,
    )


def _make_rel(source_id, target_id, rel_type=RelationshipType.CALLS):
    return UniversalRelationship(
        id=f"rel:{source_id}->{target_id}",
        source_id=source_id,
        target_id=target_id,
        relationship_type=rel_type,
    )


@pytest.fixture
def populated_graph():
    g = RustworkxCodeGraph()
    n1 = _make_node("main", line=1)
    n2 = _make_node("helper", line=10)
    n3 = _make_node("MyClass", node_type=NodeType.CLASS, line=20)
    g.add_node(n1)
    g.add_node(n2)
    g.add_node(n3)
    g.add_relationship(_make_rel(n1.id, n2.id))
    g.add_relationship(_make_rel(n3.id, n2.id, RelationshipType.CONTAINS))
    return g


class TestSaveLoad:
    def test_roundtrip(self, tmp_path, populated_graph):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)

        persistence.save(populated_graph)
        loaded = persistence.load()

        assert loaded is not None
        assert len(loaded.nodes) == 3
        assert len(loaded.relationships) == 2

    def test_node_data_preserved(self, tmp_path, populated_graph):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)

        persistence.save(populated_graph)
        loaded = persistence.load()

        original = populated_graph.nodes
        for nid, node in original.items():
            loaded_node = loaded.nodes[nid]
            assert loaded_node.name == node.name
            assert loaded_node.node_type == node.node_type
            assert loaded_node.language == node.language
            assert loaded_node.complexity == node.complexity
            assert loaded_node.location.file_path == node.location.file_path
            assert loaded_node.location.start_line == node.location.start_line

    def test_relationship_data_preserved(self, tmp_path, populated_graph):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)

        persistence.save(populated_graph)
        loaded = persistence.load()

        for rid, rel in populated_graph.relationships.items():
            loaded_rel = loaded.relationships[rid]
            assert loaded_rel.source_id == rel.source_id
            assert loaded_rel.target_id == rel.target_id
            assert loaded_rel.relationship_type == rel.relationship_type

    def test_graph_traversal_works_after_load(self, tmp_path, populated_graph):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)

        persistence.save(populated_graph)
        loaded = persistence.load()

        # Find main node and verify it has callees
        main_nodes = loaded.find_nodes_by_name("main")
        assert len(main_nodes) == 1
        callees = loaded.get_callees(main_nodes[0].id)
        assert len(callees) == 1
        assert callees[0].name == "helper"


class TestEmptyGraph:
    def test_save_load_empty(self, tmp_path):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)

        g = RustworkxCodeGraph()
        persistence.save(g)
        loaded = persistence.load()

        assert loaded is not None
        assert len(loaded.nodes) == 0
        assert len(loaded.relationships) == 0


class TestExists:
    def test_exists_false(self, tmp_path):
        persistence = GraphPersistence(str(tmp_path / "nonexistent.db"))
        assert persistence.exists() is False

    def test_exists_true(self, tmp_path, populated_graph):
        db_path = str(tmp_path / "graph.db")
        persistence = GraphPersistence(db_path)
        persistence.save(populated_graph)
        assert persistence.exists() is True


class TestCorruption:
    def test_load_nonexistent_returns_none(self, tmp_path):
        persistence = GraphPersistence(str(tmp_path / "missing.db"))
        assert persistence.load() is None
