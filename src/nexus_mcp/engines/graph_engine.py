"""High-performance code graph using rustworkx.

Ported from code-graph-mcp. Thread-safe graph with advanced algorithms.
"""

import logging
import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

import rustworkx as rx

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalNode,
    UniversalRelationship,
)

logger = logging.getLogger(__name__)


class RustworkxCodeGraph:
    """Thread-safe code graph using rustworkx PyDiGraph."""

    def __init__(self):
        self._lock = threading.RLock()
        self.graph = rx.PyDiGraph()
        self.nodes: Dict[str, UniversalNode] = {}
        self.relationships: Dict[str, UniversalRelationship] = {}

        # Node ID → rustworkx index
        self._id_to_index: Dict[str, int] = {}
        self._index_to_id: Dict[int, str] = {}

        # Performance indexes (defaultdict avoids per-key existence checks)
        self._nodes_by_type: Dict[NodeType, Set[str]] = defaultdict(set)
        self._nodes_by_language: Dict[str, Set[str]] = defaultdict(set)
        self._file_nodes: Dict[str, Set[str]] = defaultdict(set)

    def add_node(self, node: UniversalNode) -> int:
        """Add node to graph. Returns rustworkx index."""
        with self._lock:
            if node.id in self._id_to_index:
                return self._id_to_index[node.id]

            idx = self.graph.add_node(node.id)
            self._id_to_index[node.id] = idx
            self._index_to_id[idx] = node.id
            self.nodes[node.id] = node

            # Update indexes
            self._nodes_by_type[node.node_type].add(node.id)

            if node.language:
                self._nodes_by_language[node.language].add(node.id)

            if node.location:
                self._file_nodes[node.location.file_path].add(node.id)

            return idx

    def add_relationship(self, rel: UniversalRelationship) -> Optional[int]:
        """Add relationship (edge) to graph."""
        with self._lock:
            src_idx = self._id_to_index.get(rel.source_id)
            tgt_idx = self._id_to_index.get(rel.target_id)

            if src_idx is None or tgt_idx is None:
                logger.debug(
                    "Skipping edge: missing node (%s -> %s)", rel.source_id, rel.target_id
                )
                return None

            edge_idx = self.graph.add_edge(src_idx, tgt_idx, rel.id)
            self.relationships[rel.id] = rel
            return edge_idx

    def get_node(self, node_id: str) -> Optional[UniversalNode]:
        """Return node by ID, or None if not found."""
        with self._lock:
            return self.nodes.get(node_id)

    def get_nodes_by_type(self, node_type: NodeType) -> List[UniversalNode]:
        """Return all nodes of the given type."""
        with self._lock:
            ids = self._nodes_by_type.get(node_type, set())
            return [self.nodes[nid] for nid in ids if nid in self.nodes]

    def get_nodes_by_language(self, language: str) -> List[UniversalNode]:
        """Return all nodes for the given language."""
        with self._lock:
            ids = self._nodes_by_language.get(language, set())
            return [self.nodes[nid] for nid in ids if nid in self.nodes]

    def find_nodes_by_name(self, name: str, exact: bool = True) -> List[UniversalNode]:
        """Find nodes by name. If exact is False, uses case-insensitive substring match."""
        with self._lock:
            if exact:
                return [n for n in self.nodes.values() if n.name == name]
            name_lower = name.lower()
            return [n for n in self.nodes.values() if name_lower in n.name.lower()]

    def _get_callers_unlocked(self, node_id: str) -> List[UniversalNode]:
        """Get callers without acquiring lock. Caller must hold self._lock."""
        idx = self._id_to_index.get(node_id)
        if idx is None:
            return []
        callers = []
        for pred_idx in self.graph.predecessor_indices(idx):
            pred_id = self._index_to_id.get(pred_idx)
            if pred_id and pred_id in self.nodes:
                edge_data = self.graph.get_edge_data(pred_idx, idx)
                if edge_data and edge_data in self.relationships:
                    rel = self.relationships[edge_data]
                    if rel.relationship_type == RelationshipType.CALLS:
                        callers.append(self.nodes[pred_id])
        return callers

    def get_callers(self, node_id: str) -> List[UniversalNode]:
        """Get nodes that call the given node (predecessors via CALLS edges)."""
        with self._lock:
            return self._get_callers_unlocked(node_id)

    def _get_callees_unlocked(self, node_id: str) -> List[UniversalNode]:
        """Get callees without acquiring lock. Caller must hold self._lock."""
        idx = self._id_to_index.get(node_id)
        if idx is None:
            return []
        callees = []
        for succ_idx in self.graph.successor_indices(idx):
            succ_id = self._index_to_id.get(succ_idx)
            if succ_id and succ_id in self.nodes:
                edge_data = self.graph.get_edge_data(idx, succ_idx)
                if edge_data and edge_data in self.relationships:
                    rel = self.relationships[edge_data]
                    if rel.relationship_type == RelationshipType.CALLS:
                        callees.append(self.nodes[succ_id])
        return callees

    def get_callees(self, node_id: str) -> List[UniversalNode]:
        """Get nodes called by the given node (successors via CALLS edges)."""
        with self._lock:
            return self._get_callees_unlocked(node_id)

    def get_predecessors(self, node_id: str) -> List[UniversalNode]:
        """Get all predecessor nodes."""
        with self._lock:
            idx = self._id_to_index.get(node_id)
            if idx is None:
                return []
            result = []
            for pred_idx in self.graph.predecessor_indices(idx):
                pred_id = self._index_to_id.get(pred_idx)
                if pred_id and pred_id in self.nodes:
                    result.append(self.nodes[pred_id])
            return result

    def get_successors(self, node_id: str) -> List[UniversalNode]:
        """Get all successor nodes."""
        with self._lock:
            idx = self._id_to_index.get(node_id)
            if idx is None:
                return []
            result = []
            for succ_idx in self.graph.successor_indices(idx):
                succ_id = self._index_to_id.get(succ_idx)
                if succ_id and succ_id in self.nodes:
                    result.append(self.nodes[succ_id])
            return result

    def get_transitive_callers(self, node_id: str, max_depth: int = 10) -> List[UniversalNode]:
        """Get transitive closure of callers (for impact analysis)."""
        with self._lock:
            visited: Set[str] = set()
            result: List[UniversalNode] = []
            queue = [node_id]
            depth = 0

            while queue and depth < max_depth:
                next_queue = []
                for nid in queue:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    callers = self._get_callers_unlocked(nid)
                    for caller in callers:
                        if caller.id not in visited:
                            result.append(caller)
                            next_queue.append(caller.id)
                queue = next_queue
                depth += 1

            return result

    def get_relationships_from(self, node_id: str) -> List[UniversalRelationship]:
        """Return outgoing relationships from the given node."""
        with self._lock:
            idx = self._id_to_index.get(node_id)
            if idx is None:
                return []
            result = []
            for succ_idx in self.graph.successor_indices(idx):
                edge_data = self.graph.get_edge_data(idx, succ_idx)
                if edge_data and edge_data in self.relationships:
                    result.append(self.relationships[edge_data])
            return result

    def get_relationships_to(self, node_id: str) -> List[UniversalRelationship]:
        """Return incoming relationships to the given node."""
        with self._lock:
            idx = self._id_to_index.get(node_id)
            if idx is None:
                return []
            result = []
            for pred_idx in self.graph.predecessor_indices(idx):
                edge_data = self.graph.get_edge_data(pred_idx, idx)
                if edge_data and edge_data in self.relationships:
                    result.append(self.relationships[edge_data])
            return result

    def get_relationships_by_type(
        self, rel_type: RelationshipType
    ) -> List[UniversalRelationship]:
        """Return all relationships of the given type."""
        with self._lock:
            return [r for r in self.relationships.values() if r.relationship_type == rel_type]

    def remove_file_nodes(self, file_path: str) -> int:
        """Remove all nodes from a specific file (for incremental reindex)."""
        with self._lock:
            node_ids = self._file_nodes.pop(file_path, set())
            # Clean up relationships referencing removed nodes
            stale_rels = [
                rid for rid, rel in self.relationships.items()
                if rel.source_id in node_ids or rel.target_id in node_ids
            ]
            for rid in stale_rels:
                del self.relationships[rid]

            for nid in node_ids:
                idx = self._id_to_index.pop(nid, None)
                if idx is not None:
                    self._index_to_id.pop(idx, None)
                    try:
                        self.graph.remove_node(idx)
                    except Exception as e:
                        logger.debug("Failed to remove node %s from file mapping: %s", nid, e)
                        pass
                self.nodes.pop(nid, None)
                for type_set in self._nodes_by_type.values():
                    type_set.discard(nid)
                for lang_set in self._nodes_by_language.values():
                    lang_set.discard(nid)
            return len(node_ids)

    def get_node_degree(self, node_id: str) -> tuple:
        """Return (in_degree, out_degree) for a node, or (0, 0) if not found."""
        with self._lock:
            idx = self._id_to_index.get(node_id)
            if idx is None:
                return (0, 0)
            return (self.graph.in_degree(idx), self.graph.out_degree(idx))

    def get_statistics(self) -> Dict[str, Any]:
        """Return graph statistics: node/relationship counts, type/language breakdowns."""
        with self._lock:
            stats: Dict[str, Any] = {
                "total_nodes": len(self.nodes),
                "total_relationships": len(self.relationships),
                "total_files": len(self._file_nodes),
                "nodes_by_type": {
                    nt.value: len(ids) for nt, ids in self._nodes_by_type.items()
                },
                "nodes_by_language": {
                    lang: len(ids) for lang, ids in self._nodes_by_language.items()
                },
            }
            return stats

    def clear(self) -> None:
        """Remove all nodes, relationships, and indexes."""
        with self._lock:
            self.graph = rx.PyDiGraph()
            self.nodes.clear()
            self.relationships.clear()
            self._id_to_index.clear()
            self._index_to_id.clear()
            self._nodes_by_type = defaultdict(set)
            self._nodes_by_language = defaultdict(set)
            self._file_nodes = defaultdict(set)
