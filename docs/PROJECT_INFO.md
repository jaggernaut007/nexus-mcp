# Nexus-MCP Project Information

## Overview
Nexus-MCP is a unified code intelligence MCP (Model Context Protocol) server that consolidates two existing servers:
- **CodeGrok** — semantic vector search + memory layer
- **code-graph-mcp** — structural AST analysis + call graphs

Into a **single, memory-efficient MCP server** (<350MB RAM) with 12 tools for code search, navigation, analysis, and memory.

---

## Problem Statement

### Current Pain Points
1. **Two separate MCP servers** — double the memory, double the startup time, two connections to manage
2. **High memory usage** — PyTorch + ChromaDB + large embedding models consumed 1-2GB+ RAM
3. **No hybrid search** — CodeGrok only does vector search; code-graph-mcp only does structural analysis
4. **No cross-engine intelligence** — can't combine "semantic meaning" with "who calls this function"

### What Nexus-MCP Solves
- Single process, single MCP connection, 12 tools
- <350MB RAM via ONNX Runtime + LanceDB mmap + lightweight models
- Hybrid search combining vector + BM25 + graph signals with re-ranking
- Cross-engine tools like `explain` (graph + vector) and `impact` (graph traversal)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Nexus-MCP Server (FastMCP, stdio)   │
├─────────────────────────────────────────────────┤
│  12 MCP Tools                                    │
│  index | search | status | find_symbol           │
│  find_callers | find_callees | analyze | impact  │
│  explain | remember | recall | forget            │
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
│  .nexus/lance/   → LanceDB (vectors + memories)  │
│  .nexus/nexus.db → SQLite (graph persistence)    │
│  .nexus/metadata.json → Index stats              │
└─────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| MCP Framework | FastMCP ≥2.0 | Standard MCP server framework |
| Vector + FTS Storage | LanceDB ≥0.4 | Embedded, mmap, vectors + FTS in one DB |
| Inference | ONNX Runtime ≥1.16 | 50MB vs 500MB (PyTorch), 2.5x faster CPU |
| Embedding Model | jina-code (default) | 768 dims, code-specific; also bge-small-en (384d) |
| Symbol Parsing | tree-sitter 0.21.3 | Extract code symbols for embeddings |
| Structural Analysis | ast-grep-py ≥0.28 | Build call/import graphs, 25+ languages |
| Graph Engine | rustworkx ≥0.15 | In-memory directed graph, Rust-backed |
| Re-ranking | FlashRank ≥0.2 | ONNX-based two-stage re-ranking |
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

## 12 Tools

### Indexing & Search
| Tool | Description | Engine |
|------|-------------|--------|
| `index` | Index a codebase (auto/full/incremental) | Pipeline → all engines |
| `search` | Hybrid search with re-ranking | Vector + BM25 + Graph + FlashRank |
| `status` | Index stats, memory usage, language breakdown | All engines |

### Code Navigation
| Tool | Description | Engine |
|------|-------------|--------|
| `find_symbol` | Find definition + references | Graph |
| `find_callers` | Who calls this function? | Graph (predecessors) |
| `find_callees` | What does this function call? | Graph (successors) |

### Code Analysis
| Tool | Description | Engine |
|------|-------------|--------|
| `analyze` | Complexity, code smells, dependencies, quality score | Graph + Code Analyzer |
| `impact` | What breaks if I change X? | Graph (transitive callers) |
| `explain` | Architecture narrative for codebase/module/file | Graph + Vector (synthesis) |

### Memory
| Tool | Description | Engine |
|------|-------------|--------|
| `remember` | Store semantic memory with TTL and tags | Memory (LanceDB) |
| `recall` | Retrieve memories via semantic search | Memory (LanceDB) |
| `forget` | Delete memories by ID, type, or age | Memory (LanceDB) |

---

## Performance Targets

| Metric | Target | Previous (two MCPs) |
|--------|--------|--------------------|
| Total RAM | <350MB | ~1-2GB |
| Warm start | <5s | ~13s combined |
| Incremental reindex (1 file) | <1s | ~2s |
| Hybrid search + re-rank | <500ms | ~200ms (vector only) |
| `find_symbol` | <100ms | <1s |
| `explain` (codebase) | <2s | N/A (new) |
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
├── lance/                       # LanceDB tables
│   ├── codebase.lance/         # Code chunks + embeddings
│   └── memories.lance/         # Semantic memories
├── nexus.db                    # SQLite (graph nodes + edges)
└── metadata.json               # Index stats, config snapshot
```

---

## Configuration

All settings via environment variables with `NEXUS_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_EMBEDDING_MODEL` | `jina-code` | Embedding model (`jina-code`, `bge-small-en`) |
| `NEXUS_STORAGE_DIR` | `.nexus` | Per-project storage directory |
| `NEXUS_MAX_FILE_SIZE` | `1048576` | Max file size in bytes (1MB) |
| `NEXUS_LOG_FORMAT` | `text` | Logging format (`text` or `json`) |
| `NEXUS_BATCH_SIZE` | `32` | Embedding batch size |

---

## Related Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Implementation Plan | [docs/IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | 5-phase build plan |
| Research Notes | [docs/RESEARCH.md](RESEARCH.md) | Tech research (LanceDB, models, memory) |
| Market Research | [MARKET_RESEARCH_codegrpk.md](../MARKET_RESEARCH_codegrpk.md) | Competitive analysis |
| Original Spec | [NEXUS_PLAN.md](../NEXUS_PLAN.md) | Original architecture spec |
| MCP Integration | [MCP_Integration_Plan.md](../MCP_Integration_Plan.md) | MCP ecosystem integration |
| Agentic Setup | [agentic-setup-template.md](../agentic-setup-template.md) | Agent-ready project template |

---

## Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| LanceDB over ChromaDB | Embedded, mmap (disk-backed), native FTS, fewer dependencies |
| ONNX Runtime over PyTorch | 50MB vs 500MB RAM, 2.5x faster CPU inference |
| jina-code default | Code-specific 768d embeddings; 3 models supported, GPU/MPS auto-detection |
| rustworkx over Neo4j | In-memory graph is sufficient, no DB server dependency |
| Single MCP over two | Halves memory, eliminates cross-process coordination |
| Dual parsers (tree-sitter + ast-grep) | Each excels at different task: symbols vs structure |
| Spec-driven development | Tests first ensures robustness across phases |
