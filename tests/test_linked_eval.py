"""The edge eval of linked repos and its fixture (ADR-027, phase 2.5).

The fixture `tests/fixtures/linked` is a small bookshop: a FastAPI backend and a TypeScript
frontend. The labels in `evals/linked/labels.yaml` were written by hand. These tests keep the
eval honest: it must score a wrong edge and a missed edge, and the fixture must hold the gate.
"""

import shutil
from pathlib import Path

import pytest

import nexus_mcp.core_api as core_api
from evals.linked import run as edge_eval
from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.evaluate import cli
from nexus_mcp.indexing.pipeline import IndexingPipeline
from nexus_mcp.state import reset_state

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "linked"
CLIENT = "frontend:src/api/client.ts"


@pytest.fixture(scope="module")
def result():
    return edge_eval.run(["linked_fixture"])["linked_fixture"]


# --- score ----------------------------------------------------------------------------------


def test_score_a_perfect_prediction_gives_one_and_one():
    edges = {("a:x#f", "b:y#g")}
    out = edge_eval.score(edges, edges)
    assert (out["precision"], out["recall"]) == (1.0, 1.0)


def test_score_a_wrong_edge_lowers_the_precision():
    labelled = {("a:x#f", "b:y#g")}
    out = edge_eval.score(labelled | {("a:x#h", "b:y#g")}, labelled)
    assert out["precision"] == 0.5
    assert out["recall"] == 1.0
    assert out["false_positives"] == [("a:x#h", "b:y#g")]


def test_score_a_missed_edge_lowers_the_recall_and_not_the_precision():
    labelled = {("a:x#f", "b:y#g"), ("a:x#h", "b:y#k")}
    out = edge_eval.score({("a:x#f", "b:y#g")}, labelled)
    assert out["precision"] == 1.0
    assert out["recall"] == 0.5
    assert out["missed"] == [("a:x#h", "b:y#k")]


def test_score_no_prediction_and_no_label_is_not_a_failure():
    out = edge_eval.score(set(), set())
    assert (out["precision"], out["recall"]) == (1.0, 1.0)


def test_gate_failures_name_a_suite_below_the_precision_gate():
    failures = edge_eval.gate_failures({"s": {"precision": 0.5}, "t": {"skipped": "x"}})
    assert failures == ["s: precision 0.5 < 0.9"]
    assert edge_eval.gate_failures({"s": {"precision": 0.9}}) == []


def test_a_suite_whose_repos_are_missing_is_skipped():
    out = edge_eval.run_suite({"repos": {"a": "no/such/folder"}, "edges": []})
    assert "skipped" in out


# --- the fixture ----------------------------------------------------------------------------


def test_the_fixture_holds_the_precision_gate(result):
    assert result["precision"] >= edge_eval.MIN_PRECISION
    assert result["false_positives"] == []


def test_the_fixture_misses_only_the_labelled_known_miss(result):
    assert result["unexpected_misses"] == []
    assert result["known_misses"] == [
        (f"{CLIENT}#listOrdersFromTable", "backend:app/routers/orders.py#list_orders")
    ]
    assert result["recall"] == 0.9


def test_the_fixture_decoys_get_no_edge(result):
    clients = {c for c, _ in (key.split(" -> ") for key in result["via"])}
    assert f"{CLIENT}#trackShipment" not in clients  # a host outside the project
    assert f"{CLIENT}#loadInvoice" not in clients  # no route
    assert not any("client.test" in c or "orders_api" in c for c in clients)  # test files


def test_the_fixture_uses_both_tiers(result):
    tiers = {v.rsplit("(", 1)[1].rstrip(")") for v in result["via"].values()}
    assert tiers == {"exact", "suffix"}


def test_main_exits_zero_on_the_fixture(capsys):
    assert edge_eval.main(["--suites", "linked_fixture"]) == 0
    assert "precision 1.0" in capsys.readouterr().out


