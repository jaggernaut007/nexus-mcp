"""Audit tests for ast-grep call, import and class extraction in the languages with few tests."""


from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.parsing import astgrep_parser
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _functions(tmp_path, filename, source):
    path = tmp_path / filename
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    funcs = {n.name: n for n in graph.nodes.values() if n.node_type.value == "function"}
    modules = [n for n in graph.nodes.values() if n.node_type.value == "module"]
    return funcs, modules


def test_go_calls_and_plain_receiver_type(tmp_path):
    funcs, _ = _functions(
        tmp_path, "a.go",
        "package main\n\nfunc (b *Box) Get() int { return helper() + b.Other() }\n"
        "\nfunc helper() int { return 1 }\n",
    )
    assert funcs["Get"].metadata["calls"] == ["helper", "b.Other"]
    assert funcs["Get"].metadata["parent_class"] == "Box"
    assert "parent_class" not in funcs["helper"].metadata


def test_java_calls_include_constructors_and_qualified_calls(tmp_path):
    funcs, _ = _functions(
        tmp_path, "A.java",
        "class A { void f() { g(); new B(); this.h(); Util.x(); } void g() {} }\n",
    )
    assert set(funcs["f"].metadata["calls"]) == {"g", "B", "this.h", "Util.x"}
    assert funcs["f"].metadata["parent_class"] == "A"


def test_rust_calls_normalise_paths_and_use_the_impl_type(tmp_path):
    funcs, _ = _functions(
        tmp_path, "a.rs",
        "struct S;\nimpl<T> Trait for S<T> {\n"
        "    fn f(&self) { self.g(); helper(); Vec::new(); }\n    fn g(&self) {}\n}\n",
    )
    assert funcs["f"].metadata["calls"] == ["self.g", "helper", "Vec.new"]
    assert funcs["f"].metadata["parent_class"] == "S"


def test_typescript_calls_optional_chain_new_and_this(tmp_path):
    funcs, modules = _functions(
        tmp_path, "a.ts",
        'import {x} from "./y";\nclass C { m() { this.n(); new D(); x?.y(); } n() {} }\n',
    )
    assert funcs["m"].metadata["calls"] == ["this.n", "x.y", "D"]
    assert funcs["m"].metadata["parent_class"] == "C"
    assert modules[0].metadata["imports"] == ["./y"]


def test_python_relative_import_is_collected(tmp_path):
    source = "from .sib import f\nimport os.path\n\n\ndef g():\n    f()\n"
    _, modules = _functions(tmp_path, "a.py", source)
    assert set(modules[0].metadata["imports"]) >= {".sib", "os.path"}


def test_call_extraction_is_capped_per_function(tmp_path, monkeypatch):
    monkeypatch.setattr(astgrep_parser, "MAX_CALLS_PER_FUNCTION", 3)
    body = "\n".join(f"    f{i}()" for i in range(10))
    funcs, _ = _functions(tmp_path, "a.py", f"def big():\n{body}\n")
    assert funcs["big"].metadata["calls"] == ["f0", "f1", "f2"]


def test_call_on_a_lambda_or_subscript_is_dropped(tmp_path):
    funcs, _ = _functions(
        tmp_path, "a.py", "def f(d):\n    d['k']()\n    (lambda: 1)()\n    real()\n"
    )
    assert funcs["f"].metadata["calls"] == ["real"]


def test_go_generic_receiver_type_is_found(tmp_path):
    funcs, _ = _functions(
        tmp_path, "g.go",
        "package main\n\nfunc (b *Box[T]) Get() int { return b.Other() }\n",
    )
    assert funcs["Get"].metadata.get("parent_class") == "Box"
