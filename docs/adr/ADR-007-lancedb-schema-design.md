# ADR-007: LanceDB Schema Design for Code Chunks

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Nexus-MCP team

## Context
Phase 2 needs a vector database schema to store code chunks extracted from parsed symbols. The schema must support vector similarity search, metadata filtering (by language, symbol type), and incremental reindexing (delete by filepath).

## Decision
Use a PyArrow schema with 12 columns in a single `chunks` table:
- `id` (string) — deterministic SHA256 hash of `filepath:name:line_start`, truncated to 16 hex chars
- `vector` (list<float32>[N]) — embedding vector (768d for jina-code default, 384d for bge-small-en/granite-embedding-small)
- `text` (string) — formatted chunk text used for embedding
- `filepath`, `symbol_name`, `symbol_type`, `language` — metadata for filtering
- `line_start`, `line_end` (int32) — source location
- `signature`, `parent`, `docstring` — code context

Use flat search (no IVF index) for codebases up to ~100K chunks. IVF indexing can hang on large datasets (per LanceDB docs) and flat search is fast enough for typical codebases.

## Consequences
- **Easier:** Filtering by language/type via SQL-style `.where()` clauses; incremental reindex via `delete("filepath = '...'")`.
- **Harder:** Switching to a different embedding model requires re-indexing (different vector dimensions).
- Vector dimensions are configurable via `vector_dims` parameter on `LanceDBVectorEngine`.

## Alternatives Considered
- **Separate tables per language**: Rejected — adds complexity with minimal benefit since LanceDB filters are fast.
- **Storing raw code in vector table**: Rejected — `text` field contains the formatted chunk (signature + docstring + snippet), not raw source. Raw source can be read from disk.
