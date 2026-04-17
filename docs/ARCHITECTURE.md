# Architecture

Nexus-MCP is a unified Model Context Protocol (MCP) server that combines vector search, full-text search, code graph analysis, and semantic memory into a single process. It is designed to run locally with <350MB RAM.

Nexus-MCP consolidates two predecessor projects into a single server ([ADR-001](adr/ADR-001-single-mcp-consolidation.md)):
- **CodeGrok MCP** (by rdondeti / Ravitez Dondeti, MIT license) — Contributed the symbol extraction pipeline, embedding service, parallel indexing, core data models, and memory retrieval system.
- **code-graph-mcp** (by [entrepeneur4lyf](https://github.com/entrepeneur4lyf)) — Contributed the ast-grep structural parser, rustworkx graph engine, code complexity analysis, and relationship extraction.

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      MCP Client (IDE)                       │
└────────────────────────────┬────────────────────────────────┘
                             │ MCP Protocol (stdio/SSE)
┌────────────────────────────┴────────────────────────────────┐
│                    FastMCP Server (server.py)                │
│  15 Tools: index, search, status, health, find_symbol,       │
│  find_callers, find_callees, analyze, impact, explain,       │
│  overview, architecture, remember, recall, forget            │
├─────────────────────────────────────────────────────────────┤
│  Input Validation │ Graceful Shutdown │ JSON Logging         │
├─────────┬─────────┬─────────┬─────────┬─────────┬───────────┤
│ Vector  │  BM25   │  Graph  │ Live    │ Memory  │  Code     │
│ Engine  │ Engine  │ Engine  │ Grep    │ Store   │ Analysis  │
│(LanceDB)│(LanceDB)│(rustworkx)│(rg/grep)│(LanceDB)│           │
├─────────┴─────────┴─────────┴─────────┴─────────┴───────────┤
│  Indexing Pipeline (8 steps)                                │
│  discover → dual parse → chunk → embed → store              │
├─────────────────────────────────────────────────────────────┤
│  Parsing Layer                                              │
│  tree-sitter (symbols) + ast-grep (relationships)           │
├─────────────────────────────────────────────────────────────┤
│  ONNX Runtime (jina-code default, 3 models, GPU/MPS auto)   │
└─────────────────────────────────────────────────────────────┘
```

## Key Components

### Indexing Pipeline (`indexing/pipeline.py`)

The 8-step pipeline transforms source code into searchable indexes:

1. **Discover** — Walk directory tree, filter by extension/size/.gitignore
2. **Parse symbols** — tree-sitter extracts functions, classes, methods (parallel)
3. **Parse graph** — ast-grep extracts call/import/inheritance relationships (sequential)
4. **Transfer graph** — Populate rustworkx graph from ast-grep results
5. **Chunk** — Convert symbols to CodeChunks with deterministic IDs
6. **Embed** — ONNX Runtime generates vectors (768-dim jina-code default; 384-dim for bge-small-en)
7. **Store** — Write chunks to LanceDB, rebuild FTS index
8. **Cleanup** — Unload embedding model, save metadata for incremental reindex

Incremental reindexing uses mtime-based change detection: only new/modified files are re-processed. Corrupt indexes are auto-detected and rebuilt.

### Search Engines

**Vector Engine** (`engines/vector_engine.py`) — LanceDB-backed semantic search. Embeds queries at search time and performs flat cosine similarity search. Filters via SQL-escaped WHERE clauses.

**BM25 Engine** (`engines/bm25_engine.py`) — LanceDB's native Tantivy full-text search. Reads from the same `chunks` table. Good for exact keyword matches.

**Graph Engine** (`engines/graph_engine.py`) — rustworkx (Rust-backed) directed graph. Stores nodes (functions, classes) and edges (calls, imports, inheritance). Supports transitive traversal for impact analysis.

**Fusion** (`engines/fusion.py`) — Reciprocal Rank Fusion combines results from vector, BM25, and graph engines with configurable weights (default: 0.5/0.3/0.2).

**Reranker** (`engines/reranker.py`) — Optional FlashRank two-stage reranker. Gracefully degrades to passthrough if not installed.

### Dual Parser Strategy

**tree-sitter** — Fast, incremental parser for 25+ languages. Extracts symbol definitions (functions, classes, methods) with metadata (line numbers, docstrings, signatures). Runs in parallel via ThreadPool.

**ast-grep** — Structural search tool that extracts relationships between symbols (who-calls-whom, imports, inheritance). Runs sequentially to build a consistent graph.

This dual approach gets the best of both worlds: tree-sitter for fast symbol extraction and ast-grep for accurate structural relationships.

### Memory Store (`memory/memory_store.py`)

LanceDB-backed semantic memory with 11-column PyArrow schema. Supports:
- TTL-based expiration (permanent, month, week, day, session)
- Tag-based filtering with LIKE wildcards
- Memory types: note, decision, conversation, status, preference, doc

### State Management

`state.py` holds a global singleton `SessionState` with references to all engines. Lazy-loaded: engines are `None` until `index` is called. Thread-safe shutdown with lock-protected `shutdown()` method that persists graph state.

### Persistence

- **LanceDB** — Disk-backed vectors and FTS via mmap (~20-50MB overhead)
- **SQLite** (`persistence/store.py`) — Graph persistence for warm-start recovery
- **JSON metadata** — File mtimes for incremental reindex detection

## Data Flow

### Indexing
```
Source files → tree-sitter → Symbols → Chunker → CodeChunks
                                                      ↓
Source files → ast-grep → UniversalGraph → rustworkx   ONNX embed
                                                      ↓
                                              LanceDB (chunks table)
```

### Hybrid Search
```
Query → embed → Vector search (LanceDB)  ─┐
Query →       → BM25 search (Tantivy)     ├→ RRF Fusion → FlashRank → Live Grep (fallback) → Results
Query →       → Graph relevance search    ─┘
```

## Memory Budget

Target: <350MB RSS. Achieved through:
- ONNX Runtime (~50MB) instead of PyTorch (~500MB)
- LanceDB mmap (vectors stay on disk, ~20-50MB overhead)
- Lazy model loading — embedding model loaded during indexing, unloaded after
- GPU/MPS auto-detection (`NEXUS_EMBEDDING_DEVICE=auto`) for faster inference when available
- Two model options: jina-code (768d, default), bge-small-en (384d)

## Thread Safety

- Vector engine: RLock for write operations, concurrent reads allowed
- Graph engine: RLock for all mutations
- Pipeline: Threading Lock prevents concurrent indexing
- State shutdown: Lock-protected to prevent double-shutdown on signal + finally

## Configuration

All settings are in `config.py` with NEXUS_ env prefix. See README for the full table.
