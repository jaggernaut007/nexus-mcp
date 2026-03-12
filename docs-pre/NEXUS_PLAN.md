# Nexus MCP — Unified Code Intelligence Server

## Context

CR8 currently runs **two separate MCP servers** for code intelligence:
- **CodeGrok** (semantic vector search, memory layer) — no file watcher, manual re-index
- **code-graph-mcp** (structural AST analysis, call graphs) — no persistence, rebuilt every session

This creates duplicate parsing, ~400MB RAM across two processes, no keyword search, and no cross-engine queries. The goal is to merge both into a **single production-grade MCP server** ("Nexus") that adds BM25 keyword search, re-ranking, rank fusion, graph persistence, and token-optimized responses.

**Market research insights incorporated:**
- LanceDB migration path (ChromaDB → LanceDB for native hybrid search)
- FlashRank re-ranking (two-stage retrieval is standard practice)
- Parent context in results (return class when method matches)
- `explain` tool (the #1 developer request — "explain this codebase")
- Path/directory scoping on search
- Configurable embedding model (smaller model option to reduce first-run friction)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Nexus MCP Server                          │
│                    (FastMCP, stdio)                            │
├────────┬────────┬──────────┬─────────┬─────────┬─────────────┤
│ index  │ search │find_symbol│ explain │ impact  │   memory    │
│        │(hybrid)│callers/ees│  (NEW)  │  (NEW)  │recall/forget│
├────────┴────────┴──────────┴─────────┴─────────┴─────────────┤
│               Response Formatter (token budget)               │
├──────────────────────────────────────────────────────────────┤
│            FlashRank Re-ranker (two-stage retrieval)          │
├──────────────────────────────────────────────────────────────┤
│                    Rank Fusion (RRF)                          │
├──────────────┬──────────────┬────────────────────────────────┤
│    Vector    │     BM25     │      Graph Engine               │
│    Engine    │    Engine    │      (rustworkx)                │
│  (ChromaDB → │ (rank_bm25 → │                                │
│   LanceDB)   │  tantivy)    │                                │
├──────────────┴──────────────┴────────────────────────────────┤
│              Unified Indexing Pipeline                        │
│   file_discovery → parallel_parse → fan-out to 3 engines     │
├──────────────────────────────────────────────────────────────┤
│   Parsing: ast-grep (graph/BM25) + tree-sitter (vectors)     │
├──────────────────────────────────────────────────────────────┤
│   File Watcher (watchdog, 2s debounce, incremental)          │
├──────────────────────────────────────────────────────────────┤
│   Persistence: ChromaDB (vectors) + SQLite (graph + BM25)    │
│   → Phase 3: LanceDB (vectors + BM25 native hybrid)         │
└──────────────────────────────────────────────────────────────┘
```

---

## Consolidated Tools (17 → 12)

| # | Tool | Replaces | Key Params | Purpose |
|---|------|----------|------------|---------|
| 1 | `index` | `learn` + `analyze_codebase` | `path, mode=auto, model=default` | Single init — triggers all 3 engines |
| 2 | `search` | `get_sources` + NEW BM25 | `query, n=10, engine=hybrid, path_filter, language, symbol_type, include_parent=false, verbosity=summary, max_tokens` | **Flagship.** RRF + re-rank. Path scoping. Parent context. |
| 3 | `find_symbol` | `find_definition` + `find_references` | `symbol, mode=definition\|references\|both` | Merged — agent almost always wants both |
| 4 | `find_callers` | `find_callers` | `function` | Unchanged |
| 5 | `find_callees` | `find_callees` | `function` | Unchanged |
| 6 | `analyze` | `complexity_analysis` + `dependency_analysis` + `project_statistics` | `aspect=overview\|complexity\|dependencies\|quality, threshold=10` | Single entry for all analytics |
| 7 | `explain` | **NEW** (#1 developer request) | `scope=codebase\|module\|file, path=None` | "Explain this codebase" — synthesizes graph + semantic into architecture narrative |
| 8 | `impact` | **NEW** | `symbol, depth=2` | "What breaks if I change X?" — callers + refs + semantic similarity |
| 9 | `remember` | `remember` | `content, memory_type, tags, ttl` | Unchanged (unique differentiator) |
| 10 | `recall` | `recall` | `query, memory_type, tags, n=5` | Unchanged |
| 11 | `forget` | `forget` | `memory_id, memory_type, older_than` | Unchanged |
| 12 | `status` | `get_stats` + `memory_stats` + `list_supported_languages` | `section=all\|index\|memory\|languages` | All status in one call |

**Removed:** `get_usage_guide` (bake into tool descriptions), `analyze_codebase` (merged into `index`), `project_statistics` / `memory_stats` / `list_supported_languages` (merged into `status`)

### `explain` Tool Design (the #1 developer request)

Synthesizes graph topology + semantic search + symbol counts into a narrative:

```
Input:  explain(scope="codebase")
Output: {
  "summary": "Python FastAPI backend with 3-agent LangGraph pipeline...",
  "entry_points": ["backend/app.py:main", "backend/run_pipeline.py:run_job"],
  "modules": [
    {"name": "pipeline", "purpose": "LangGraph orchestration", "files": 5, "key_symbols": ["run_pipeline", "PipelineState"]},
    ...
  ],
  "data_flow": "PDF upload → FileParser → ChromaDB → AgentIngest → AgentResearch → AgentGenerate → PDF/PPT/Video",
  "hot_spots": [{"file": "services/video_builder.py", "complexity": 47, "reason": "highest cyclomatic complexity"}],
  "token_count": 180
}
```

How it works internally:
1. `graph_engine.get_entry_points()` — nodes with high out-degree, no callers
2. `graph_engine.detect_modules()` — strongly connected components + directory grouping
3. `graph_engine.calculate_pagerank()` — rank symbols by importance (Aider repo-map concept)
4. `vector_engine.search("main purpose of this codebase")` — semantic summary context
5. `graph_engine.complexity_analysis()` — hot spots
6. Assemble into structured JSON narrative

### `search` Tool Enhancements (from market research)

New params vs original plan:
- **`path_filter`**: Scope search to directory (e.g., `"backend/services/"`) — #5 developer request
- **`include_parent`**: Return surrounding class/module context when a method matches — #8 developer request
- **Re-ranking stage**: FlashRank re-ranks RRF results before returning — standard two-stage retrieval

```
search flow:
  query → [vector_engine, bm25_engine, graph_engine] (parallel)
        → RRF fusion
        → FlashRank re-rank top-20
        → token budget truncation
        → response
```

---

## Package Structure

```
nexus-mcp/
  pyproject.toml
  src/nexus_mcp/
    __init__.py
    server.py                 # FastMCP server, 12 tool definitions, CLI entry
    config.py                 # Settings, env vars, constants
    state.py                  # Global singleton (engines + indexing status)

    parsing/
      unified_parser.py       # ast-grep (graph/BM25) + tree-sitter (chunks)
      language_registry.py    # Merged extension map (25+ languages)
      file_discovery.py       # Single os.walk + .gitignore + SKIP_DIRS
      file_watcher.py         # DebouncedFileWatcher (from code-graph-mcp)

    engines/
      vector_engine.py        # ChromaDB + CodeRankEmbed (from CodeGrok) → LanceDB in Phase 3
      graph_engine.py         # rustworkx PyDiGraph + algorithms (from code-graph-mcp)
      bm25_engine.py          # BM25 keyword search (rank_bm25) → LanceDB FTS in Phase 3
      fusion.py               # Reciprocal Rank Fusion (RRF)
      reranker.py             # FlashRank two-stage re-ranking (NEW)

    memory/
      memory_store.py         # ChromaDB-backed memory with TTL (from CodeGrok)

    indexing/
      pipeline.py             # Orchestrator: discover → parse → fan-out
      embedding_service.py    # Singleton model loader (lazy, GPU-aware, model-configurable)
      parallel_indexer.py     # ThreadPoolExecutor (4 workers)
      chunker.py              # Symbol → CodeChunk conversion (with parent context)

    formatting/
      token_budget.py         # Token estimation + smart truncation
      response_builder.py     # Verbosity: summary (<200 tok) / detailed (<800) / full (<2000)

    persistence/
      store.py                # SQLite manager for graph + BM25
      graph_serializer.py     # rustworkx ↔ SQLite (nodes/edges tables)
      bm25_serializer.py      # BM25 inverted index ↔ SQLite
```

Storage directory per project:
```
.nexus/
  chroma/          # ChromaDB (vectors + memories) — Phase 1-2
  nexus.db         # SQLite (graph nodes/edges, BM25 inverted index, file metadata)
  metadata.json    # Index stats, config snapshot
  # Phase 3: .lance/ replaces chroma/ for native hybrid
```

---

## Search Pipeline: RRF + Re-ranking

```
search("how does auth work", n=10)
  │
  ├─ vector_engine.search(query, n=30)     ─┐
  ├─ bm25_engine.search(query, n=30)        ├─ parallel
  └─ graph_engine.boost(query, n=30)        ─┘
                                              │
                              reciprocal_rank_fusion(k=60)
                              weights: [0.5, 0.3, 0.2]
                                              │
                              flashrank_rerank(top_20)     ← NEW (two-stage)
                                              │
                              apply path_filter, include_parent
                                              │
                              token_budget_truncate(max_tokens)
                                              │
                              return top-n with verbosity formatting
```

**Re-ranker choice: FlashRank**
- ~4MB ONNX model (vs 500MB+ cross-encoder)
- <10ms for top-20 re-ranking
- MIT license
- Upgrade path: `bge-reranker-base` via CrossEncoder in Phase 3

---

## Token Optimization Strategy

**Problem:** Current tools return 1-3K tokens of verbose markdown per call.

**Solution — `verbosity` param on all tools:**

| Level | Target | Format | Content |
|-------|--------|--------|---------|
| `summary` (default) | <200 tokens | JSON | Counts + top-3 results, file:line only |
| `detailed` | <800 tokens | JSON | All results with signatures, no code |
| `full` | <2000 tokens | Markdown | Code snippets, docstrings, metadata |

Additional optimizations:
- `max_tokens` param on `search` — agent specifies budget, results truncated to fit
- `token_count` field in every response — agent knows the cost
- No emojis in any output (strip from code-graph-mcp's current format)
- JSON default (agents parse JSON more efficiently than markdown)
- Parent context only when `include_parent=true` (avoids bloat by default)

---

## Persistence Strategy

### Phase 1-2: ChromaDB + SQLite

| Data | Store | Rationale |
|------|-------|-----------|
| Vectors + embeddings | ChromaDB (SQLite + parquet) | Purpose-built, already works |
| Memory entries | ChromaDB (separate collection) | Same as current CodeGrok |
| Graph nodes/edges | SQLite `nexus.db` | rustworkx has no serialization; SQLite is simple |
| BM25 inverted index | SQLite `nexus.db` | Relational data, fast load |
| File metadata (mtime) | SQLite `nexus.db` | Single source of truth for incremental |

### Phase 3: LanceDB Migration (from market research)

Replace ChromaDB + rank_bm25 with LanceDB:
- **Native hybrid search** (FTS via Tantivy + vector) in one engine
- Eliminates rank_bm25 dependency entirely
- Eliminates ChromaDB dependency entirely
- **Single storage format** (.lance files) instead of chroma/ + nexus.db
- Better performance on large codebases (columnar format)
- Still local-first, zero cloud dependency

Post-migration storage:
```
.nexus/
  lance/           # LanceDB (vectors + BM25 + memories — all-in-one)
  nexus.db         # SQLite (graph nodes/edges only — rustworkx persistence)
  metadata.json
```

---

## Embedding Model Strategy (from market research)

**Problem:** 500MB model download on first use is adoption friction.

**Solution — configurable model with a lightweight default option:**

| Model | Dims | Size | Quality | Use Case |
|-------|------|------|---------|----------|
| `nomic-ai/CodeRankEmbed` (default) | 768 | ~500MB | Best | Production, large codebases |
| `BAAI/bge-small-en-v1.5` | 384 | ~50MB | Good | Quick start, small projects |
| `jina-embeddings-v2-base-code` | 768 | ~500MB | Comparable | Alternative if CodeRankEmbed issues |

Config via `NEXUS_EMBEDDING_MODEL` env var or `index(model="bge-small")` param.

---

## Dependencies (all MIT/Apache-2.0 compatible)

| Package | Purpose | License | Phase |
|---------|---------|---------|-------|
| fastmcp >=2.0.0 | MCP server framework | MIT | 1 |
| tree-sitter 0.21.3 | Symbol parsing for chunks | MIT | 1 |
| tree-sitter-languages >=1.10.0 | Grammar packs | MIT | 1 |
| chromadb >=1.3.0 | Vector store | Apache-2.0 | 1-2 |
| sentence-transformers >=2.2.0 | Embedding model | Apache-2.0 | 1 |
| torch >=2.0.0 | ML runtime | BSD-3 | 1 |
| ast-grep-py >=0.28.0 | AST analysis (Rust) | MIT | 2 |
| rustworkx >=0.15.0 | Graph algorithms (Rust) | Apache-2.0 | 2 |
| rank-bm25 >=0.2.2 | BM25 keyword search | Apache-2.0 | 2 |
| flashrank >=0.2.0 | Re-ranking (ONNX, 4MB) | Apache-2.0 | 2 |
| watchdog >=3.0.0 | File system monitoring | Apache-2.0 | 1 |
| pathspec >=0.11.0 | .gitignore matching | MPL-2.0 | 1 |
| einops >=0.7.0 | Tensor ops | MIT | 1 |
| lancedb >=0.4.0 | Native hybrid search | Apache-2.0 | 3 |

---

## Security Model

1. **Path traversal:** All paths resolved via `Path.resolve()`, validated against project root
2. **File size:** Skip files >1MB (`NEXUS_MAX_FILE_SIZE` env var)
3. **.gitignore:** Always respected + hardcoded SKIP_DIRS (`.git`, `node_modules`, `__pycache__`, `.venv`)
4. **No eval/exec:** Zero dynamic code execution
5. **Input validation:** Symbol names max 200 chars, alphanumeric + `_.-`
6. **Memory limits:** Collection size cap, configurable max indexed files
7. **Graceful shutdown:** SIGTERM/SIGINT handlers, file watcher cleanup
8. **Model safety:** Only load from trusted HuggingFace repos (allowlist)

---

## Performance Targets

| Metric | Target | Current (two MCPs) |
|--------|--------|--------------------|
| Warm start | <5s | CodeGrok ~3s + code-graph 10-60s |
| Incremental (1 file) | <1s | CodeGrok: N/A, code-graph: ~2s |
| Hybrid search + re-rank | <500ms | ~200ms (vector only, no re-rank) |
| find_symbol | <100ms | <1s |
| explain (codebase) | <2s | N/A (doesn't exist) |
| RAM (50K symbols) | <400MB | ~400MB (two processes) |
| Processes | 1 | 2 |
| First-run model download | ~50MB (small) / ~500MB (full) | ~500MB (no choice) |

---

## Implementation Phases

### Phase 1: Foundation (3-4 days)
Scaffold + port vector engine + basic tools.

1. Create `nexus-mcp/` with pyproject.toml, package structure
2. Port `file_discovery.py` from CodeGrok `discover_files()`
3. Port `embedding_service.py` (singleton, lazy, GPU-aware, **model-configurable**)
4. Port `vector_engine.py` from CodeGrok `SourceRetriever` (ChromaDB + chunks)
5. Port `file_watcher.py` from code-graph-mcp `DebouncedFileWatcher`
6. Port `chunker.py` with **parent context support** (store class context alongside method chunks)
7. Implement `server.py` with 4 tools: `index`, `search` (vector-only), `find_symbol`, `status`
8. Add **`path_filter`** param to search (filter by directory scope)
9. Wire file watcher → vector engine (fixes CodeGrok's biggest gap)
10. Tests: 30+ covering indexing, search, file watching, path filtering

### Phase 2: Graph + BM25 + Re-ranking + Fusion (3-4 days)
Add structural analysis, keyword search, re-ranking. All 12 tools.

1. Port `graph_engine.py` from code-graph-mcp `RustworkxCodeGraph`
2. Port `unified_parser.py` from code-graph-mcp `UniversalParser` (ast-grep)
3. Build unified indexing pipeline: parse once → fan-out to 3 engines
4. Implement `bm25_engine.py` using rank_bm25
5. Implement `fusion.py` (RRF algorithm)
6. Implement `reranker.py` using **FlashRank** (two-stage retrieval)
7. Add tools: `find_callers`, `find_callees`, `analyze`, `impact`
8. Implement **`explain` tool** — the #1 developer request (graph + semantic synthesis)
9. SQLite persistence for graph + BM25
10. Port memory layer: `remember`, `recall`, `forget`
11. Implement `response_builder.py` + `token_budget.py`
12. Tests: 50+ additional

### Phase 3: Production Hardening + LanceDB Migration (2-3 days)
Reliability, performance, storage unification, distribution.

1. **LanceDB migration** — replace ChromaDB + rank_bm25 with native hybrid search
2. Graceful shutdown + signal handlers
3. Structured logging (JSON option)
4. Warm start optimization (benchmark SQLite → rustworkx load)
5. Memory profiling for 50K-symbol target
6. Corrupt index detection + auto-rebuild
7. End-to-end benchmarks against CR8 codebase
8. PyPI packaging (`uv publish` as `nexus-mcp`)
9. Update CR8 integration: `.claude/mcp.json`, agents, CLAUDE.md, AGENTS.md, ADR

### Future Additions (post-v1.0)
- **Cross-encoder re-ranking** upgrade (`bge-reranker-base` via CrossEncoder)
- **Multi-project** indexing (multiple roots)
- **Diff-aware search** ("what changed recently related to X")
- **Code generation context** ("everything I need to implement feature X")
- **Query expansion** — synonym awareness (auth=login=authentication)
- **Smithery listing** + awesome-mcp-servers PR
- **SSE/HTTP transport** for remote/hosted mode

---

## Distribution Plan (from market research — biggest gap today)

1. **PyPI**: Publish as `nexus-mcp` (`pip install nexus-mcp` / `uv tool install nexus-mcp`)
2. **Smithery.ai**: Add `smithery.yaml` for one-click install (2,000+ server marketplace)
3. **GitHub**: Add `mcp-server` topic, comprehensive README with GIF demos
4. **awesome-mcp-servers**: Submit PR (30K+ stars, primary discovery channel)
5. **Glama.ai**: Submit to directory (1,000+ servers)
6. **Integration docs**: Claude Desktop, Cursor, VS Code, Windsurf setup guides
7. **Positioning**: "The only MCP server with hybrid search + code graph + memory — fully local"

---

## Migration Path

1. Build Nexus as standalone repo (`~/dev/nexus-mcp/`)
2. Validate all 12 tools against CR8 codebase
3. Update `.claude/mcp.json`: replace `codegrok` + `code-graph` entries with single `nexus`
4. Update agent configs (code-reviewer, debug-detective, research-assistant) with new tool names
5. Update CLAUDE.md, AGENTS.md docs
6. Remove `.codegrok/` and `.codegraph/` directories
7. Write ADR-012 documenting the consolidation decision

---

## Critical Source Files to Port

| Source | Destination | What to port |
|--------|-------------|-------------|
| `CodeGrok_mcp/.../source_retriever.py` (990 LOC) | `engines/vector_engine.py` | ChromaDB ops, chunk pipeline, search |
| `CodeGrok_mcp/.../embedding_service.py` (487 LOC) | `indexing/embedding_service.py` | Model loading, batching, LRU cache |
| `CodeGrok_mcp/.../treesitter_parser.py` (991 LOC) | `parsing/unified_parser.py` | Symbol extraction for chunks |
| `CodeGrok_mcp/.../memory_retriever.py` (499 LOC) | `memory/memory_store.py` | Memory CRUD + TTL |
| `code-graph-mcp/rustworkx_graph.py` (1524 LOC) | `engines/graph_engine.py` | Graph storage, algorithms, centrality |
| `code-graph-mcp/universal_parser.py` (1035 LOC) | `parsing/unified_parser.py` | ast-grep parsing for graph |
| `code-graph-mcp/file_watcher.py` (263 LOC) | `parsing/file_watcher.py` | Debounced watcher |
| `code-graph-mcp/server.py` (1413 LOC) | `server.py` | Tool handler patterns, analysis engine |

---

## Verification Plan

1. **Unit tests:** 80+ tests covering each engine, parser, persistence, re-ranker
2. **Integration test:** Index CR8 codebase (~27K LOC), run all 12 tools, verify results
3. **A/B comparison:** Compare search quality (hybrid + re-rank) vs current CodeGrok-only search on 20 real queries
4. **Performance benchmark:** Cold start, warm start, search latency, RAM — compare against current two-MCP setup
5. **Security scan:** `snyk_code_scan` on all new code
6. **Agent smoke test:** Run code-reviewer, debug-detective, research-assistant against CR8 with Nexus as sole MCP
7. **Regression:** Ensure `make test` (CR8's 1063 tests) still passes with new MCP config
8. **Token savings:** Measure actual token usage per tool call at each verbosity level
