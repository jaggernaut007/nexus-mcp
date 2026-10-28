"""Reciprocal Rank Fusion, test-file ordering and exact-name lookup.

Combines results from vector search and BM25
into a single ranked list using Reciprocal Rank Fusion (RRF).
"""

import logging
import re
from typing import Any, Dict, List, Optional

from nexus_mcp.core.graph_models import identifier_words

logger = logging.getLogger(__name__)


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


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INNER_CAPITAL = re.compile(r"[a-z0-9][A-Z]")
MAX_QUERY_IDENTIFIERS = 5


def query_identifiers(query: str) -> List[str]:
    """Words of the query that are written like code names: `snake_case` or `CamelCase`.

    A plain word (`cache`, `Governor`, `API`) is not a name here: it can be prose. The
    part after the last dot of `module.name` is used. At most ``MAX_QUERY_IDENTIFIERS``.
    """
    names: List[str] = []
    for token in _IDENTIFIER.findall(query):
        if len(token) < 3 or token in names:
            continue
        if "_" in token.strip("_") or _INNER_CAPITAL.search(token):
            names.append(token)
    return names[:MAX_QUERY_IDENTIFIERS]


def exact_names_first(
    query: str, results: List[Dict[str, Any]], definitions: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Put the definitions of the names in the query before the other results.

    ``definitions`` are the chunks whose symbol name is a name in the query. A file that
    defines more of the names comes first; then the order of ``results`` decides. On four
    eval suites this raised hit@1 on one (0.59 to 0.64) and lowered none. A plain "all
    definitions first" order lowered one suite, because one name can have a definition in
    several files.
    """
    if not definitions:
        return results
    names_in_file: Dict[str, set] = {}
    for d in definitions:
        names_in_file.setdefault(d.get("filepath", ""), set()).add(
            str(d.get("symbol_name", "")).rsplit(".", 1)[-1]
        )
    order = {r.get("id"): i for i, r in enumerate(results)}
    # A definition that the engines also found keeps its scores. One that only the name
    # lookup found gets the same fields, so that each row has one shape.
    ranked = {r.get("id"): r for r in results}
    definitions = [
        ranked.get(
            d.get("id"),
            {**d, "score": 1.0, "rrf_score": 0.0, "_fusion_sources": ["exact_name"]},
        )
        for d in definitions
    ]
    first = sorted(
        definitions,
        key=lambda d: (
            -len(names_in_file[d.get("filepath", "")]),
            order.get(d.get("id"), len(results)),
        ),
    )
    first = demote_test_files(query, first)
    ids = {d.get("id") for d in first}
    return first + [r for r in results if r.get("id") not in ids]


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
        self.weights = weights or {"vector": 0.5, "bm25": 0.3}
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
