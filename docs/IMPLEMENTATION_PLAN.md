# Nexus-MCP Implementation Plan

## Context
Nexus-MCP consolidates CodeGrok + code-graph-mcp into **one single MCP server** with hybrid search, code graph analysis, and semantic memory. Target: <350MB RAM.

**Key decisions:**
- **Single MCP** — one process, one connection, 12 tools
- **LanceDB** — vectors + full-text search in one DB (no ChromaDB, no rank-bm25)
- **ONNX Runtime** — replaces PyTorch (~50MB vs ~500MB), 2.5x faster CPU inference
- **jina-code default** — code-specific 768d embedding model; bge-small-en as alternative
- **rustworkx code graph** — in-memory directed graph, not a knowledge graph DB
- **Port from both repos** — `CodeGrok_mcp/` + `code-graph-mcp/` (already cloned in project)
- **Spec-driven** — tests first, then implement

---

## Phase 1: Scaffold + Port Core Modules

**Goal:** Project structure, agentic setup, port all reusable code from both repos. No new logic — just reorganize.

### 1.1 Project Setup
- `pyproject.toml` (hatchling, Python ≥3.10, all deps)
- `AGENTS.md` (<100 lines), `CLAUDE.md`, `PROGRESS.md`, `.gitignore`
- `scripts/init.sh` (env check, import test, ruff, pytest)
- `.claude/hooks.json` (ruff on Stop + PostToolUse)
- `.claude/agents/` — code-reviewer, research-assistant, test-runner, docs-writer
- `.claude/rules/test-standards.md`
- Configure MCPs: Context7, Sequential Thinking

### 1.2 Port from CodeGrok
| Source | Target | Action |
|--------|--------|--------|
| `core/models.py` | `core/models.py` | Direct port |
| `core/interfaces.py` | `core/interfaces.py` | Port + add IEngine |
| `core/exceptions.py` | `core/exceptions.py` | Direct port |
| `parsers/treesitter_parser.py` | `parsing/treesitter_parser.py` | Direct port |
| `parsers/language_configs.py` | `parsing/language_registry.py` | Merge with code-graph-mcp |
| `indexing/embedding_service.py` | `indexing/embedding_service.py` | Port + add bge-small + ONNX |
| `indexing/parallel_indexer.py` | `indexing/parallel_indexer.py` | Direct port |
| `mcp/state.py` | `state.py` | Port + extend |

### 1.3 Port from code-graph-mcp
| Source | Target | Action |
|--------|--------|--------|
| `universal_graph.py` | `core/graph_models.py` | Direct port |
| `rustworkx_graph.py` | `engines/graph_engine.py` | Direct port |
| `universal_parser.py` | `parsing/astgrep_parser.py` | Direct port |
| `universal_ast.py` | `analysis/code_analyzer.py` | Direct port |
| `file_watcher.py` | `parsing/file_watcher.py` | Direct port |

### 1.4 Write fresh stubs
- `config.py` — Settings dataclass with `NEXUS_` env prefix
- `server.py` — FastMCP instance, no tools yet

### 1.5 Tests & Verification
- Tests for ported modules (models, parser, graph engine) — 30+ tests
- `pip install -e ".[dev]"` succeeds
- `ruff check .` clean
- `scripts/init.sh` passes

### 1.6 Post-Phase Review *(standard for all phases)*
- [ ] Run **code-reviewer agent** — architecture, tests, memory safety, thread safety, ruff
- [ ] Run **docs-writer agent** — update PROGRESS.md, CLAUDE.md, README, docstrings
- [ ] Write **ADRs** for key decisions made in this phase (docs/adr/)
- [ ] Update **docs/research/INDEX.md** — add research notes for any new libraries
- [ ] Run **Snyk security scan** on new/modified code
- [ ] Verify all tests pass and linter is clean

---

## Phase 2: Indexing Pipeline + Vector Search (3 tools)

