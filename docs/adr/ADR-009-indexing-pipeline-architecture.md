# ADR-009: Indexing Pipeline Architecture

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Nexus-MCP team

## Context
The indexing pipeline must coordinate two parsers (tree-sitter for symbols, ast-grep for graph), embedding, and storage into two engines (LanceDB vectors, rustworkx graph). It must support both full and incremental re-indexing within the <350MB RAM budget.

## Decision
8-step pipeline orchestrated by `IndexingPipeline`:
1. **Discover** files (pathspec for .gitignore, skip hidden/vendor dirs, filter by extension + size)
2. **Parse symbols** via tree-sitter (parallel, ThreadPoolExecutor)
3. **Parse graph** via ast-grep (sequential — parser has internal counters, not thread-safe)
4. **Chunk** symbols into CodeChunks
5. **Embed** in batches of 32 via EmbeddingService
6. **Store** vectors in LanceDB, graph nodes in rustworkx
7. **Save metadata** (filepath→mtime JSON for incremental detection)
8. **Unload model** via `embedding_service.unload()` to free ~50MB RAM

**Incremental re-indexing:** Compare file mtimes against stored metadata. Categories: new, modified (mtime changed), deleted (in metadata but not on disk). Only re-process changed files. Model unload is wrapped in `try/finally` to prevent memory leaks on exceptions.

**Full re-indexing:** Clears both engines before storing, ensuring a clean state.

## Consequences
- **Easier:** Incremental re-indexing is fast (only processes changed files); model unloading keeps idle RAM low.
- **Harder:** Mtime-based detection can miss changes on filesystems with low resolution (FAT32); only root `.gitignore` is parsed (nested gitignores ignored).
- ast-grep running sequentially is acceptable — it's fast without embedding overhead.

## Alternatives Considered
- **Content hashing for change detection**: More reliable but slower (must read every file). Deferred to Phase 5 hardening.
- **Single parser**: Rejected — tree-sitter excels at symbol extraction, ast-grep excels at structural relationships (ADR-005).
- **Parallel ast-grep**: Rejected — parser maintains internal counters (`_node_counter`, `_rel_counter`) that are not thread-safe.
