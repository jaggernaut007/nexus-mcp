"""Universal graph data structures for code analysis.

Ported from code-graph-mcp. Language-agnostic representations for
code nodes, relationships, and graph structure.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class CacheConfig:
    """Centralized cache size configuration."""

    SMALL_CACHE = 1000
    MEDIUM_CACHE = 10000
    LARGE_CACHE = 50000
    XLARGE_CACHE = 100000


class NodeType(Enum):
    """Universal node types across all languages."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    PARAMETER = "parameter"
    CONDITIONAL = "conditional"
    LOOP = "loop"
    EXCEPTION = "exception"
    INTERFACE = "interface"
    ENUM = "enum"
    NAMESPACE = "namespace"
    IMPORT = "import"
    LITERAL = "literal"
    CALL = "call"
    REFERENCE = "reference"


class RelationshipType(Enum):
    """Universal relationship types between code elements."""

    CONTAINS = "contains"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CALLS = "calls"
    IMPORTS = "imports"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    OVERRIDES = "overrides"
    EXTENDS = "extends"
    USES = "uses"


@dataclass(frozen=True, slots=True)
class UniversalLocation:
    """Location information for code elements. Immutable after creation."""

    file_path: str
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0
    language: str = ""

    def __post_init__(self):
        if not self.file_path:
            raise ValueError("file_path cannot be empty")
        if self.start_line < 1:
            raise ValueError(f"start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) cannot be less than start_line ({self.start_line})"
            )
        if self.start_column < 0:
            raise ValueError(f"start_column must be >= 0, got {self.start_column}")
        if self.end_column < 0:
            raise ValueError(f"end_column must be >= 0, got {self.end_column}")


@dataclass(slots=True)
class UniversalNode:
    """Universal representation of a code element.

    Not frozen because metadata and parameter_types are populated
    incrementally during AST parsing. Consider immutable once added to graph.
    """

    id: str
    name: str
    node_type: NodeType
    location: UniversalLocation

    content: str = ""
    docstring: Optional[str] = None
    complexity: int = 0
    line_count: int = 0
    language: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    visibility: str = "public"
    is_static: bool = False
    is_abstract: bool = False
    is_async: bool = False

    return_type: Optional[str] = None
    parameter_types: List[str] = field(default_factory=list)


@dataclass(slots=True)
class UniversalRelationship:
    """Relationship between code elements.

    Not frozen because metadata is populated during parsing.
    Consider immutable once added to graph.
    """

    id: str
    source_id: str
    target_id: str
    relationship_type: RelationshipType

    metadata: Dict[str, Any] = field(default_factory=dict)
    strength: float = 1.0
    location: Optional[UniversalLocation] = None


