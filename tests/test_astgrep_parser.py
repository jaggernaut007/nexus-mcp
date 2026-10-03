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


def _functions_by_name(tmp_path, filename, source):
    path = tmp_path / filename
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    return {n.name: n for n in graph.nodes.values() if n.node_type.value == "function"}


def test_complexity_counts_branch_points_in_python(tmp_path):
    funcs = _functions_by_name(
        tmp_path, "c.py",
        "def straight():\n    return 1\n\n\n"
        "def branchy(a, b):\n"
        "    if a and b or a:\n"
        "        for x in a:\n"
        "            while x:\n"
        "                x -= 1\n"
        "    elif b:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        pass\n"
        "    return [i for i in a if i] if a else None\n",
    )
    assert funcs["straight"].complexity == 1
    # 1 + if, 2 boolean operators, for, while, elif, except, ternary, comprehension for/if
    assert funcs["branchy"].complexity == 11


def test_complexity_counts_branch_points_in_typescript(tmp_path):
    funcs = _functions_by_name(
        tmp_path, "c.ts",
        "function f(a: number) {\n"
        "  if (a > 1) { return 1; }\n"
        "  for (let i = 0; i < a; i++) { }\n"
        "  return a ? 1 : 2;\n}\n",
    )
    assert funcs["f"].complexity == 4


def test_complexity_is_at_least_one_for_every_supported_language(tmp_path):
    cases = {
        "a.go": "package main\n\nfunc F() {}\n",
        "A.java": "class A {\n  void f() { }\n}\n",
        "a.rs": "fn f() {}\n",
    }
    for filename, source in cases.items():
        assert list(_functions_by_name(tmp_path, filename, source).values())[0].complexity == 1


def test_python_docstring_is_extracted_without_quotes(tmp_path):
    funcs = _functions_by_name(
        tmp_path, "d.py",
        'def documented():\n    """Do the thing.\n\n    More detail."""\n    return 1\n\n\n'
        "def single():\n    'one line'\n    return 2\n\n\n"
        "def bare():\n    return 3\n\n\n"
        "def not_a_docstring():\n    x = 1\n    return x\n",
    )
    assert funcs["documented"].docstring == "Do the thing.\n\nMore detail."
    assert funcs["single"].docstring == "one line"
    assert funcs["bare"].docstring is None
    assert funcs["not_a_docstring"].docstring is None


def test_docstring_is_capped(tmp_path):
    long_text = "x" * 2000
    funcs = _functions_by_name(tmp_path, "e.py", f'def f():\n    """{long_text}"""\n')
    assert len(funcs["f"].docstring) == 500
