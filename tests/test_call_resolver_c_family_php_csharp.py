"""CALLS edges for C, C++, C# and PHP (ADR-019, amendment of 2026-11-05).

Each test writes a small project with `tmp_path`, parses it with the real ast-grep
parser and resolves the calls as the pipeline does. No edge is built by hand. Most
tests also check an edge that must not exist: precision comes first.
"""

from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser


def _graph(tmp_path, files):
    universal = UniversalGraph()
    parser = AstGrepParser()
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    return graph


def _node(graph, name, file_hint="", parent=None):
    matches = [
        n for n in graph.find_nodes_by_name(name)
        if n.node_type in (NodeType.FUNCTION, NodeType.CLASS)
        and file_hint in n.location.file_path
        and (parent is None or n.metadata.get("parent_class") == parent)
    ]
    assert len(matches) == 1, f"{name}: {len(matches)} matches"
    return matches[0]


def _callees(graph, name, file_hint="", parent=None):
    node = _node(graph, name, file_hint, parent)
    return {
        (c.metadata.get("parent_class"), c.name, c.location.file_path.rsplit("/", 1)[-1])
        for c in graph.get_callees(node.id)
    }


C_FILES = {
    "src/util.h": "int util_go(int x);\n",
    "src/util.c": '#include "util.h"\nint util_go(int x) { return x; }\n',
    "src/main.c": (
        '#include <stdio.h>\n#include "util.h"\n'
        "struct ops { int (*read)(void); };\n"
        "static int helper(int a) { return a; }\n"
        "int main(struct ops *o) { helper(1); util_go(2); printf(\"x\"); o->read(); return 0; }\n"
    ),
    "src/other.c": "int read(void) { return 0; }\n",
}


def test_resolve_calls_c_same_file_and_included_file(tmp_path):
    graph = _graph(tmp_path, C_FILES)
    callees = _callees(graph, "main")
    assert (None, "helper", "main.c") in callees
    # `#include "util.h"` makes util.h and util.c imported files; the body is in util.c.
    assert (None, "util_go", "util.c") in callees


def test_resolve_calls_c_function_pointer_gets_no_edge(tmp_path):
    graph = _graph(tmp_path, C_FILES)
    # `o->read()` calls through a pointer; the free function `read` is not the callee.
    assert all(name != "read" for _, name, _ in _callees(graph, "main"))


CPP_FILES = {
    "app/widget.h": (
        "namespace app {\n"
        "class Widget {\n public:\n"
        "  void Draw() { Paint(); this->Layout(); }\n"
        "  void Paint();\n  void Layout() { }\n"
        "  static Widget* Create();\n"
        "};\n}\n"
    ),
    "app/widget.cc": (
        '#include "app/widget.h"\n'
        "namespace app {\n"
        "void Widget::Paint() { Widget::Create(); base::Log(); std::sort(a, b); v.push_back(1); }\n"
        "Widget* Widget::Create() { return new Widget(); }\n"
        "}\n"
    ),
    "base/log.cc": "namespace base {\nvoid Log() { }\nvoid sort() { }\n}\n",
}


def test_resolve_calls_cpp_own_method_without_receiver(tmp_path):
    graph = _graph(tmp_path, CPP_FILES)
    callees = _callees(graph, "Draw")
    # `Paint()` is defined out of line in widget.cc: a method of Widget all the same.
    assert ("Widget", "Paint", "widget.cc") in callees
    assert ("Widget", "Layout", "widget.h") in callees


def test_resolve_calls_cpp_scoped_calls(tmp_path):
    graph = _graph(tmp_path, CPP_FILES)
    callees = _callees(graph, "Paint", "widget.cc")
    assert ("Widget", "Create", "widget.cc") in callees  # a static method
    assert (None, "Log", "log.cc") in callees  # a function in a namespace
    # `std::sort` is the standard library, though the project has a `sort`.
    assert all(name != "sort" for _, name, _ in callees)


def test_resolve_calls_cpp_new_expression_links_the_class(tmp_path):
    graph = _graph(tmp_path, CPP_FILES)
    assert (None, "Widget", "widget.h") in _callees(graph, "Create", "widget.cc")


