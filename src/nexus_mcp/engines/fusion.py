"""Reciprocal Rank Fusion and graph relevance scoring.

Combines results from vector search, BM25, and graph relevance
into a single ranked list using Reciprocal Rank Fusion (RRF).
"""

import logging
import re
from typing import Any, Dict, List, Optional

from nexus_mcp.indexing.chunker import _generate_chunk_id

logger = logging.getLogger(__name__)


def graph_relevance_search(
    graph_engine,
    query: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Score graph nodes by structural importance for a query.

    Tokenizes the query, finds matching nodes by name, and scores
    them by graph centrality (weighted in-degree + out-degree).

    Args:
        graph_engine: RustworkxCodeGraph instance.
        query: Search query text.
        limit: Max results to return.

    Returns:
        List of result dicts with id, symbol_name, filepath, score, etc.
    """
    # Tokenize query into words (alphanumeric, 2+ chars)
    tokens = [t.lower() for t in re.split(r'\W+', query) if len(t) >= 2]
    if not tokens:
        return []

    # Find matching nodes across all tokens
    seen_ids: set = set()
    candidates: list = []
    for token in tokens:
        matches = graph_engine.find_nodes_by_name(token, exact=False)
        for node in matches:
            if node.id not in seen_ids:
                seen_ids.add(node.id)
                candidates.append(node)

    if not candidates:
        return []

    # Score by graph centrality: in_degree * 2 + out_degree
    scored = []
    for node in candidates:
        in_deg, out_deg = graph_engine.get_node_degree(node.id)
        raw_score = in_deg * 2 + out_deg
        scored.append((node, raw_score))

    if not scored:
        return []

    # Normalize scores to [0, 1]
    max_score = max(s for _, s in scored) or 1
    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for node, raw_score in scored[:limit]:
        # Map graph node to chunk ID for fusion deduplication
        chunk_id = _generate_chunk_id(
            node.location.file_path, node.name, node.location.start_line
        )
        results.append({
            "id": chunk_id,
            "filepath": node.location.file_path,
            "symbol_name": node.name,
            "symbol_type": node.node_type.value,
            "language": node.language,
            "line_start": node.location.start_line,
            "line_end": node.location.end_line,
            "score": raw_score / max_score,
        })

    return results


class ReciprocalRankFusion:
    """Combine ranked lists from multiple engines using RRF.

    RRF formula: rrf_score(d) = sum(weight_i / (k + rank_i(d)))
    where k is a constant (default 60) and rank is 1-based.
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        k: int = 60,
    ):
        self.weights = weights or {"vector": 0.5, "bm25": 0.3, "graph": 0.2}
        self.k = k

    def fuse(
        self, ranked_lists: Dict[str, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """Fuse multiple ranked lists into one using RRF.

        Args:
            ranked_lists: Dict mapping engine name to its ranked results.
                Each result must have an 'id' field for deduplication.

        Returns:
            Fused and sorted list of results with rrf_score field.
        """
        # Accumulate RRF scores per chunk ID
        scores: Dict[str, float] = {}
        # Track best metadata per chunk (from highest-weight engine)
        metadata: Dict[str, Dict[str, Any]] = {}
        sources: Dict[str, List[str]] = {}

        # Process engines in weight order (highest first) so metadata
        # from highest-weight engine takes priority
        sorted_engines = sorted(
            self.weights.keys(),
            key=lambda e: self.weights.get(e, 0),
            reverse=True,
        )

        for engine_name in sorted_engines:
            if engine_name not in ranked_lists:
                continue

            results = ranked_lists[engine_name]
            weight = self.weights.get(engine_name, 0.0)
            if weight <= 0:
                continue

            for rank, result in enumerate(results, start=1):
                doc_id = result.get("id")
                if not doc_id:
                    continue

                rrf_contribution = weight / (self.k + rank)
                scores[doc_id] = scores.get(doc_id, 0.0) + rrf_contribution

                # First engine to provide metadata wins (highest weight)
                if doc_id not in metadata:
                    metadata[doc_id] = {k: v for k, v in result.items() if k != "score"}

                if doc_id not in sources:
                    sources[doc_id] = []
                sources[doc_id].append(engine_name)

        # Build fused results sorted by RRF score
        fused = []
        for doc_id in sorted(scores.keys(), key=lambda d: scores[d], reverse=True):
            entry = metadata.get(doc_id, {"id": doc_id})
            entry["rrf_score"] = scores[doc_id]
            entry["score"] = scores[doc_id]
            entry["_fusion_sources"] = sources.get(doc_id, [])
            fused.append(entry)

        return fused
