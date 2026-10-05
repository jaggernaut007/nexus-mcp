# Nexus-MCP Project Information

## Overview
Nexus-MCP is a unified code intelligence MCP (Model Context Protocol) server that consolidates two existing servers:
- **CodeGrok** — semantic vector search + memory layer
- **code-graph-mcp** — structural AST analysis + call graphs
- **Live Grep** — 100% coverage fallback via ripgrep/grep

Into a **single, memory-efficient MCP server** (about 90MB idle and 460MB with the model loaded, see docs/MEMORY.md) with 10 tools for code search, navigation, analysis, and memory.

---

## Problem Statement

### Current Pain Points
1. **Two separate MCP servers** — double the memory, double the startup time, two connections to manage
2. **High memory usage** — PyTorch + ChromaDB + large embedding models consumed 1-2GB+ RAM
3. **No hybrid search** — CodeGrok only does vector search; code-graph-mcp only does structural analysis
4. **No cross-engine intelligence** — can't combine "semantic meaning" with "who calls this function"

### What Nexus-MCP Solves
- Single process, single MCP connection, 10 tools
- Low memory: LanceDB mmap and a lazy-loaded model (see docs/MEMORY.md for measured numbers)
- Hybrid search combining vector + BM25 + graph signals + live-grep fallback
- Cross-engine tools like `explain` (graph + vector) and `graph` (static call-graph traversal)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Nexus-MCP Server (FastMCP, stdio)   │
├─────────────────────────────────────────────────┤
│  10 MCP Tools (thin wrappers in server.py)       │
│  index | status | health | search | find_symbol  │
│  graph | explain | analyze | map | memory        │
├─────────────────────────────────────────────────┤
│  core_api.py (transport-agnostic tool logic)     │
├─────────────────────────────────────────────────┤
│  Response Formatter (token budget optimization)  │
│  FlashRank Re-ranker (two-stage retrieval)       │
│  Reciprocal Rank Fusion (0.5/0.3/0.2 weights)   │
├─────────────────────────────────────────────────┤
│  Three Search Engines (parallel)                 │
│  ┌─────────┐ ┌──────────┐ ┌──────────────┐     │
│  │ Vector  │ │  BM25    │ │    Graph     │     │
│  │(LanceDB)│ │(LanceDB) │ │ (rustworkx)  │     │
│  │   ANN   │ │   FTS    │ │  traversal   │     │
│  └─────────┘ └──────────┘ └──────────────┘     │
├─────────────────────────────────────────────────┤
│  Dual Parsing Pipeline                           │
│  tree-sitter → symbols → embeddings → vectors   │
│  ast-grep → structure → relationships → graph    │
├─────────────────────────────────────────────────┤
│  Storage Layer                                   │
│  .nexus/lancedb/ → LanceDB (chunks + memories)   │
│  .nexus/graph.db → SQLite (graph persistence)    │
│  .nexus/index_metadata.json → mtimes, index stats│
└─────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| MCP Framework | FastMCP ≥2.0 | Standard MCP server framework |
| Vector + FTS Storage | LanceDB ≥0.4 | Embedded, mmap, vectors + FTS in one DB |
| Inference | PyTorch (bge-small-en) | The deprecated jina-code runs on ONNX Runtime |
| Embedding Model | bge-small-en (default) | 384 dims, lightweight; jina-code (768d) is deprecated |
| Symbol Parsing | tree-sitter 0.21.3 | Extract code symbols for embeddings |
| Structural Analysis | ast-grep-py ≥0.28 | Build the containment and import graph (plus callee names, resolved into call edges after indexing) |
| Graph Engine | rustworkx ≥0.15 | In-memory directed graph, Rust-backed |
| Re-ranking | FlashRank ≥0.2 | ONNX-based two-stage re-ranking |
| Live Grep | ripgrep (rg) / grep | 100% coverage fallback for unindexed files |
| File Watching | watchdog ≥3.0 | Debounced file change detection |

---

## Source Repositories

### CodeGrok (vector search + memory)
- **GitHub:** https://github.com/jaggernaut007/CodeGrok_mcp
- **Local clone:** `CodeGrok_mcp/`
- **What we port:** models, tree-sitter parser, embedding service, parallel indexer, memory retriever, state management
- **What we rewrite:** vector storage (ChromaDB → LanceDB), chunking pipeline

### code-graph-mcp (graph analysis)
- **GitHub:** https://github.com/entrepeneur4lyf/code-graph-mcp
- **Local clone:** `code-graph-mcp/`
- **What we port:** graph models, rustworkx graph engine, ast-grep parser, code analyzer, file watcher
- **What we rewrite:** server layer (merge into single server)

---

## 10 Tools

The v2.0.0 release merged 15 tools into 10 ([ADR-017](adr/ADR-017-tool-consolidation.md)).

