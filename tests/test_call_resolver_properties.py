"""Invariants of CALLS edge resolution, on generated Python projects (PR 12 audit, 2026-10-28).

The projects are parsed with the real ast-grep parser and resolved the way the pipeline
does it. No edge is built by hand.
"""

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nexus_mcp.core.graph_models import RelationshipType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from tests.test_audit_parser_calls import _functions


def _build(files: dict) -> RustworkxCodeGraph:
    universal = UniversalGraph()
    parser = AstGrepParser()
    with tempfile.TemporaryDirectory() as tmp:
        for name, source in files.items():
            path = Path(tmp) / name
            path.write_text(source)
            parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _calls(graph) -> set:
    return {
        (r.source_id, r.target_id)
        for r in graph.get_relationships_by_type(RelationshipType.CALLS)
    }


CALL_FORMS = st.sampled_from(["self.m{j}()", "super().m{j}()", "C{k}.m{j}()", "m{j}()",
                              "unknown.m{j}()", "f{j}()"])


@st.composite
def projects(draw):
    """1 to 3 files. Each holds classes whose methods call each other in several forms."""
    n_files = draw(st.integers(1, 3))
    files = {}
    for f in range(n_files):
        lines = [f"from mod{(f + 1) % n_files} import *\n"] if n_files > 1 else []
        for c in range(draw(st.integers(1, 3))):
            base = f"(C{draw(st.integers(0, 2))})" if c else ""
            lines.append(f"class C{c}{base}:\n")
            for m in range(draw(st.integers(1, 3))):
                body = [
                    draw(CALL_FORMS).format(j=draw(st.integers(0, 2)), k=draw(st.integers(0, 2)))
                    for _ in range(draw(st.integers(1, 3)))
                ]
                lines.append(f"    def m{m}(self):\n")
                lines += [f"        {b}\n" for b in body]
            lines.append("\n")
        lines += [f"def f{j}():\n    return C0().m0()\n\n" for j in range(draw(st.integers(0, 2)))]
        files[f"mod{f}.py"] = "".join(lines)
    return files


@settings(max_examples=30, deadline=None)
@given(projects())
def test_resolve_calls_edges_stay_inside_the_graph(files):
    graph = _build(files)
    for source, target in _calls(graph):
        assert graph.get_node(source) is not None
        assert graph.get_node(target) is not None
        assert source != target


@settings(max_examples=30, deadline=None)
@given(projects())
def test_resolve_calls_second_run_changes_nothing(files):
    graph = _build(files)
    first = _calls(graph)
    resolve_calls(graph)
    assert _calls(graph) == first


def _callee_names(graph, name):
    node = next(n for n in graph.find_nodes_by_name(name))
    return {c.name for c in graph.get_callees(node.id)}


def test_resolve_calls_super_with_parent_in_an_imported_file():
    graph = _build({
        "base.py": "class Base:\n    def save(self):\n        return 1\n",
        "child.py": (
            "from base import Base\n\n"
            "class Child(Base):\n    def store(self):\n        return super().save()\n"
        ),
    })
    assert _callee_names(graph, "store") == {"save"}


def test_resolve_calls_self_call_to_an_inherited_method_gets_no_edge():
    """Precision first (ADR-019): `self.x()` resolves in the caller's own class only."""
    graph = _build({
        "shapes.py": (
            "class Base:\n    def area(self):\n        return 0\n\n"
            "class Square(Base):\n    def report(self):\n        return self.area()\n"
        ),
    })
    assert _callee_names(graph, "report") == set()


def test_go_unnamed_receiver_type_is_found(tmp_path):
    funcs, _ = _functions(
        tmp_path, "u.go",
        "package main\n\nfunc (*Box) Size() int { return 1 }\n\nfunc (Box) Name() string "
        '{ return "b" }\n',
    )
    assert funcs["Size"].metadata.get("parent_class") == "Box"
    assert funcs["Name"].metadata.get("parent_class") == "Box"
