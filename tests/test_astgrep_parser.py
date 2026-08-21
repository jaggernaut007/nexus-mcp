"""Regression tests for AstGrepParser's function/class extraction.

Guards against a real bug found while indexing nexus-mcp's own new
core_api.py: the old extraction patterns ("def $NAME($$$PARAMS): $$$BODY",
"class $NAME: $$$BODY") only match unannotated functions and base-less
classes — a typed codebase or one using inheritance silently loses most of
its graph nodes, with no error raised anywhere.
"""

from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _function_names(graph: UniversalGraph) -> set[str]:
    return {n.name for n in graph.nodes.values() if n.node_type.value == "function"}


def _class_names(graph: UniversalGraph) -> set[str]:
    return {n.name for n in graph.nodes.values() if n.node_type.value == "class"}


def test_extracts_functions_with_type_annotations(tmp_path):
    src = tmp_path / "annotated.py"
    src.write_text(
        "def plain(a, b):\n"
        "    return a + b\n\n"
        "def annotated_args(a: int, b: str):\n"
        "    return a\n\n"
        "def with_return_type(a, b) -> int:\n"
        "    return 1\n\n"
        "def fully_annotated(a: int, b: str) -> dict:\n"
        "    return {}\n"
    )

    parser = AstGrepParser()
    graph = UniversalGraph()
    parser.parse_file(str(src), graph)

    assert _function_names(graph) == {
        "plain",
        "annotated_args",
        "with_return_type",
        "fully_annotated",
    }


def test_extracts_methods_with_return_annotations(tmp_path):
    src = tmp_path / "methods.py"
    src.write_text(
        "class Thing:\n"
        "    def method_plain(self):\n"
        "        pass\n\n"
        "    def method_returns(self) -> str:\n"
        "        return 'x'\n"
    )

    parser = AstGrepParser()
    graph = UniversalGraph()
    parser.parse_file(str(src), graph)

    assert _function_names(graph) == {"method_plain", "method_returns"}
    assert _class_names(graph) == {"Thing"}


def test_extracts_classes_with_and_without_base_classes(tmp_path):
    src = tmp_path / "classes.py"
    src.write_text(
        "class Bare:\n"
        "    pass\n\n"
        "class WithBase(Exception):\n"
        "    pass\n\n"
        "class WithGeneric(dict[str, int]):\n"
        "    pass\n"
    )

    parser = AstGrepParser()
    graph = UniversalGraph()
    parser.parse_file(str(src), graph)

    assert _class_names(graph) == {"Bare", "WithBase", "WithGeneric"}
