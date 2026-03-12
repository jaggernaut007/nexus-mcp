# ADR-010: Graph Tools API Design

## Status
Accepted

## Date
2026-03-12

## Context
Phase 3 exposes the existing graph engine and code analyzer as 5 new MCP tools (`find_symbol`, `find_callers`, `find_callees`, `analyze`, `impact`). We needed to decide on serialization format, ambiguity handling, path filtering, and shared patterns.

## Decisions

### 1. Node Serialization via `_serialize_node()` Helper
All tools serialize `UniversalNode` to a consistent dict format including `id`, `name`, `type`, `language`, `location` (with relative file path), `complexity`, `line_count`, `docstring`, `visibility`, `is_async`, `return_type`, and `parameter_types`. This provides rich context for LLM consumers without requiring them to understand internal graph structures.

### 2. Ambiguity: Return All Matches
`find_symbol` returns **all** matching nodes when multiple share a name, with a `total` count and `symbols` list. This follows the same pattern as the `search` tool (`total` + `results`). The LLM consumer can refine or pick the correct match.

### 3. Path Filtering for `analyze`
The `analyze` tool accepts an optional `path` parameter. Rather than constructing a subgraph, it runs full analysis then filters results post-hoc by location prefix. This is simpler and avoids duplicating graph construction logic. Acceptable for Phase 3 codebases; can be optimized later if needed.

### 4. `_require_indexed()` Guard Pattern
A shared helper returns `(state, None)` or `(None, error_dict)`, deduplicating the "not indexed" check across all 5 new tools plus the existing `search` tool.

### 5. Relative Paths Everywhere
All tool outputs use paths relative to the indexed codebase root, consistent with the `search` tool convention from Phase 2.

## Consequences
- Consistent API surface across all 8 tools
- LLM consumers get rich, self-describing JSON without needing to parse internal IDs
- Post-hoc filtering is O(n) but acceptable for typical codebase sizes
- Adding new graph tools in future follows the established pattern