**Goal:** Index a codebase into LanceDB + rustworkx graph. First 3 working tools.

### 2.1 Tests first
| Test File | Covers |
|-----------|--------|
| `test_config.py` | Defaults, env vars, model selection |
| `test_vector_engine.py` | LanceDB CRUD, search, filters, mmap |
| `test_chunker.py` | Symbol→CodeChunk, parent context |
| `test_pipeline.py` | Full pipeline, incremental reindex |
| `test_tools_basic.py` | index, search, status tools |

### 2.2 Implement
- **`config.py`** — embedding model selection, storage paths, memory limits
- **`engines/vector_engine.py`** — LanceDB: connect, add, search, delete, upsert
- **`indexing/chunker.py`** — port from CodeGrok `source_retriever.py` chunk logic
- **`indexing/pipeline.py`** — discover → parse (both parsers) → chunk → embed → store + build graph
- **`server.py`** — 3 tools: `index`, `search`, `status`

### 2.3 Verification
- 50+ tests pass
- Smoke: index CodeGrok's own codebase, search "embedding", get relevant results
- Memory: <300MB during indexing (check with `tracemalloc`)

### 2.4 Post-Phase Review
- [ ] Run **code-reviewer agent** + **docs-writer agent**
- [ ] Write ADRs (LanceDB schema, chunking strategy, pipeline architecture)
- [ ] Research notes for LanceDB API, ONNX Runtime usage
- [ ] Snyk scan + all tests green

---

## Phase 3: Graph Tools + Code Analysis (5 tools)

**Goal:** Expose graph engine + code analyzer as MCP tools. Total 8 tools.

### 3.1 Tests first
| Test File | Covers |
|-----------|--------|
| `test_graph_tools.py` | find_symbol, find_callers, find_callees |
| `test_analyze_tool.py` | complexity, code smells, dependencies |
| `test_impact_tool.py` | change impact via graph traversal |

### 3.2 Implement
- **`server.py`** — 5 new tools:
  - `find_symbol` — definition + references lookup via graph
  - `find_callers` — who calls this function (rustworkx predecessors)
  - `find_callees` — what does this call (rustworkx successors)
  - `analyze` — complexity, dependencies, code smells, quality score
  - `impact` — "what breaks if I change X?" (transitive caller graph)

### 3.3 Verification
- 80+ tests pass
- Smoke: find_callers on a real function, analyze a module

### 3.4 Post-Phase Review
- [ ] Run **code-reviewer agent** + **docs-writer agent**
- [ ] Write ADRs (graph tool API design, impact analysis algorithm)
- [ ] Snyk scan + all tests green

---

## Phase 4: Hybrid Search + Memory (4 tools)

**Goal:** Upgrade search to 3-engine hybrid. Add memory layer. Total 12 tools.

### 4.1 Tests first
| Test File | Covers |
|-----------|--------|
| `test_bm25_engine.py` | LanceDB FTS, filters |
| `test_fusion.py` | RRF algorithm, weights, dedup |
| `test_reranker.py` | FlashRank re-rank, fallback |
| `test_memory_store.py` | remember/recall/forget, TTL, tags |
| `test_token_budget.py` | 3 verbosity levels |
| `test_hybrid_search.py` | End-to-end fusion + re-rank |

### 4.2 Implement
- **`engines/bm25_engine.py`** — LanceDB native FTS (shares table with vector engine)
- **`engines/fusion.py`** — Reciprocal Rank Fusion [0.5 vector, 0.3 BM25, 0.2 graph]
- **`engines/reranker.py`** — FlashRank two-stage re-ranking
- **`memory/memory_store.py`** — LanceDB `memories` table, TTL, tags
- **`formatting/token_budget.py`** + **`response_builder.py`** — summary/detailed/full
- **`persistence/store.py`** — SQLite for graph serialization
- **Update `search`** — hybrid mode with all 3 engines
- **3 new tools:** `remember`, `recall`, `forget`
- **1 new tool:** `explain` — architecture narrative

