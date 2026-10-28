"""Pure retrieval metrics over ranked file lists.

A query has a set of relevant files. A model returns files in rank order. These
functions score that ranking. Nothing here reads a file or calls a model.
"""

from typing import Any, Dict, List, Optional, Sequence, Set


def dedupe(ranked: Sequence[str]) -> List[str]:
    """Keep the first occurrence of each file, in rank order."""
    seen: Set[str] = set()
    out: List[str] = []
    for path in ranked:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def first_hit_rank(ranked: Sequence[str], relevant: Set[str]) -> Optional[int]:
    """1-based rank of the first relevant file, or None if none is ranked."""
    for i, path in enumerate(dedupe(ranked), start=1):
        if path in relevant:
            return i
    return None


def hit_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    """1.0 when any relevant file is in the top k, else 0.0."""
    rank = first_hit_rank(ranked, relevant)
    return 1.0 if rank is not None and rank <= k else 0.0


def recall_at_k(ranked: Sequence[str], relevant: Set[str], k: int) -> float:
    """Fraction of the relevant files that are in the top k distinct files."""
    if not relevant:
        return 0.0
    top = set(dedupe(ranked)[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: Set[str], k: int = 10) -> float:
    """1/rank of the first relevant file inside the top k, else 0.0."""
    rank = first_hit_rank(ranked, relevant)
    return 1.0 / rank if rank is not None and rank <= k else 0.0


def score_query(ranked: Sequence[str], relevant: Set[str]) -> Dict[str, float]:
    """All per-query metrics in one dict."""
    return {
        "hit@1": hit_at_k(ranked, relevant, 1),
        "hit@5": hit_at_k(ranked, relevant, 5),
        "recall@5": recall_at_k(ranked, relevant, 5),
        "recall@10": recall_at_k(ranked, relevant, 10),
        "mrr@10": reciprocal_rank(ranked, relevant, 10),
    }


def mean_metrics(per_query: List[Dict[str, float]]) -> Dict[str, Any]:
    """Mean of each metric over queries. Empty input gives an empty dict."""
    if not per_query:
        return {}
    keys = per_query[0].keys()
    out: Dict[str, Any] = {k: sum(q[k] for q in per_query) / len(per_query) for k in keys}
    out["queries"] = len(per_query)
    return out