class UniversalGraph:
    """Universal code graph with indexed lookups."""

    def __init__(self):
        self.nodes: Dict[str, UniversalNode] = {}
        self.relationships: Dict[str, UniversalRelationship] = {}
        self._nodes_by_type: Dict[NodeType, Set[str]] = defaultdict(set)
        self._nodes_by_language: Dict[str, Set[str]] = defaultdict(set)
        self._relationships_from: Dict[str, Set[str]] = defaultdict(set)
        self._relationships_to: Dict[str, Set[str]] = defaultdict(set)
        self.metadata: Dict[str, Any] = {}

    def add_node(self, node: UniversalNode) -> None:
        """Add a node to the graph, updating type and language indexes."""
        self.nodes[node.id] = node
        self._nodes_by_type[node.node_type].add(node.id)
        if node.language:
            self._nodes_by_language[node.language].add(node.id)

    def add_relationship(self, relationship: UniversalRelationship) -> None:
        """Add a relationship, updating source/target indexes."""
        self.relationships[relationship.id] = relationship
        self._relationships_from[relationship.source_id].add(relationship.id)
        self._relationships_to[relationship.target_id].add(relationship.id)

    def get_node(self, node_id: str) -> Optional[UniversalNode]:
        """Return node by ID, or None if not found."""
        return self.nodes.get(node_id)

    def get_nodes_by_type(self, node_type: NodeType) -> List[UniversalNode]:
        """Return all nodes of the given type."""
        node_ids = self._nodes_by_type.get(node_type, set())
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_nodes_by_language(self, language: str) -> List[UniversalNode]:
        """Return all nodes for the given language."""
        node_ids = self._nodes_by_language.get(language, set())
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_relationships_from(self, node_id: str) -> List[UniversalRelationship]:
        """Return outgoing relationships from the given node."""
        rel_ids = self._relationships_from.get(node_id, set())
        return [self.relationships[rid] for rid in rel_ids if rid in self.relationships]

    def get_relationships_to(self, node_id: str) -> List[UniversalRelationship]:
        """Return incoming relationships to the given node."""
        rel_ids = self._relationships_to.get(node_id, set())
        return [self.relationships[rid] for rid in rel_ids if rid in self.relationships]

    def get_relationships_by_type(
        self, relationship_type: RelationshipType
    ) -> List[UniversalRelationship]:
        """Return all relationships of the given type."""
        return [
            rel
            for rel in self.relationships.values()
            if rel.relationship_type == relationship_type
        ]

    def find_nodes_by_name(
        self, name: str, exact_match: bool = True
    ) -> List[UniversalNode]:
        """Find nodes by name. If exact_match is False, uses case-insensitive substring match."""
        if exact_match:
            return [node for node in self.nodes.values() if node.name == name]
        name_lower = name.lower()
        return [node for node in self.nodes.values() if name_lower in node.name.lower()]

    def get_connected_nodes(
        self,
        node_id: str,
        relationship_types: Optional[List[RelationshipType]] = None,
    ) -> List[UniversalNode]:
        """Return all connected nodes, optionally filtered by relationship type."""
        connected_ids: Set[str] = set()
        for rel in self.get_relationships_from(node_id):
            if not relationship_types or rel.relationship_type in relationship_types:
                connected_ids.add(rel.target_id)
        for rel in self.get_relationships_to(node_id):
            if not relationship_types or rel.relationship_type in relationship_types:
                connected_ids.add(rel.source_id)
        return [self.nodes[nid] for nid in connected_ids if nid in self.nodes]

    def get_statistics(self) -> Dict[str, Any]:
        """Return graph statistics: node/relationship counts, type breakdowns, complexity."""
        stats: Dict[str, Any] = {
            "total_nodes": len(self.nodes),
            "total_relationships": len(self.relationships),
            "nodes_by_type": {},
            "nodes_by_language": {},
            "relationships_by_type": {},
            "complexity_stats": {
                "total_complexity": 0,
                "average_complexity": 0.0,
                "max_complexity": 0,
                "high_complexity_functions": 0,
            },
        }
        for node_type, node_ids in self._nodes_by_type.items():
            stats["nodes_by_type"][node_type.value] = len(node_ids)
        for language, node_ids in self._nodes_by_language.items():
            stats["nodes_by_language"][language] = len(node_ids)
        for rel in self.relationships.values():
            rel_type = rel.relationship_type.value
            stats["relationships_by_type"][rel_type] = (
                stats["relationships_by_type"].get(rel_type, 0) + 1
            )
        complexities = [n.complexity for n in self.nodes.values() if n.complexity > 0]
        if complexities:
            stats["complexity_stats"]["total_complexity"] = sum(complexities)
            stats["complexity_stats"]["average_complexity"] = sum(complexities) / len(complexities)
            stats["complexity_stats"]["max_complexity"] = max(complexities)
            stats["complexity_stats"]["high_complexity_functions"] = sum(
                1 for c in complexities if c > 10
            )
        return stats

    def export_graph_data(self) -> Dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "type": node.node_type.value,
                    "language": node.language,
                    "location": {
                        "file": node.location.file_path,
                        "start_line": node.location.start_line,
                        "end_line": node.location.end_line,
                    },
                    "complexity": node.complexity,
                    "line_count": node.line_count,
                }
                for node in self.nodes.values()
            ],
            "relationships": [
                {
                    "id": rel.id,
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "type": rel.relationship_type.value,
                    "strength": rel.strength,
                }
                for rel in self.relationships.values()
            ],
            "statistics": self.get_statistics(),
            "metadata": self.metadata,
        }
