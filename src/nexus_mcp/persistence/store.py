"""SQLite-based graph persistence for warm starts.

Serializes and deserializes the rustworkx code graph to SQLite,
enabling graph recovery without re-parsing with ast-grep.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalLocation,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

logger = logging.getLogger(__name__)


class GraphPersistence:
    """SQLite-backed graph serialization."""

    def __init__(self, db_path: str):
        self._db_path = str(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        conn.commit()

    def _serialize_node(self, node: UniversalNode) -> str:
        return json.dumps({
            "id": node.id,
            "name": node.name,
            "node_type": node.node_type.value,
            "location": {
                "file_path": node.location.file_path,
                "start_line": node.location.start_line,
                "end_line": node.location.end_line,
                "start_column": node.location.start_column,
                "end_column": node.location.end_column,
                "language": node.location.language,
            },
            "content": node.content,
            "docstring": node.docstring,
            "complexity": node.complexity,
            "line_count": node.line_count,
            "language": node.language,
            "metadata": node.metadata,
            "visibility": node.visibility,
            "is_static": node.is_static,
            "is_abstract": node.is_abstract,
            "is_async": node.is_async,
            "return_type": node.return_type,
            "parameter_types": node.parameter_types,
        })

    def _deserialize_node(self, data: str) -> UniversalNode:
        d = json.loads(data)
        loc = UniversalLocation(**d["location"])
        return UniversalNode(
            id=d["id"],
            name=d["name"],
            node_type=NodeType(d["node_type"]),
            location=loc,
            content=d.get("content", ""),
            docstring=d.get("docstring"),
            complexity=d.get("complexity", 0),
            line_count=d.get("line_count", 0),
            language=d.get("language", ""),
            metadata=d.get("metadata", {}),
            visibility=d.get("visibility", "public"),
            is_static=d.get("is_static", False),
            is_abstract=d.get("is_abstract", False),
            is_async=d.get("is_async", False),
            return_type=d.get("return_type"),
            parameter_types=d.get("parameter_types", []),
        )

    def _serialize_rel(self, rel: UniversalRelationship) -> str:
        return json.dumps({
            "id": rel.id,
            "source_id": rel.source_id,
            "target_id": rel.target_id,
            "relationship_type": rel.relationship_type.value,
            "metadata": rel.metadata,
            "strength": rel.strength,
        })

    def _deserialize_rel(self, data: str) -> UniversalRelationship:
        d = json.loads(data)
        return UniversalRelationship(
            id=d["id"],
            source_id=d["source_id"],
            target_id=d["target_id"],
            relationship_type=RelationshipType(d["relationship_type"]),
            metadata=d.get("metadata", {}),
            strength=d.get("strength", 1.0),
        )

    def save(self, graph_engine: RustworkxCodeGraph) -> None:
        """Serialize the graph to SQLite."""
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)

            # Clear existing data
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM relationships")

            # Insert nodes in batches
            node_rows = [
                (node.id, self._serialize_node(node))
                for node in graph_engine.nodes.values()
            ]
            conn.executemany("INSERT INTO nodes (id, data) VALUES (?, ?)", node_rows)

            # Insert relationships in batches
            rel_rows = [
                (rel.id, self._serialize_rel(rel))
                for rel in graph_engine.relationships.values()
            ]
            conn.executemany(
                "INSERT INTO relationships (id, data) VALUES (?, ?)", rel_rows
            )

            conn.commit()
            logger.info(
                "Saved graph: %d nodes, %d relationships",
                len(node_rows), len(rel_rows),
            )
        finally:
            conn.close()

    def load(self) -> Optional[RustworkxCodeGraph]:
        """Deserialize the graph from SQLite. Returns None if DB doesn't exist."""
        if not Path(self._db_path).exists():
            return None

        conn = self._get_connection()
        try:
            self._ensure_tables(conn)

            graph = RustworkxCodeGraph()

            # Load nodes
            cursor = conn.execute("SELECT data FROM nodes")
            node_count = 0
            for (data,) in cursor:
                try:
                    node = self._deserialize_node(data)
                    graph.add_node(node)
                    node_count += 1
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.warning("Failed to deserialize node: %s", e)

            # Load relationships
            cursor = conn.execute("SELECT data FROM relationships")
            rel_count = 0
            for (data,) in cursor:
                try:
                    rel = self._deserialize_rel(data)
                    graph.add_relationship(rel)
                    rel_count += 1
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.warning("Failed to deserialize relationship: %s", e)

            logger.info("Loaded graph: %d nodes, %d relationships", node_count, rel_count)
            return graph
        finally:
            conn.close()

    def exists(self) -> bool:
        """Check if the persistence DB exists."""
        return Path(self._db_path).exists()