### Indexing & Discovery
| Tool | Description | Engine |
|------|-------------|--------|
| `index` | Index one or more folders (incremental once an index exists); starts the file watcher | Pipeline → all engines |
| `status` | Index stats, memory usage, staleness warning | All engines |
| `health` | Liveness probe: uptime and engine availability | All engines |
| `map` | Project overview (`detail="summary"`), architecture (`"architecture"`) or both (`"full"`) | Graph + Code Analyzer |

### Search & Navigation
| Tool | Description | Engine |
|------|-------------|--------|
| `search` | Hybrid search with re-ranking and live-grep fallback | Vector + BM25 + Graph + FlashRank + rg/grep |
| `find_symbol` | Find definition and relationships | Graph |
| `graph` | Callers or callees of a symbol; `transitive=True` returns change impact | Graph (predecessors/successors) |

### Analysis
| Tool | Description | Engine |
|------|-------------|--------|
| `analyze` | Complexity, code smells, dependencies, quality score | Graph + Code Analyzer |
| `explain` | Symbol explanation: graph relationships + related code + metrics | Graph + Vector (synthesis) |

### Memory
| Tool | Description | Engine |
|------|-------------|--------|
| `memory` | `action="store"` / `"search"` / `"delete"`, with TTL and tags | Memory (LanceDB) |

Old names (`find_callers`, `find_callees`, `impact`, `overview`, `architecture`, `remember`, `recall`, `forget`) no longer exist. The [CHANGELOG](../CHANGELOG.md) has the full mapping.

---

## Performance Targets

| Metric | Target | Previous (two MCPs) |
|--------|--------|--------------------|
| Total RAM | ~90MB idle, ~460MB with the model loaded | ~1-2GB |
| Warm start | <5s | ~13s combined |
| Incremental reindex (1 file) | <1s | ~2s |
| Hybrid search + re-rank | <500ms | ~200ms (vector only) |
| `find_symbol` | <100ms | <1s |
| `explain` (symbol) | <2s | N/A (new) |
| Processes | 1 | 2 |

---

## Supported Languages (25+)

| Category | Languages |
|----------|-----------|
| Web & Frontend | JavaScript, TypeScript, HTML, CSS |
| Backend & Systems | Python, Java, C#, C++, C, Rust, Go |
| JVM | Java, Kotlin, Scala |
| Functional | Elixir, Elm, Haskell, OCaml, F# |
| Mobile | Swift, Dart |
| Scripting | Ruby, PHP, Lua |
| Data & Config | SQL, YAML, JSON, TOML |
| Markup | XML, Markdown |

---

## Storage Structure

```
.nexus/                          # Per-project, created by `index` tool
├── lancedb/                     # LanceDB tables
│   ├── chunks.lance/           # Code chunks + embeddings
│   └── memories.lance/         # Semantic memories
├── graph.db                    # SQLite (graph nodes + edges)
└── index_metadata.json         # File mtimes and index stats for incremental reindex
```

---

## Configuration

All settings via environment variables with `NEXUS_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_EMBEDDING_MODEL` | `bge-small-en` | Embedding model (`bge-small-en`; `jina-code` is deprecated) |
| `NEXUS_STORAGE_DIR` | `.nexus` | Per-project storage directory |
| `NEXUS_MAX_FILE_SIZE_MB` | `10` | Skip files larger than this (MB) |
| `NEXUS_LOG_FORMAT` | `text` | Logging format (`text` or `json`) |
| `NEXUS_EMBEDDING_BATCH_SIZE` | `32` | Embedding batch size |
| `NEXUS_AUTO_WATCH` | `true` | Auto-reindex on file change |
| `NEXUS_AUTO_RESTORE` | `true` | Reattach to the stored index when a new process starts |

The [README](../README.md#configuration) lists every variable.

---

## Related Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Implementation Plan | [docs/IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Original 5-phase build plan (historical; describes the pre-2.0.0 15-tool surface) |
| Research Notes | [docs/RESEARCH.md](RESEARCH.md) | Tech research (LanceDB, models, memory) |
| Roadmap | [docs/ROADMAP-2026.md](ROADMAP-2026.md) | Forward-looking priorities |
| ADRs | [docs/adr/](adr/) | 18 Architecture Decision Records |

---

## Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| LanceDB over ChromaDB | Embedded, mmap (disk-backed), native FTS, fewer dependencies |
| ONNX Runtime over PyTorch | 50MB vs 500MB RAM, 2.5x faster CPU inference |
| bge-small-en default | Lightweight 384d embeddings ([ADR-004](adr/ADR-004-bge-small-default-model.md)); jina-code (768d) is deprecated; GPU/MPS auto-detection |
| rustworkx over Neo4j | In-memory graph is sufficient, no DB server dependency |
| Single MCP over two | Halves memory, eliminates cross-process coordination |
| Dual parsers (tree-sitter + ast-grep) | Each excels at different task: symbols vs structure |
| Spec-driven development | Tests first ensures robustness across phases |