def test_resolve_calls_cpp_library_method_name_gets_no_edge(tmp_path):
    files = dict(CPP_FILES)
    files["app/list.h"] = "class List { public: void push_back(int x) { } };\n"
    graph = _graph(tmp_path, files)
    assert all(name != "push_back" for _, name, _ in _callees(graph, "Paint", "widget.cc"))


CSHARP_FILES = {
    "Shop/Order.cs": (
        "namespace Shop;\n"
        "public class Order : OrderBase\n{\n"
        "    public Order() { Init(); }\n"
        "    public void Submit(List<int> items) {\n"
        "        this.Validate(); Pricing.Total(); var o = new Order(); items.Add(1);\n"
        "        base.Audit(); helper.Notify();\n"
        "    }\n"
        "    private void Init() { }\n"
        "    private void Validate() { }\n"
        "    public void Add(int x) { }\n"
        "}\n"
    ),
    "Shop/Pricing.cs": "public static class Pricing { public static int Total() { return 0; } }\n",
    "Shop/OrderBase.cs": "public class OrderBase { public void Audit() { } }\n",
    "Shop/Mailer.cs": "public class Mailer { public void Notify() { } }\n",
}


def test_resolve_calls_csharp_own_class_and_static_calls(tmp_path):
    graph = _graph(tmp_path, CSHARP_FILES)
    callees = _callees(graph, "Submit")
    assert ("Order", "Validate", "Order.cs") in callees
    assert ("Pricing", "Total", "Pricing.cs") in callees
    assert (None, "Order", "Order.cs") in callees  # `new Order()` is the class
    assert ("OrderBase", "Audit", "OrderBase.cs") in callees  # `base.Audit()`
    # `helper` has an unknown type, and `Notify` is the only method with that name.
    assert ("Mailer", "Notify", "Mailer.cs") in callees
    assert ("Order", "Init", "Order.cs") in _callees(graph, "Order", parent="Order")


def test_resolve_calls_csharp_library_method_name_gets_no_edge(tmp_path):
    graph = _graph(tmp_path, CSHARP_FILES)
    # `items.Add(1)` is List.Add, not the project method `Order.Add` in the same file.
    assert ("Order", "Add", "Order.cs") not in _callees(graph, "Submit")


PHP_FILES = {
    "src/Support/Helper.php": (
        "<?php\nnamespace App\\Support;\n"
        "class Helper { public static function assist() { } }\n"
    ),
    "src/Models/Base.php": (
        "<?php\nnamespace App\\Models;\nclass Base { public function boot() { } }\n"
    ),
    "src/Models/User.php": (
        "<?php\nnamespace App\\Models;\n"
        "use App\\Support\\Helper;\n"
        "class User extends Base {\n"
        "    public function save() {\n"
        "        $this->validate(); self::make(); static::make(); parent::boot();\n"
        "        Helper::assist(); \\App\\format_name('x'); $u = new User(); $u->map();\n"
        "    }\n"
        "    private function validate() { }\n"
        "    public static function make() { }\n"
        "    public function map() { }\n"
        "}\n"
    ),
    "src/functions.php": "<?php\nnamespace App;\nfunction format_name($n) { return trim($n); }\n",
}


def test_resolve_calls_php_receivers(tmp_path):
    graph = _graph(tmp_path, PHP_FILES)
    callees = _callees(graph, "save")
    assert ("User", "validate", "User.php") in callees  # $this->
    assert ("User", "make", "User.php") in callees  # self:: and static::
    assert ("Base", "boot", "Base.php") in callees  # parent::
    assert ("Helper", "assist", "Helper.php") in callees  # use + Class::
    assert (None, "format_name", "functions.php") in callees  # namespace prefix dropped
    assert (None, "User", "User.php") in callees  # new User()


def test_resolve_calls_php_library_method_name_gets_no_edge(tmp_path):
    graph = _graph(tmp_path, PHP_FILES)
    # `$u->map()` on a receiver of unknown type: `map` is a collection method name.
    assert ("User", "map", "User.php") not in _callees(graph, "save")
