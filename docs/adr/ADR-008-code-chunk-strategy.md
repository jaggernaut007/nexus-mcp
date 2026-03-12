# ADR-008: Symbol-Based Code Chunking Strategy

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Nexus-MCP team

## Context
Code must be split into chunks for embedding and vector search. The chunking strategy affects search relevance — chunks should be semantically meaningful units, not arbitrary token windows.

## Decision
Chunk boundaries follow symbol boundaries (functions, classes, methods). Each `Symbol` from tree-sitter becomes one `CodeChunk` with rich context:

**Chunk text format:**
```
# filepath:line_start
type: qualified_name

signature

docstring (truncated to 500 chars)

code_snippet (truncated to chunk_max_chars=4000)

Imports: ...
Calls: ...
```

**Chunk ID:** `sha256(filepath:name:line_start)[:16]` — deterministic, stable across re-indexes, collision probability ~2.7e-10 for 100K symbols.

**Parent tracking:** Methods include their parent class name in `qualified_name` (e.g., `MyClass.do_thing`), providing class context for search.

## Consequences
- **Easier:** Each search result maps to exactly one code symbol; results are actionable (file + line number).
- **Harder:** Code outside symbols (module-level statements, comments) is not indexed. Very large functions produce very large chunks.
- Truncation at 4000 chars (~1000-1300 tokens) keeps embedding quality high while fitting model context windows.

## Alternatives Considered
- **Sliding window chunking**: Rejected — produces chunks that split functions mid-body, reducing search relevance.
- **AST-level chunking**: Rejected — too fine-grained (individual statements); symbol-level is the right granularity for code search.