### 4.3 Verification
- 120+ tests pass
- Hybrid search > vector-only on 10 test queries
- Memory CRUD lifecycle works

### 4.4 Post-Phase Review
- [ ] Run **code-reviewer agent** + **docs-writer agent**
- [ ] Write ADRs (RRF weights, memory TTL strategy, FlashRank integration)
- [ ] Research notes for FlashRank API
- [ ] Snyk scan + all tests green

---

## Phase 5: Hardening + Ship

**Goal:** Security, stability, packaging. Production-ready.

### 5.1 Tests
| Test File | Covers |
|-----------|--------|
| `test_security.py` | Path traversal, file limits, input validation |
| `test_e2e.py` | Full lifecycle, graceful shutdown, corrupt index recovery |
| `test_performance.py` | Warm start <5s, search <500ms, find_symbol <100ms |
| `test_memory_usage.py` | RSS stays <350MB during full workflow |

### 5.2 Implement
- SIGTERM/SIGINT graceful shutdown (stop file watcher, flush LanceDB)
- Corrupt index detection + auto-rebuild
- JSON structured logging (`NEXUS_LOG_FORMAT=json`)
- Input validation (symbol name length, path containment)
- Memory monitoring in `status` tool (RSS via `tracemalloc`)
- PyPI packaging as `nexus-mcp`
- Snyk security scan
- Final README with tool docs

### 5.3 Verification
- 140+ tests pass
- Performance benchmarks meet targets
- Memory stays <350MB
- `pip install .` from clean venv succeeds
- `nexus-mcp` CLI runs

### 5.4 Final Post-Phase Review
- [ ] Run **code-reviewer agent** (full codebase review) + **docs-writer agent** (final README)
- [ ] All ADRs up to date
- [ ] All research notes current in docs/research/INDEX.md
- [ ] Snyk security scan clean
- [ ] All 140+ tests pass, ruff clean

---

## Project Structure

```
src/nexus_mcp/
├── server.py                  # FastMCP, 12 tools, single entry point
├── config.py                  # Settings, env vars, model selection
├── state.py                   # Session state singleton
├── core/
│   ├── models.py              # Symbol, ParsedFile, Memory (from CodeGrok)
│   ├── graph_models.py        # UniversalNode, Relationship (from code-graph-mcp)
│   ├── interfaces.py          # IParser, IEngine
│   └── exceptions.py
├── parsing/
│   ├── treesitter_parser.py   # Symbol extraction → embeddings
│   ├── astgrep_parser.py      # Structural analysis → graph
│   ├── language_registry.py   # Merged language support (25+ langs)
│   ├── file_discovery.py      # os.walk + .gitignore
│   └── file_watcher.py        # Debounced watchdog (from code-graph-mcp)
├── engines/
│   ├── vector_engine.py       # LanceDB vector search
│   ├── graph_engine.py        # rustworkx PyDiGraph (from code-graph-mcp)
│   ├── bm25_engine.py         # LanceDB native FTS
│   ├── fusion.py              # Reciprocal Rank Fusion
│   └── reranker.py            # FlashRank
├── analysis/
│   └── code_analyzer.py       # Complexity, smells, deps (from code-graph-mcp)
├── memory/
│   └── memory_store.py        # LanceDB-backed semantic memory
├── indexing/
│   ├── pipeline.py            # Orchestrator: parse → chunk → embed → store
│   ├── embedding_service.py   # ONNX Runtime + jina-code/bge-small-en
│   ├── parallel_indexer.py    # ThreadPool (from CodeGrok)
│   └── chunker.py             # Symbol → CodeChunk
├── formatting/
│   ├── token_budget.py        # Token estimation + truncation
│   └── response_builder.py    # summary/detailed/full output
└── persistence/
    └── store.py               # SQLite for graph persistence
```

---

## Dependencies

