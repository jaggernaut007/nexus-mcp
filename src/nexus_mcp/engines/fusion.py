"""Reciprocal Rank Fusion and graph relevance scoring.

Combines results from vector search, BM25, and graph relevance
into a single ranked list using Reciprocal Rank Fusion (RRF).
"""

import heapq
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from nexus_mcp.core.graph_models import identifier_words
from nexus_mcp.indexing.chunker import _generate_chunk_id

logger = logging.getLogger(__name__)


MIN_TOKEN_LENGTH = 3
MAX_GRAPH_CANDIDATES = 1000
STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "when", "where", "what", "which",
    "who", "how", "does", "are", "was", "were", "not", "any", "all", "into", "over", "than",
    "then", "them", "they", "its", "our", "out", "has", "have", "had", "can", "will", "you",
    "your", "use", "uses", "used", "one", "each", "per", "via", "get", "gets",
})
# Query words that show the user wants test code.
TEST_QUERY_WORDS = frozenset({
    "test", "tests", "testing", "tested", "spec", "specs", "fixture", "fixtures", "mock",
    "mocks", "pytest", "unittest", "conftest", "jest",
})
_TEST_DIRS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs", "e2e"})
_TEST_SUFFIXES = (
    "_test.py", "_test.go", "_test.rs", ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", "test.java", "tests.java",
)


def is_test_path(path: str) -> bool:
    """True when ``path`` is test code by its folder or file name."""
    parts = str(path).replace("\\", "/").lower().split("/")
    name = parts[-1]
    if any(part in _TEST_DIRS for part in parts[:-1]):
        return True
    return name.startswith("test_") or name in ("conftest.py", "tests.py") or name.endswith(
        _TEST_SUFFIXES
    )


def query_mentions_tests(query: str) -> bool:
    """True when the query asks for test code (`test_login`, "the fixture for orders")."""
    words = set()
    for token in query.replace("/", " ").replace(".", " ").split():
        words.update(identifier_words(token))
        words.add(token.lower())
    return bool(words & TEST_QUERY_WORDS)


def demote_test_files(query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Move results in test files after all other results, in the same relative order.

    A test repeats the words of the code it tests, so tests outrank the source they
    cover (measured: 70% of the chunks of a large project were tests, and a prose
    query returned five tests before the function). The order is unchanged when the
    query itself asks for tests. No result is removed.
    """
    if query_mentions_tests(query):
        return results
    source, tests = [], []
    for r in results:
        path = r.get("absolute_path") or r.get("filepath") or ""
        (tests if is_test_path(path) else source).append(r)
    return source + tests


def _name_words(name: str) -> List[str]:
    """Lowercase words of an identifier: `create_order` and `CreateOrder` -> create, order."""
    return list(identifier_words(name))


def _word_forms(word: str) -> set:
    """The word and its plain singular and plural, so `order` finds `orders` and back."""
    forms = {word, word + "s"}
    if word.endswith("es") and len(word) > 4:
        forms.add(word[:-2])
    if word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
    return forms


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
    # Whole words only. Substring matching let "to", "in" or "an" hit every node whose
    # name contained those letters, and centrality then ranked the noise by hub score.
    # Split the query like an identifier, so `order_total` finds `compute_order_total`.
    tokens = [
        w for w in identifier_words(query)
        if len(w) >= MIN_TOKEN_LENGTH and w not in STOP_WORDS
    ]
    if not tokens:
        return []
    # Count how many query words each node matches, and keep the best few. A common word
    # such as "handle" can match thousands of nodes; scoring all of them costs time and
    # adds nothing, because a node that matches several words is the better candidate.
    hits: Counter = Counter()
    for token in tokens:
        hits.update(graph_engine.find_node_ids_by_words(_word_forms(token)))
    best = heapq.nsmallest(MAX_GRAPH_CANDIDATES, hits.items(), key=lambda kv: (-kv[1], kv[0]))
    candidates = [n for n in (graph_engine.get_node(nid) for nid, _ in best) if n is not None]
    # A set has no order; sort so equal scores always come back in the same order.
    candidates.sort(key=lambda n: (n.location.file_path, n.location.start_line, n.name))

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
