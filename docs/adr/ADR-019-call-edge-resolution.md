# ADR-019: Static Call-Edge Resolution

## Status: Accepted
## Date: 2026-10-02

## Context

`graph()`, the `callers`/`callees` fields of `explain()` and the dead-code check in
`analyze` all read `CALLS` edges. No parser created them. Real indexes held only
`CONTAINS` and `IMPORTS` edges, so these tools returned empty results. The tests
hid this: they built `CALLS` edges by hand, and `self_test/demo_mcp.py` passed on
empty results. The roadmap names the call graph as the one place where an index
beats agentic grep, so the gap hurt the main claim of the project.

## Decision

Extract callee names in the ast-grep parser. Resolve them to edges once, after the
last file is parsed.

- `parsing/astgrep_parser.py` stores on each function node `metadata["calls"]`
  (normalised dotted names) and `metadata["parent_class"]`. It stores on each module
  node `metadata["imports"]`. Call kinds exist for Python, JavaScript, TypeScript,
  Go, Java, Rust, C, C++, C# and PHP (C, C++, C# and PHP since 2026-11-05; see
  "Amendment: C, C++, C# and PHP" below).
- `indexing/call_resolver.py` runs after the last batch in all four index paths
  (`index`, `incremental_index`, `_incremental_multi_index`, `multi_index`). It
  removes every `CALLS` edge and rebuilds them from node metadata. A rebuild is
  correct after any incremental change, and it needs no extra stored state: the
  metadata already persists with the nodes.
- Precision first: a tier produces an edge only when exactly one callee of the
  caller's language fits. Several fitting callees mean no edge.
- Resolution tiers, in order: same file, imported file, unique name in the index.
  - `self.helper()`, `this.helper()` and Rust `Self::helper()` resolve to methods of
    the caller's own class. `super().m()` resolves to a method of another class.
  - `Class().method()` and `Class.method()` resolve to methods of that class.
  - `module.func()` resolves to functions in a module the caller imports.
  - Relative imports resolve from the caller's directory, so `from .store import x`
    does not match a `store.py` in another package. A candidate in another
    language never matches.
  - Java, C++ and C# call their own methods without a receiver, so a plain call
    also matches methods of the caller's own class there.
  - Dunder calls (`__init__`, ...) never resolve.
  - A plain call in Python, JavaScript or TypeScript never resolves outside the
    same file and the imports. Otherwise a parameter named `callback` would link
    to an unrelated function named `callback`.
  - Method names that built-in types also have (`get`, `append`, `exists`, ...) never
    resolve through a receiver of unknown type, so `self._data.get(key)` does not
    link to a project method named `get`.
  - A name with several candidates and no tie-breaker gets no edge.

## Alternatives considered

- **Use the tree-sitter `Symbol.calls` field.** It needs a mapping between two
  parsers by line number, and its `_get_call_name` returns the receiver (`db`) for
  `db.execute(...)`. The ast-grep parser already owns the graph nodes.
- **Store raw call records in SQLite.** The node metadata already persists with the
  node, so a second store adds drift and no information.
- **Emit edges to every candidate.** Tested on this repository: `table.delete`
  linked to three unrelated `delete` methods. A missing edge is safer than a wrong
  one, because `graph(transitive=True)` is documented as a lower bound.

## Consequences

- `graph()` and `explain()` return real callers and callees. Precision is high and
  recall is incomplete. Dynamic dispatch, callbacks and reflection stay invisible.
- **Coverage differs by language.** Python is the best covered. TypeScript and
  JavaScript cover `function` declarations and class methods but not arrow
  functions. Go methods get their receiver type. Rust `impl` blocks work, but
  `use crate::...` paths do not resolve. Java resolves same-class and imported-class
  calls. Calls through values whose type is unknown resolve only when the method
  name is unique.
- **Warm start.** Before this change a restarted server began with an empty graph,
  because the graph saved at shutdown was never loaded. `IndexingPipeline` now
  saves the graph after a full build and loads it on the next start. It rejects a
  saved graph that is older than the index metadata and rebuilds instead. AstGrep
  node ids carry a per-process token, because a restored graph holds ids from an
  earlier parser and a counter that restarts at 1 would reuse them.
- **Cost.** The pass is linear in the number of calls: about 9 s for 14,000 Python
  files (about 270k edges) on the machine used for testing. It holds the graph lock
  for that time. An incremental reindex still rebuilds all edges; re-resolving only
  the affected callers is future work.
- `analyze` dead-code results change: before, every function was "never called".
  The reason text now says "No static caller found".
- The same parser change adds cyclomatic complexity (branch points per function) and
  Python docstrings to graph nodes. Before it, `analyze` complexity was always zero.
  Only Python counts `and`/`or`.
- `INHERITS` edges are still not extracted.

## Amendment: C, C++, C# and PHP (2026-11-05)

The parser had no function, class or call kinds for C, C++, C# and PHP, so these
languages gave module nodes only. The same rules now apply to them, with these
additions. Precision still comes first.

- **Graph nodes.** C and C++ functions take their name and scope from the declarator,
  with the same helper as the tree-sitter parser (`c_declarator_name`). An out-of-line
  `void Widget::Paint()` is a method of `Widget`. A struct, union or class without a
  body (`struct point p;`, `class Foo;`) is not a class node. A C++ class head is
  masked for export macros first (`class BASE_EXPORT Foo`), and a `.h` file with C++
  syntax is parsed as C++. A declarator named like a keyword (`namespace` after an
  unknown macro) is a misparse and gives no node.
- **Constructors.** A C++ or C# constructor has the name of its class. When the class
  and its constructor are both candidates of a plain call (`new Order()`), the class is
  the callee, as for a Python class.
- **C has no methods.** `ops->read()` in C calls through a function pointer, so a call
  on a receiver never resolves in C.
- **C and C++ are one group.** A C++ caller can resolve to a C callee and the reverse,
  because a `.h` file can be either.
- **Includes are imports.** `#include "util.h"` imports the module path `util`, so the
  imported tier holds `util.h` and `util.c` (or `util.cc`). `<...>` includes are not
  imports.
- **C++ `ns::f()`.** A call through `::` whose qualifier is not a class is a free
  function in a namespace. It resolves like a plain call. A call through `std::` never
  resolves. `obj.f()` and `ptr->f()` stay calls on a receiver of unknown type.
- **PHP.** `$this->m()`, `self::m()` and `static::m()` are calls on the own class.
  `parent::m()` is a `super` call. `Helper::m()` is a class call. A namespace prefix
  (`\App\fmt()`) is dropped. `use App\Support\Helper;` imports the last two parts
  (`Support/Helper`), because a PSR-4 root such as `App` is often not a folder name.
- **C#.** `base.M()` is a `super` call. `using` names a namespace, not a file, so C#
  has no imported tier. `new Order()` resolves to the class `Order`.
- **Super calls by unique name.** In C# and PHP the base class is often in a file that
  is not imported. There a `super` call that has no candidate in the same or an imported
  file resolves when one method of another class has the name.
- **Method names of library types.** C# and C++ call library methods with names that
  the Python list does not have (`Add`, `ToString`, `push_back`, `size`). A call
  through a receiver of unknown type with one of these names does not resolve. For C#
  the check ignores case.
- The routing eval and the benchmark can now test the graph tools on real data.
