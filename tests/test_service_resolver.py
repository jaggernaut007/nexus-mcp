"""The service resolver makes HTTP_CALLS edges between a client call and a route (ADR-027, 2.4).

The graphs are real: they come from the ast-grep parser and the pipeline transfer, as in
`test_call_resolver.py`. No node or edge is built by hand.
"""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexus_mcp.core.graph_models import NodeType, RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.indexing.service_resolver import (
    PRIMARY,
    TIER_EXACT,
    TIER_SUFFIX,
    ServiceBridge,
    resolve_services,
)
from nexus_mcp.parsing.astgrep_parser import AstGrepParser

SERVER = (
    "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
    '@app.get("/orders/{order_id}")\ndef get_order(order_id):\n    return {}\n\n\n'
    '@app.post("/payments")\ndef pay(body):\n    return {}\n'
)
CLIENT = (
    "import requests\n\n\n"
    "def show_order(order_id):\n"
    '    return requests.get(f"/orders/{order_id}")\n\n\n'
    "def charge():\n"
    '    return requests.post("/payments")\n'
)


def call(verb: str, path: str) -> str:
    """A client file with one function `c` that sends one request."""
    return f'import requests\n\n\ndef c():\n    return requests.{verb}("{path}")\n'


def route_file(verb: str, path: str) -> str:
    """A server file with one FastAPI route `h`."""
    return (
        "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
        f'@app.{verb}("{path}")\ndef h():\n    return 1\n'
    )


def build(root: Path, files: dict) -> RustworkxCodeGraph:
    """The graph of a small repo: parse, transfer, resolve the CALLS edges."""
    universal = UniversalGraph()
    parser = AstGrepParser()
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def node(graph, name):
    [found] = [n for n in graph.find_nodes_by_name(name) if n.node_type == NodeType.FUNCTION]
    return found


def edges_between(bridge: ServiceBridge, client_repo, client, route_repo, route):
    return [
        e
        for e in bridge.edges
        if (e.client_repo, e.client_id, e.route_repo, e.route_id)
        == (client_repo, client.id, route_repo, route.id)
    ]


@pytest.fixture
def two_repos(tmp_path):
    front = build(tmp_path / "front", {"app.py": CLIENT})
    back = build(tmp_path / "back", {"api.py": SERVER})
    return front, back


def test_the_relationship_type_exists():
    assert RelationshipType.HTTP_CALLS.value == "http_calls"


def test_a_client_call_gets_an_edge_to_the_route_of_another_repo(two_repos):
    front, back = two_repos
    bridge = resolve_services({PRIMARY: front, "back": back})
    [edge] = edges_between(bridge, PRIMARY, node(front, "charge"), "back", node(back, "pay"))
    assert edge.rel.relationship_type == RelationshipType.HTTP_CALLS
    assert edge.rel.metadata["via"] == "POST /payments"
    assert edge.rel.metadata["resolution"] == TIER_EXACT
    assert edge.rel.strength == 1.0


def test_a_parameter_in_the_path_matches_a_parameter(two_repos):
    front, back = two_repos
    bridge = resolve_services({PRIMARY: front, "back": back})
    [edge] = edges_between(
        bridge, PRIMARY, node(front, "show_order"), "back", node(back, "get_order")
    )
    assert edge.rel.metadata["via"] == "GET /orders/{}"


def test_the_method_must_be_equal(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("get", "/payments")})
    back = build(tmp_path / "b", {"s.py": SERVER})
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_an_any_route_matches_every_method(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("delete", "/things")})
    back = build(
        tmp_path / "b",
        {"s.js": 'const app = express();\nfunction h(req, res) {}\napp.all("/things", h);\n'},
    )
    bridge = resolve_services({PRIMARY: front, "b": back})
    assert len(bridge.edges) == 1
    assert bridge.edges[0].rel.metadata["via"] == "DELETE /things"


def test_a_route_path_that_ends_the_client_path_matches_with_a_lower_strength(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "/api/v1/payments")})
    back = build(tmp_path / "b", {"s.py": SERVER})
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert edge.rel.metadata["resolution"] == TIER_SUFFIX
    assert edge.rel.strength == 0.8
    assert edge.rel.metadata["via"] == "POST /api/v1/payments"


