"""Turn HTTP client calls into edges to the routes that serve them (ADR-027, phase 2.4).

The ast-grep parser records two facts on graph nodes (phase 2.3): `metadata["routes"]`
on a handler and `metadata["http_calls"]` on the function that makes a request. Both
hold a method and a normalised path (`/orders/{}`). This module matches them, between
the graphs of the repos of a session and inside one graph too (a monorepo).

The rule is precision first, as in `call_resolver.py` (ADR-019). A call gets an edge only
when exactly one route fits at the first tier that has any candidate:

- `exact`: the method and the full path are equal (strength 1.0). `ANY` routes fit every
  method. Two fitting routes mean no edge, and the next tier is not tried.
- `suffix`: a route path equals the end of the client path, for a mount prefix that the
  client path holds and the route does not (strength 0.8). The part that is cut off must have
  literal segments only: `/users/{}/orders` is a nested resource, not a prefix of `/orders`.
  A route with no literal segment (`/{}`) never fits this way.

The edges are `HTTP_CALLS` relationships. They are kept in a `ServiceBridge` in memory.
They are never added to a graph: `resolve_calls` rebuilds the CALLS edges of each graph,
and the graph is saved to disk without them. The bridge is rebuilt when the repos are
attached and after a reindex.

Calls and routes in test files are left out: a test often calls a route of its own
project with a test client, and that is no link between two services. A call to a host
outside the project (`external`, set by the extractor) is left out too: the path of
`https://api.stripe.com/v1/charges` says nothing about the routes of the project.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.fusion import is_test_path
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph

logger = logging.getLogger(__name__)

PRIMARY = ""  # the repo key of the project of the session; a linked repo uses its name
TIER_EXACT = "exact"
TIER_SUFFIX = "suffix"
_STRENGTH = {TIER_EXACT: 1.0, TIER_SUFFIX: 0.8}
ANY_METHOD = "ANY"

Key = Tuple[str, str]  # (repo key, node id)


@dataclass
class ServiceEdge:
    """One `HTTP_CALLS` edge: a client node and a route handler, in two repos or in one."""

    client_repo: str
    client_id: str
    route_repo: str
    route_id: str
    rel: UniversalRelationship


@dataclass
class ServiceBridge:
    """The edges between the graphs of a session. In memory only."""

    edges: List[ServiceEdge] = field(default_factory=list)
    _into: Dict[Key, List[ServiceEdge]] = field(default_factory=lambda: defaultdict(list))
    _out: Dict[Key, List[ServiceEdge]] = field(default_factory=lambda: defaultdict(list))

    def add(self, edge: ServiceEdge) -> None:
        self.edges.append(edge)
        self._into[(edge.route_repo, edge.route_id)].append(edge)
        self._out[(edge.client_repo, edge.client_id)].append(edge)

    def callers_of(self, repo: str, node_id: str) -> List[ServiceEdge]:
        """Edges that end at this route handler: the client nodes that call it."""
        return list(self._into.get((repo, node_id), ()))

    def callees_of(self, repo: str, node_id: str) -> List[ServiceEdge]:
        """Edges that start at this client node: the route handlers it calls."""
        return list(self._out.get((repo, node_id), ()))

    def __len__(self) -> int:
        return len(self.edges)

    def __bool__(self) -> bool:
        return bool(self.edges)


@dataclass
class _Route:
    repo: str
    node: UniversalNode
    method: str
    path: str


def _facts(node: UniversalNode, key: str) -> List[Tuple[str, str]]:
    """The (method, path) pairs of a node fact, or [] when the fact is missing or odd.

    A client call marked `external` (a host outside the project) is left out.
    """
    found = []
    for entry in node.metadata.get(key) or ():
        if not isinstance(entry, dict) or entry.get("external"):
            continue
        method, path = entry.get("method"), entry.get("path")
        if isinstance(method, str) and isinstance(path, str) and path.startswith("/"):
            found.append((method.upper(), path))
    return found


def _has_literal(path: str) -> bool:
    return any(part and part != "{}" for part in path.split("/"))


def _relative(file_path: str, roots: Sequence[Path]) -> str:
    """The path below the repo root: a folder above the root must not make a file a test."""
    for root in roots:
        try:
            return str(Path(file_path).relative_to(root))
        except ValueError:
            continue
    return file_path


def _suffixes(path: str) -> List[str]:
    """The proper ends of a path at a segment border, when the cut-off part is all literal.

    `/api/v1/orders` -> `/v1/orders`, `/orders`. `/users/{}/orders` -> none for `/orders`,
    because the part that would be cut (`/users/{}`) holds a parameter.
    """
    parts = [p for p in path.split("/") if p]
    out = []
    for i in range(1, len(parts)):
        if "{}" in parts[i - 1]:
            break
        out.append("/" + "/".join(parts[i:]))
    return out


class _RouteTable:
    def __init__(self) -> None:
        self.by_key: Dict[Tuple[str, str], List[_Route]] = defaultdict(list)

    def add(self, route: _Route) -> None:
        self.by_key[(route.method, route.path)].append(route)

    def fitting(self, method: str, path: str) -> List[_Route]:
        """Routes of this path that serve this method (an `ANY` route serves every method)."""
        return self.by_key.get((method, path), []) + self.by_key.get((ANY_METHOD, path), [])


def _distinct(routes: Iterable[_Route], client: Key) -> List[_Route]:
    """One entry for each route node, and never the client node itself."""
    seen: Set[Key] = set()
    out = []
    for route in routes:
        key = (route.repo, route.node.id)
        if key == client or key in seen:
            continue
        seen.add(key)
        out.append(route)
    return out


def _match(
    table: _RouteTable, client: Key, method: str, path: str
) -> Optional[Tuple[_Route, str]]:
    exact = table.fitting(method, path)
    if exact:
        # A tier with any candidate ends the search, also when the only one is the client
        # itself (a proxy that calls the route it serves): the suffix tier must not run.
        fitting = _distinct(exact, client)
        return (fitting[0], TIER_EXACT) if len(fitting) == 1 else None
    ends = _distinct(
        (r for suffix in _suffixes(path) for r in table.fitting(method, suffix)), client
    )
    return (ends[0], TIER_SUFFIX) if len(ends) == 1 else None


def resolve_services(
    graphs: Mapping[str, RustworkxCodeGraph],
    roots: Optional[Mapping[str, Sequence[Path]]] = None,
) -> ServiceBridge:
    """Match the client calls of every graph with the routes of every graph.

    `graphs` maps a repo key (`PRIMARY` for the project of the session) to its graph.
    `roots` maps the same keys to the repo roots, so that the test-file rule looks at the
    path inside the repo. Nothing is written to a graph.
    """
    roots = roots or {}
    table = _RouteTable()
    clients: List[Tuple[str, UniversalNode]] = []
    for repo, graph in graphs.items():
        with graph._lock:
            for node in list(graph.nodes.values()):
                if "routes" not in node.metadata and "http_calls" not in node.metadata:
                    continue
                if node.node_type not in (NodeType.FUNCTION, NodeType.MODULE):
                    continue
                path = _relative(node.location.file_path, roots.get(repo, ()))
                if is_test_path(path):
                    continue
                for method, route_path in _facts(node, "routes"):
                    if _has_literal(route_path):
                        table.add(_Route(repo, node, method, route_path))
                if node.metadata.get("http_calls"):
                    clients.append((repo, node))
    bridge = ServiceBridge()
    made: Set[str] = set()
    for repo, node in clients:
        for method, path in _facts(node, "http_calls"):
            found = _match(table, (repo, node.id), method, path)
            if found is None:
                continue
            route, tier = found
            edge_id = f"http:{repo}:{node.id}->{route.repo}:{route.node.id}"
            if edge_id in made:
                continue
            made.add(edge_id)
            rel = UniversalRelationship(
                id=edge_id,
                source_id=node.id,
                target_id=route.node.id,
                relationship_type=RelationshipType.HTTP_CALLS,
                metadata={
                    "resolution": tier,
                    "via": f"{method} {path}",
                    "route": f"{route.method} {route.path}",
                },
                strength=_STRENGTH[tier],
            )
            bridge.add(ServiceEdge(repo, node.id, route.repo, route.node.id, rel))
    logger.info("Resolved %d service edges from %d client nodes", len(bridge), len(clients))
    return bridge
