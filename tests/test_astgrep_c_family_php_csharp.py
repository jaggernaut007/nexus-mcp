"""ast-grep graph nodes for C, C++, C# and PHP.

Before 2.2.3 `_extract_functions` and `_extract_classes` had node kinds only for
Python, JavaScript, TypeScript, Go, Java and Rust. A C, C++, C# or PHP file gave a
module node and nothing else, so `graph`, `analyze` and the call resolver had no data.
"""

from nexus_mcp.core.graph_models import NodeType, UniversalGraph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser

C_SOURCE = """\
#include <stdio.h>
#include "util.h"
struct point { int x; };
typedef struct { int y; } anon_t;
union value { int a; float b; };
static int helper(int a) { if (a) { return 1; } return a ? 2 : 3; }
char *make_name(void) { return 0; }
int main(void) { struct point p; helper(1); return 0; }
int declared_only(int);
"""

CPP_SOURCE = """\
#include "widget.h"
namespace app {
class BASE_EXPORT Widget : public Base {
 public:
  void Draw() const { Paint(); }
  void Paint();
  struct Inner { void Run() { } };
};
template <typename T> class Box { public: T get() { return v; } T v; };
}
void app::Widget::Paint() { for (int i = 0; i < 3; ++i) { } }
app::Widget::~Widget() { }
bool operator==(const Widget& a, const Widget& b) { return true; }
class Outer::Nested { };
"""

CSHARP_SOURCE = """\
namespace Acme.Shop;

public class Order
{
    public Order(int total) { Init(); }
    public void Add(int x) { if (x > 0) { } foreach (var y in items) { } }
    private void Init() { }
    public class Line { public void Ship() { } }
}
public interface IOrder { void Add(int x); }
public struct Money { public Money Plus() { return this; } }
public record Customer(string Name);
"""

PHP_SOURCE = """\
<?php
namespace App;

interface Greeter { public function greet(string $n): string; }
trait Loud { public function shout() { return 1; } }
final class User implements Greeter {
    public function __construct() { }
    public function greet(string $n): string { if ($n) { return $n; } return ''; }
}
enum Suit { case Hearts; public function color() { return 'Red'; } }
function format_name($n) { return trim($n); }
"""


def _graph(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    graph = UniversalGraph()
    AstGrepParser().parse_file(str(path), graph)
    return graph


def _nodes(graph, node_type):
    return {n.name: n for n in graph.nodes.values() if n.node_type == node_type}


def test_extract_functions_c_names_from_declarator(tmp_path):
    funcs = _nodes(_graph(tmp_path, "main.c", C_SOURCE), NodeType.FUNCTION)
    # `char *make_name(void)` is `make_name`, not the return type.
    assert set(funcs) == {"helper", "make_name", "main"}
    assert funcs["helper"].complexity == 3
    assert "parent_class" not in funcs["main"].metadata


def test_extract_classes_c_structs_with_a_body_only(tmp_path):
    classes = _nodes(_graph(tmp_path, "main.c", C_SOURCE), NodeType.CLASS)
    # `struct point p;` names the struct but does not define it. The anonymous struct
    # takes the name of its typedef.
    assert set(classes) == {"point", "anon_t", "value"}


def test_extract_functions_cpp_methods_get_their_class(tmp_path):
    funcs = list(
        n for n in _graph(tmp_path, "widget.cc", CPP_SOURCE).nodes.values()
        if n.node_type == NodeType.FUNCTION
    )
    parents = {(f.name, f.metadata.get("parent_class")) for f in funcs}
    assert ("Draw", "Widget") in parents
    assert ("Run", "Inner") in parents
    assert ("get", "Box") in parents
    # Out of line: the class comes from the declarator scope, not from the namespace.
    assert ("Paint", "Widget") in parents
    assert ("~Widget", "Widget") in parents
    assert ("operator==", None) in parents


def test_extract_classes_cpp_masks_export_macros(tmp_path):
    classes = _nodes(_graph(tmp_path, "widget.cc", CPP_SOURCE), NodeType.CLASS)
    # `class BASE_EXPORT Widget` is the class `Widget`; `class Outer::Nested` is `Nested`.
    assert {"Widget", "Inner", "Box", "Nested"} <= set(classes)
    assert "BASE_EXPORT" not in classes


def test_parse_file_cpp_header_with_cpp_syntax_is_cpp(tmp_path):
    graph = _graph(tmp_path, "widget.h", "namespace a {\nclass W { void f() { } };\n}\n")
    funcs = _nodes(graph, NodeType.FUNCTION)
    assert funcs["f"].language == "cpp"
    assert funcs["f"].metadata.get("parent_class") == "W"


def test_extract_functions_csharp(tmp_path):
    funcs = list(
        n for n in _graph(tmp_path, "Order.cs", CSHARP_SOURCE).nodes.values()
        if n.node_type == NodeType.FUNCTION
    )
    parents = {(f.name, f.metadata.get("parent_class")) for f in funcs}
    assert {("Order", "Order"), ("Add", "Order"), ("Init", "Order")} <= parents
    assert ("Ship", "Line") in parents
    assert ("Plus", "Money") in parents
    add = next(f for f in funcs if f.name == "Add" and f.metadata.get("parent_class") == "Order")
    assert add.complexity == 3


def test_extract_classes_csharp(tmp_path):
    classes = _nodes(_graph(tmp_path, "Order.cs", CSHARP_SOURCE), NodeType.CLASS)
    assert {"Order", "Line", "IOrder", "Money", "Customer"} <= set(classes)


def test_extract_functions_php(tmp_path):
    funcs = list(
        n for n in _graph(tmp_path, "User.php", PHP_SOURCE).nodes.values()
        if n.node_type == NodeType.FUNCTION
    )
    parents = {(f.name, f.metadata.get("parent_class")) for f in funcs}
    assert {("greet", "User"), ("__construct", "User"), ("shout", "Loud")} <= parents
    assert ("color", "Suit") in parents
    assert ("format_name", None) in parents
    greet = next(f for f in funcs if f.name == "greet" and f.metadata.get("parent_class"))
    assert greet.metadata["parent_class"] in ("User", "Greeter")


def test_extract_classes_php(tmp_path):
    classes = _nodes(_graph(tmp_path, "User.php", PHP_SOURCE), NodeType.CLASS)
    assert {"Greeter", "Loud", "User", "Suit"} <= set(classes)


def test_extract_functions_cpp_keyword_is_not_a_function_name(tmp_path):
    # fmt: `FMT_BEGIN_NAMESPACE namespace detail {` was the function `namespace`.
    source = "FMT_BEGIN_NAMESPACE namespace detail {\nvoid parse_width() { }\n}\n"
    funcs = _nodes(_graph(tmp_path, "impl.cc", source), NodeType.FUNCTION)
    assert "namespace" not in funcs
    assert "parse_width" in funcs