def test_a_route_with_no_literal_segment_never_matches_by_its_end(tmp_path):
    client = 'import requests\n\n\ndef c(i):\n    return requests.get(f"/api/{i}")\n'
    server = (
        "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
        '@app.get("/{anything}")\ndef h(anything):\n    return 1\n'
    )
    front = build(tmp_path / "f", {"c.py": client})
    back = build(tmp_path / "b", {"s.py": server})
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_two_routes_that_fit_exactly_give_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "/payments")})
    one = build(tmp_path / "one", {"s.py": SERVER})
    two = build(tmp_path / "two", {"s.py": SERVER})
    assert not resolve_services({PRIMARY: front, "one": one, "two": two}).edges


def test_two_routes_that_fit_by_their_end_give_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "/api/payments")})
    one = build(tmp_path / "one", {"s.py": SERVER})
    two = build(tmp_path / "two", {"s.py": SERVER})
    assert not resolve_services({PRIMARY: front, "one": one, "two": two}).edges


def test_an_exact_match_wins_over_matches_by_the_end(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "/api/payments")})
    back = build(
        tmp_path / "b",
        {
            "s.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
                '@app.post("/api/payments")\ndef exact(b):\n    return 1\n\n\n'
                '@app.post("/payments")\ndef shorter(b):\n    return 1\n'
            )
        },
    )
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert edge.rel.metadata["resolution"] == TIER_EXACT
    assert edge.route_id == node(back, "exact").id


def test_a_client_and_a_route_in_one_repo_get_an_edge(tmp_path):
    graph = build(
        tmp_path / "mono",
        {"frontend/app.py": CLIENT, "backend/api.py": SERVER},
    )
    bridge = resolve_services({PRIMARY: graph})
    found = edges_between(bridge, PRIMARY, node(graph, "charge"), PRIMARY, node(graph, "pay"))
    assert len(found) == 1


def test_a_function_never_gets_an_edge_to_itself(tmp_path):
    graph = build(
        tmp_path / "self",
        {
            "s.py": (
                "import requests\nfrom fastapi import FastAPI\napp = FastAPI()\n\n\n"
                '@app.post("/loop")\ndef loop(b):\n    return requests.post("/loop")\n'
            )
        },
    )
    assert not resolve_services({PRIMARY: graph}).edges


def test_a_call_in_a_test_file_gets_no_edge(tmp_path):
    front = build(tmp_path / "f", {"tests/test_pay.py": CLIENT})
    back = build(tmp_path / "b", {"s.py": SERVER})
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_a_route_in_a_test_file_gets_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": CLIENT})
    back = build(tmp_path / "b", {"tests/test_server.py": SERVER})
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_a_call_at_module_level_gets_an_edge_from_the_module(tmp_path):
    front = build(tmp_path / "f", {"boot.py": 'import requests\n\nrequests.post("/payments")\n'})
    back = build(tmp_path / "b", {"s.py": SERVER})
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert front.get_node(edge.client_id).node_type == NodeType.MODULE
    assert edge.route_id == node(back, "pay").id


def test_the_bridge_lists_callers_and_callees_of_a_node(two_repos):
    front, back = two_repos
    bridge = resolve_services({PRIMARY: front, "back": back})
    pay, charge = node(back, "pay"), node(front, "charge")
    [into] = bridge.callers_of("back", pay.id)
    assert (into.client_repo, into.client_id) == (PRIMARY, charge.id)
    [out] = bridge.callees_of(PRIMARY, charge.id)
    assert (out.route_repo, out.route_id) == ("back", pay.id)
    assert bridge.callers_of(PRIMARY, charge.id) == []
    assert bridge.callees_of("back", pay.id) == []


def test_resolving_never_changes_a_graph(two_repos):
    front, back = two_repos
    before = (len(front.relationships), len(back.relationships), len(front.nodes))
    resolve_services({PRIMARY: front, "back": back})
    after = (len(front.relationships), len(back.relationships), len(front.nodes))
    assert before == after
    assert not [
        r
        for g in (front, back)
        for r in g.relationships.values()
        if r.relationship_type == RelationshipType.HTTP_CALLS
    ]


def test_resolving_twice_gives_the_same_edges(two_repos):
    front, back = two_repos
    first = resolve_services({PRIMARY: front, "back": back})
    second = resolve_services({PRIMARY: front, "back": back})
    assert [e.rel.id for e in first.edges] == [e.rel.id for e in second.edges]


def test_graphs_with_no_http_facts_give_an_empty_bridge(tmp_path):
    graph = build(tmp_path / "plain", {"a.py": "def f():\n    return 1\n"})
    bridge = resolve_services({PRIMARY: graph})
    assert not bridge.edges
    assert len(bridge) == 0


