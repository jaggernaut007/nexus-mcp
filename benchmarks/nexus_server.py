"""Start nexus-mcp for a benchmark run, with a candidate embedding model if one is named.

The shipped registry holds `bge-small-en` (and the deprecated `jina-code`). The candidates in
evals/retrieval/candidates.py (for example `granite-97m-r2-int8`) exist only inside the
evals. This entry point adds the one named in NEXUS_EMBEDDING_MODEL to the in-memory
registry of this process, then runs the normal server. Nothing on disk changes.

    NEXUS_EMBEDDING_MODEL=granite-97m-r2-int8 python -m benchmarks.nexus_server

Needs the repository root and `src` on PYTHONPATH (conditions.model_mcp_config sets both).
"""

import os
from typing import Optional


def register_candidate(name: Optional[str] = None) -> Optional[str]:
    """Add `name` (default: $NEXUS_EMBEDDING_MODEL) to the model registry if it is a candidate.

    Returns the name that was registered, or None when the model is already shipped, empty
    or unknown. An unknown name is left for the server to reject with its usual error.
    """
    name = name if name is not None else os.environ.get("NEXUS_EMBEDDING_MODEL", "")
    if not name:
        return None
    from evals.retrieval import candidates as cand
    from nexus_mcp.indexing import embedding_service as es

    if cand.CANDIDATES.get(name) is None:
        return None  # shipped (or unknown): nothing to add
    cand.register(name, es.EMBEDDING_MODELS)
    return name


def main() -> None:
    """Register the candidate model, then run the nexus-mcp stdio server."""
    register_candidate()
    from nexus_mcp.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