# --- the fixture through the tools ----------------------------------------------------------


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """The frontend as the primary project, the backend linked; a new process on the stores."""
    monkeypatch.setenv("NEXUS_LINKS_FILE", str(tmp_path / "home" / "links.json"))
    front, back = tmp_path / "frontend", tmp_path / "backend"
    shutil.copytree(FIXTURE / "frontend", front)
    shutil.copytree(FIXTURE / "backend", back)
    monkeypatch.chdir(front)
    monkeypatch.setenv("NEXUS_STORAGE_DIR", str(front / ".nexus"))
    reset_settings()
    IndexingPipeline(Settings()).index(front)
    assert cli.dispatch(["link", str(back)]) == 0
    reset_state()
    core_api._pipeline = None
    monkeypatch.setenv("NEXUS_AUTO_RESTORE", "true")
    monkeypatch.setenv("NEXUS_STALENESS_CHECK_INTERVAL", "0")
    reset_settings()
    yield front, back
    reset_settings()


@pytest.mark.usefixtures("fake_embeddings")
def test_the_impact_of_a_route_reaches_the_screen_that_uses_it(stack):
    """From the backend handler to the frontend page, over one HTTP edge and one call edge."""
    result = core_api.graph("get_order", "callers", transitive=True)
    files = result["impacted_files"]
    assert sorted(files["src/api/client.ts"]) == ["fetchOrder via GET /api/orders/{}"]
    assert files["src/components/OrderPage.ts"] == ["openOrder"]
    assert result["defined_in"] == {"backend/app/routers/orders.py": [f"get_order:{_line(stack)}"]}


def _line(stack):
    src = (stack[1] / "app" / "routers" / "orders.py").read_text().splitlines()
    return next(i for i, line in enumerate(src, 1) if line.startswith("def get_order"))


@pytest.mark.usefixtures("fake_embeddings")
def test_a_decoy_call_has_no_callee_in_the_other_repo(stack):
    for name in ("trackShipment", "loadInvoice"):
        result = core_api.graph(name, "callees")
        assert result["total"] == 0
        assert "via" not in str(result)


# --- audit of phase 2.5 -------------------------------------------------------------------


def test_gate_failures_name_a_suite_that_predicts_nothing_but_has_labels():
    result = {"precision": 1.0, "predicted": 0, "labelled": 3, "unexpected_misses": []}
    [failure] = edge_eval.gate_failures({"s": result})
    assert "no edge" in failure


def test_gate_failures_name_a_missed_edge_that_is_not_a_known_miss():
    result = {
        "precision": 1.0,
        "predicted": 2,
        "labelled": 3,
        "unexpected_misses": [("a:x#f", "b:y#g")],
    }
    [failure] = edge_eval.gate_failures({"s": result})
    assert "a:x#f" in failure


def test_gate_failures_pass_for_a_suite_with_no_label_and_no_prediction():
    result = {"precision": 1.0, "predicted": 0, "labelled": 0, "unexpected_misses": []}
    assert edge_eval.gate_failures({"s": result}) == []


def test_label_tells_two_methods_of_one_name_in_one_file_apart(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "m.py").write_text(
        "class A:\n    def get(self):\n        return 1\n\n\n"
        "class B:\n    def get(self):\n        return 2\n"
    )
    graph = edge_eval.build_graph(root)
    labels = {
        edge_eval._label("r", n, root)
        for n in graph.nodes.values()
        if n.name == "get"
    }
    assert labels == {"r:m.py#A.get", "r:m.py#B.get"}


def test_the_with_link_eval_restores_the_environment_and_the_folder():
    import os

    from evals.linked import with_link

    def state():
        return (
            os.getcwd(),
            os.environ.get("NEXUS_LINKS_FILE"),
            os.environ.get("NEXUS_STORAGE_DIR"),
        )

    before = state()
    with with_link._isolated_process("/tmp"):
        assert os.environ["NEXUS_STORAGE_DIR"] != (before[2] or "")
    assert state() == before
