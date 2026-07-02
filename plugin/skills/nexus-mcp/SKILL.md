---
name: nexus-mcp
description: Route code search, symbol lookup, call-graph analysis, and codebase understanding through the nexus-mcp MCP server tools instead of Read/Grep/Glob. Use whenever exploring an indexed codebase, finding a symbol/function/class, tracing callers/callees or transitive change-impact before refactoring, getting a project overview/architecture summary, or persisting/recalling project context across sessions. Always run mcp__nexus-mcp__status first to check index state.
---

## When to use nexus-mcp tools instead of built-ins

1. **Session start**: run `status`. If `indexed: false`, run `index`. If
   `stale: true`, a background reindex is already triggered automatically — no
   action needed, but treat results from the next call or two as possibly a beat
   behind the latest edit.
2. **Before reading any file**: run `search` first — it returns code snippets, so a
   follow-up `Read` is often unnecessary.
3. **Before exploring a symbol**: use `find_symbol`, `explain`, or `graph` instead of
   grepping for it.
4. **Before refactoring or editing a widely-shared symbol**: run
   `graph(symbol_name, transitive=True)` to see the full change-impact blast radius —
   grep cannot show transitive impact.
5. **For project orientation**: use `map(detail="summary")` for a quick overview or
   `map(detail="architecture")` for design/dependency structure.
6. **Only fall back to Read/Grep/Glob** once nexus-mcp has named the specific
   files/lines you need to examine or edit, or for files outside the indexed
   codebase.

## Tool reference (10 tools)

| Tool | Use for |
|---|---|
| `index(path, paths)` | First run on a new/changed codebase. Comma-separated paths for multi-folder/monorepo indexing. Incremental by default; auto-reindex watcher starts after it completes. |
| `status()` / `health()` | Index freshness/stats (`status`) vs. liveness probe (`health`). |
| `search(query, mode, ...)` | Primary "where is / how does / find" tool. Falls back to live grep when sparse. |
| `find_symbol(name, exact)` | Look up a specific symbol; `exact=False` for fuzzy matching. |
| `graph(symbol_name, direction, transitive, max_depth)` | `direction="callers"`/`"callees"` for immediate call-graph edges; `transitive=True` (direction must be `"callers"`) for full change-impact analysis. |
| `explain(symbol_name, verbosity)` | Combined graph + semantic + quality-metrics view of one symbol — usually replaces a `Read`. |
| `analyze(path)` | Code quality: complexity, dependencies, smells, quality score. |
| `map(detail)` | `"summary"` for project overview, `"architecture"` for design structure, `"full"` for both. |
| `memory(action, ...)` | `action="store"`/`"search"`/`"delete"` — persist and retrieve project context/decisions across sessions. |

## Known limitations

- The call graph (`graph()`) only sees static edges — dynamic dispatch, closures,
  callbacks, and reflection are invisible to it. Treat `transitive=True` results as a
  lower bound on blast radius, not exhaustive, in highly dynamic code.
- `NEXUS_AUTO_WATCH` (default on) has a debounce window of a few seconds; treat very
  recent edits as possibly not yet reflected until the next `status`/`search` call
  triggers or confirms a refresh.

## v2.0.0 tool names

If you see project instructions referencing `find_callers`, `find_callees`,
`impact`, `overview`, `architecture`, `remember`, `recall`, or `forget` — those were
merged into `graph`/`map`/`memory` in v2.0.0. Use the mapping in the tool reference
table above instead.
