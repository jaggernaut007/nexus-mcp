# ADR-002: LanceDB over ChromaDB for Vector + FTS Storage

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok used ChromaDB for vector storage, but it lacked native full-text search (required a separate rank-bm25 dependency), kept vectors in memory, and didn't support disk-backed mmap. We needed a single storage engine for both vector search and BM25 full-text search within our <350MB RAM target.

## Decision
Replace ChromaDB with LanceDB — an embedded, serverless vector database with native FTS, disk-backed storage via mmap, and SQL-like filtering.

## Consequences
- **Easier:** Single DB for vectors + FTS, mmap keeps vectors on disk (not in RAM), native upsert/delete, PyArrow integration
- **Harder:** Younger ecosystem than ChromaDB, async API docs are incomplete, IVF index can hang on 100K+ vectors (use flat search for small codebases), need `gc.collect()` after closing connections

## Alternatives Considered
- **ChromaDB + rank-bm25:** Rejected — two dependencies, no shared storage, ChromaDB keeps vectors in memory
- **Qdrant:** Rejected — requires running a separate server process; not embedded
- **SQLite + faiss:** Rejected — more moving parts, no native FTS integration with vector search
