"""Checks of evals/retrieval/queries.languages.yaml against benchmarks/corpora.yaml."""

from pathlib import Path

import pytest
import yaml

from benchmarks import corpora

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITES = yaml.safe_load(
    (REPO_ROOT / "evals" / "retrieval" / "queries.languages.yaml").read_text()
)["suites"]


def test_language_suites_cover_each_retrieval_corpus():
    wanted = {c["suite"] for c in corpora.select(corpora.load(), tier="retrieval")}
    # Python's suite (`flask`) lives in queries.yaml with the older public suites.
    assert wanted - {"flask"} <= set(SUITES)


@pytest.mark.parametrize("name", sorted(SUITES))
def test_language_suite_has_unique_ids_and_answers(name):
    queries = SUITES[name]["queries"]
    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)) >= 12
    assert all(q["relevant"] for q in queries)


@pytest.mark.parametrize("name", sorted(SUITES))
def test_language_suite_relevant_files_exist_in_the_checkout(name):
    root = REPO_ROOT / SUITES[name]["root"]
    if not root.is_dir():
        pytest.skip(f"{root} is not cloned (python -m benchmarks.corpora setup)")
    missing = [
        f"{q['id']}: {path}"
        for q in SUITES[name]["queries"]
        for path in q["relevant"]
        if not (root / path).is_file()
    ]
    assert missing == []