@given(
    st.lists(st.sampled_from(["/a", "/b/{}", "/c/d", "/e"]), min_size=1, max_size=4, unique=True)
)
def test_a_client_call_never_gets_more_than_one_edge(paths):
    """Property: whatever the routes are, one client call makes at most one edge."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        routes = "".join(
            f'@app.get("{p}")\ndef h{i}():\n    return 1\n\n\n' for i, p in enumerate(paths)
        )
        back = build(
            root / "b",
            {"s.py": "from fastapi import FastAPI\napp = FastAPI()\n\n\n" + routes},
        )
        calls = "".join(
            f'def c{i}():\n    return requests.get("{p}")\n\n\n' for i, p in enumerate(paths)
        )
        front = build(root / "f", {"c.py": "import requests\n\n\n" + calls})
        bridge = resolve_services({PRIMARY: front, "b": back})
        per_client = {}
        for e in bridge.edges:
            per_client[e.client_id] = per_client.get(e.client_id, 0) + 1
        assert len(per_client) == len(paths)  # every call fits its own route
        assert set(per_client.values()) == {1}


# --- more paths: ANY, two routes on a node, two facts, roots, the test-file rule ----------------

ANY_SERVER = (
    "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
    '@app.get("/things")\n@app.post("/other")\ndef h():\n    return 1\n'
)


def test_resolve_services_an_any_route_does_not_serve_a_path_it_does_not_name(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("delete", "/elsewhere")})
    back = build(
        tmp_path / "b",
        {"s.js": 'const app = express();\nfunction h(req, res) {}\napp.all("/things", h);\n'},
    )
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_resolve_services_an_any_route_and_a_method_route_of_one_node_give_one_edge(tmp_path):
    """`ANY /things` and `GET /things` on two nodes are two routes: no edge. On one node: one."""
    front = build(tmp_path / "f", {"c.py": call("get", "/things")})
    back = build(
        tmp_path / "b",
        {
            "s.js": (
                "const app = express();\nfunction h(req, res) {}\n"
                'app.all("/things", h);\napp.get("/things", h);\n'
            )
        },
    )
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert edge.route_id == node(back, "h").id


def test_resolve_services_an_any_route_and_a_get_route_on_two_nodes_give_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("get", "/things")})
    back = build(
        tmp_path / "b",
        {
            "s.js": (
                "const app = express();\nfunction h(req, res) {}\nfunction g(req, res) {}\n"
                'app.all("/things", h);\napp.get("/things", g);\n'
            )
        },
    )
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_resolve_services_a_route_node_with_two_routes_serves_each_of_them(tmp_path):
    front = build(
        tmp_path / "f",
        {
            "c.py": (
                "import requests\n\n\n"
                'def a():\n    return requests.get("/things")\n\n\n'
                'def b():\n    return requests.post("/other")\n\n\n'
                'def c():\n    return requests.get("/other")\n'
            )
        },
    )
    back = build(tmp_path / "b", {"s.py": ANY_SERVER})
    bridge = resolve_services({PRIMARY: front, "b": back})
    h = node(back, "h")
    assert len(edges_between(bridge, PRIMARY, node(front, "a"), "b", h)) == 1
    assert len(edges_between(bridge, PRIMARY, node(front, "b"), "b", h)) == 1
    # `GET /other` is a route of the node with another method: it must not fit.
    assert not bridge.callees_of(PRIMARY, node(front, "c").id)
    assert len(bridge.callers_of("b", h.id)) == 2


def test_resolve_services_two_facts_of_one_client_node_that_hit_one_route_give_one_edge(tmp_path):
    front = build(
        tmp_path / "f",
        {
            "c.py": (
                "import requests\n\n\ndef c(i):\n"
                '    requests.get(f"/orders/{i}")\n'
                '    return requests.get(f"/api/orders/{i}")\n'
            )
        },
    )
    assert len(node(front, "c").metadata["http_calls"]) == 2
    back = build(
        tmp_path / "b",
        {
            "s.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
                '@app.get("/orders/{order_id}")\ndef h(order_id):\n    return 1\n'
            )
        },
    )
    bridge = resolve_services({PRIMARY: front, "b": back})
    assert len(bridge.edges) == 1
    assert len(bridge.callers_of("b", node(back, "h").id)) == 1
    assert len(bridge.callees_of(PRIMARY, node(front, "c").id)) == 1


def test_resolve_services_one_client_node_with_facts_for_two_routes_gets_two_edges(tmp_path):
    front = build(
        tmp_path / "f",
        {
            "c.py": (
                "import requests\n\n\ndef c():\n"
                '    requests.get("/orders/x/y")\n    return requests.post("/payments")\n'
            )
        },
    )
    back = build(tmp_path / "b", {"s.py": SERVER})
    # `/orders/x/y` has no route; only `/payments` fits.
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert edge.rel.metadata["via"] == "POST /payments"


def test_resolve_services_a_path_that_only_shares_part_of_a_segment_gets_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "/api/mypayments")})
    back = build(tmp_path / "b", {"s.py": SERVER})
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_resolve_services_a_client_with_no_route_anywhere_gives_no_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": CLIENT})
    assert not resolve_services({PRIMARY: front}).edges


def test_resolve_services_the_test_rule_reads_the_path_below_the_repo_root(tmp_path):
    """A repo in a folder called `tests` is no test code: the rule must not see that folder."""
    base = tmp_path / "tests" / "repos"
    front = build(base / "front", {"app.py": CLIENT})
    back = build(base / "back", {"api.py": SERVER})
    roots = {PRIMARY: [base / "front"], "back": [base / "back"]}
    assert len(resolve_services({PRIMARY: front, "back": back}, roots).edges) == 2
    # With no roots the absolute path is all that is known, and the folder makes it a test path.
    assert not resolve_services({PRIMARY: front, "back": back}).edges


def test_resolve_services_a_test_folder_below_the_root_is_still_left_out_with_roots(tmp_path):
    base = tmp_path / "tests" / "repos"
    front = build(base / "front", {"tests/test_pay.py": CLIENT})
    back = build(base / "back", {"api.py": SERVER})
    roots = {PRIMARY: [base / "front"], "back": [base / "back"]}
    assert not resolve_services({PRIMARY: front, "back": back}, roots).edges


def test_resolve_services_every_root_of_a_multi_root_primary_is_used(tmp_path):
    base = tmp_path / "tests" / "multi"
    one, two = base / "one", base / "two"
    universal = UniversalGraph()
    parser = AstGrepParser()
    for root, name, source in ((one, "c.py", CLIENT), (two, "s.py", SERVER)):
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(source)
        parser.parse_file(str(root / name), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    both = resolve_services({PRIMARY: graph}, {PRIMARY: [one, two]})
    assert len(both.edges) == 2
    # With the second root missing, the route file keeps its `tests` folder and is left out.
    only_one = resolve_services({PRIMARY: graph}, {PRIMARY: [one]})
    assert not only_one.edges


# --- audit of phase 2.4: wrong edges ------------------------------------------------------


def test_a_prefix_with_a_parameter_is_no_mount_prefix(tmp_path):
    """`/users/{}/orders` is a nested resource: the route `/orders` is another thing."""
    front = build(tmp_path / "f", {"c.py": call("get", "/users/{u}/orders")})
    back = build(
        tmp_path / "b",
        {"s.py": route_file("get", "/orders")},
    )
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_a_literal_prefix_still_matches_by_the_end(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("get", "/api/v1/orders")})
    back = build(
        tmp_path / "b",
        {"s.py": route_file("get", "/orders")},
    )
    [edge] = resolve_services({PRIMARY: front, "b": back}).edges
    assert edge.rel.metadata["resolution"] == TIER_SUFFIX


@pytest.mark.parametrize(
    "url",
    ["https://api.stripe.com/v1/charges", "https://partner.example.org/charges"],
)
def test_a_call_to_an_outside_host_gets_no_edge(tmp_path, url):
    front = build(tmp_path / "f", {"c.py": call("post", url)})
    back = build(
        tmp_path / "b",
        {"s.py": route_file("post", "/charges")},
    )
    assert not resolve_services({PRIMARY: front, "b": back}).edges


def test_a_call_to_localhost_still_gets_an_edge(tmp_path):
    front = build(tmp_path / "f", {"c.py": call("post", "http://localhost:8000/payments")})
    back = build(tmp_path / "b", {"s.py": SERVER})
    assert len(resolve_services({PRIMARY: front, "b": back}).edges) == 1


def test_a_self_match_at_the_exact_tier_stops_the_search(tmp_path):
    """The only exact fit is the client itself: the suffix tier must not run (ADR-027)."""
    graph = build(
        tmp_path / "m",
        {
            "proxy.py": (
                "import requests\nfrom fastapi import FastAPI\napp = FastAPI()\n\n\n"
                '@app.get("/api/orders")\ndef proxy():\n    return requests.get("/api/orders")\n'
            ),
            "other.py": (
                "from fastapi import FastAPI\napp = FastAPI()\n\n\n"
                '@app.get("/orders")\ndef real():\n    return 1\n'
            ),
        },
    )
    assert not resolve_services({PRIMARY: graph}).edges