| Package | Purpose | RAM Impact |
|---------|---------|-----------|
| `fastmcp>=2.0.0` | MCP server | ~20MB |
| `lancedb>=0.4.0` | Vector + FTS (mmap, disk-backed) | ~20-50MB |
| `onnxruntime>=1.16.0` | Inference (replaces torch) | ~50MB |
| `sentence-transformers>=2.2.0` | Model loading + ONNX export | Lazy load |
| `tree-sitter==0.21.3` | Symbol parsing | ~10MB |
| `tree-sitter-languages>=1.10.0` | Grammar packs | ~20MB |
| `ast-grep-py>=0.28.0` | Structural AST analysis | ~15MB |
| `rustworkx>=0.15.0` | Graph algorithms | ~5MB + graph |
| `pathspec>=0.11.0` | .gitignore matching | <1MB |
| `watchdog>=3.0.0` | File monitoring | <5MB |
| `flashrank>=0.2.0` | Re-ranking (Phase 4) | ~30MB |
| `pyarrow>=14.0.0` | LanceDB dependency | Shared |

**Eliminated:** `chromadb`, `rank-bm25`, `torch`, `einops`

---

## Memory Strategy (Target: <350MB)

| Strategy | Savings |
|----------|---------|
| ONNX Runtime instead of PyTorch | -300-500MB |
| ONNX models (jina-code/bge-small-en) instead of PyTorch | -300-450MB |
| LanceDB mmap (disk-backed) | Vectors stay on disk |
| Lazy model load (only during `index`) | Model not in RAM at idle |
| Model unload after indexing | `del model; gc.collect()` |
| Lightweight graph payloads | {id, name, type, file, line} only |
| Batch embedding (size=32) | No chunk accumulation |
| Periodic `gc.collect()` | Prevent leaks |
| `status` tool reports RSS | Monitor in production |

---

## Agentic Agents

| Agent | Trigger | Model | Purpose |
|-------|---------|-------|---------|
| `code-reviewer` | "review my changes" | sonnet | Architecture, tests, memory safety, ruff |
| `research-assistant` | "research [library]" | haiku | Context7 → docs/research/ → web |
| `test-runner` | After code changes | sonnet | pytest -v, coverage, pass/fail |
| `docs-writer` | When API changes | haiku | README, PROGRESS.md, docstrings |

### Phase integration:
- **Phase 1**: code-reviewer on scaffold, docs-writer creates README
- **Phase 2**: research-assistant for LanceDB/ONNX, test-runner validates
- **Phase 3**: code-reviewer on graph tools
- **Phase 4**: code-reviewer on hybrid search, docs-writer updates tool docs
- **Phase 5**: full code-reviewer pass, docs-writer final README

---

## MCP Integration

```bash
# Configure alongside Nexus-MCP (from MCP_Integration_Plan.md)
claude mcp add context7 -- npx -y @upstash/context7-mcp
claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
```

| MCP | Purpose |
|-----|---------|
| Context7 | Library docs (prevents hallucination) |
| Sequential Thinking | Structured reasoning for architecture |
| Notion | Already active |
| Snyk | Security scanning — already active |

---

## 12 Tools (Final)

| # | Tool | Phase | Engine |
|---|------|-------|--------|
| 1 | `index` | 2 | Pipeline → all engines |
| 2 | `search` | 2→4 | Vector → Hybrid (vector+BM25+graph+rerank) |
| 3 | `status` | 2 | All engines |
| 4 | `find_symbol` | 3 | Graph |
| 5 | `find_callers` | 3 | Graph |
| 6 | `find_callees` | 3 | Graph |
| 7 | `analyze` | 3 | Graph + code_analyzer |
| 8 | `impact` | 3 | Graph (transitive callers) |
| 9 | `explain` | 4 | Graph + Vector (synthesis) |
| 10 | `remember` | 4 | Memory (LanceDB) |
| 11 | `recall` | 4 | Memory (LanceDB) |
| 12 | `forget` | 4 | Memory (LanceDB) |
