"""Does a link make `search` worse for the project itself? (ADR-027, phase 2.5)

`search` merges the results of each linked store by score. BM25 scores are scaled inside
each store, so the best keyword result of a linked repo has the same score as the best of
the project, and a weak linked match can outrank the second result of the project. This eval
measures it. It indexes the `shop_repo` fixture, runs the queries of its retrieval suite
through `core_api.search` (hybrid, real embedding model) with no link and then with the
bookshop backend of `tests/fixtures/linked` linked, and compares hit@1. The bookshop shares
words with the shop (orders, payments), so the other repo is a hard distractor.

    python -m evals.linked.with_link          # CPU time only; needs the default model

A result counts as a hit when the first result is a relevant file of the project. A first
result in the linked repo is a miss. The gate: hit@1 with the link is not lower than without.

The other side is reported too, not gated: four queries about the bookshop backend, with the
link, must find a file of the linked repo first (`linked_side`). A merge that always put the
project first would pass the gate and fail here.
"""

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Union

from evals.retrieval.run import load_suites

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRIMARY_SUITE = "shop_repo"
_BACKEND = "backend/app"
LINKED_QUERIES = [
    {"id": "cancel", "query": "cancel an order by its id", "file": "routers/orders.py"},
    {"id": "card", "query": "charge a customer's payment card", "file": "routers/payments.py"},
    {"id": "me", "query": "who is the signed-in user", "file": "routers/users.py"},
    {"id": "health", "query": "health check endpoint", "file": "main.py"},
]
for _q in LINKED_QUERIES:
    _q["relevant"] = [f"{_BACKEND}/{_q.pop('file')}"]
LINKED_REPO = REPO_ROOT / "tests" / "fixtures" / "linked" / "backend"


def _top_file(results: list) -> Optional[str]:
    return results[0]["filepath"] if results else None


def _hit1(queries: list, search) -> Dict[str, Any]:
    hits, firsts = 0, {}
    for q in queries:
        top = _top_file(search(q["query"]))
        firsts[q["id"]] = top
        hits += top in set(q["relevant"])
    return {"hit@1": round(hits / len(queries), 3), "first_results": firsts}


_ENV_KEYS = ("NEXUS_LINKS_FILE", "NEXUS_STORAGE_DIR", "NEXUS_AUTO_RESTORE")


@contextlib.contextmanager
def _isolated_process(work_dir: Union[Path, str]) -> Iterator[None]:
    """Point the links file and the storage folder into `work_dir`; restore all on exit.

    The eval must not touch the registry or the stores of the user, and a caller that imports
    `run()` must get its folder and its environment back.
    """
    work = Path(work_dir)
    saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    saved_cwd = os.getcwd()
    os.environ["NEXUS_LINKS_FILE"] = str(work / "home" / "links.json")
    os.environ["NEXUS_STORAGE_DIR"] = str(work / "shop_repo" / ".nexus")
    os.environ["NEXUS_AUTO_RESTORE"] = "true"
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(work_dir: Path) -> Dict[str, Any]:
    with _isolated_process(work_dir):
        return _run(work_dir)


def _run(work_dir: Path) -> Dict[str, Any]:
    import nexus_mcp.core_api as core_api
    from nexus_mcp import links
    from nexus_mcp.config import Settings, reset_settings
    from nexus_mcp.evaluate import cli
    from nexus_mcp.indexing.pipeline import IndexingPipeline
    from nexus_mcp.state import get_state, reset_state

    suite = load_suites()[PRIMARY_SUITE]
    queries = suite["queries"]
    primary = work_dir / "shop_repo"
    backend = work_dir / "backend"
    shutil.copytree(REPO_ROOT / suite["root"], primary)
    shutil.copytree(LINKED_REPO, backend)

    os.chdir(primary)
    reset_settings()
    IndexingPipeline(Settings()).index(primary)
    if cli.dispatch(["link", str(backend)]) != 0:
        raise RuntimeError("link failed")

    def new_process() -> None:
        reset_state()
        core_api._pipeline = None
        reset_settings()

    def search(query: str) -> list:
        return core_api.search(query, limit=10).get("results", [])

    out: Dict[str, Any] = {"queries": len(queries)}
    new_process()
    core_api.restore_session()
    assert len(get_state().linked) == 1 and get_state().linked[0].attached
    out["with_link"] = _hit1(queries, search)
    out["linked_side"] = _hit1(LINKED_QUERIES, search)
    links.remove_link(primary, "backend")
    new_process()
    core_api.restore_session()
    assert get_state().linked == []
    out["without_link"] = _hit1(queries, search)
    out["changed"] = {
        qid: {"without": out["without_link"]["first_results"][qid], "with": first}
        for qid, first in out["with_link"]["first_results"].items()
        if first != out["without_link"]["first_results"][qid]
    }
    out["gate_met"] = out["with_link"]["hit@1"] >= out["without_link"]["hit@1"]
    return out


def main(argv: Optional[list] = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        result = run(Path(tmp))
    print(json.dumps(result, indent=2))
    return 0 if result["gate_met"] else 1


if __name__ == "__main__":
    sys.exit(main())
