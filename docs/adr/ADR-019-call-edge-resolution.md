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
  Go, Java and Rust.
- `indexing/call_resolver.py` runs after the last batch in all four index paths
  (`index`, `incremental_index`, `_incremental_multi_index`, `multi_index`). It
  removes every `CALLS` edge and rebuilds them from node metadata. A rebuild is
  correct after any incremental change, and it needs no extra stored state: the
  metadata already persists with the nodes.
- Resolution tiers, in order: same file, imported file, unique name in the index.
  - `self.helper()` resolves to methods of the caller's own class.
  - `Class().method()` and `Class.method()` resolve to methods of that class.
  - `module.func()` resolves to functions in a module with that name.
  - A plain call in Python, JavaScript or TypeScript never resolves outside the
    same file and the imports. Otherwise a parameter named `callback` would link
    to an unrelated function named `callback`.
  - Method names that built-in types also have (`get`, `append`, `exists`, ...)
    resolve only inside the same file or an imported file.
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
- `analyze` dead-code results change: before, every function was "never called".
  The reason text now says "No static caller found".
- Function complexity and docstrings on ast-grep nodes are still empty. This ADR
  does not change that.
- `INHERITS` edges are still not extracted.
- The routing eval and the benchmark can now test the graph tools on real data.
