# Nexus-MCP

[![PyPI version](https://img.shields.io/pypi/v/nexus-mcp-ci)](https://pypi.org/project/nexus-mcp-ci/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/nexus-mcp-ci/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-441-green)](tests/)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

**Hybrid search + code graph + semantic memory in a single local MCP server — under 350 MB RAM.**

Nexus-MCP is a code intelligence server for the [Model Context Protocol](https://modelcontextprotocol.io). It gives AI agents precise, token-efficient answers about your codebase without cloud dependencies: no API keys, no data egress, no subscriptions.

```
pip install nexus-mcp-ci
claude mcp add nexus-mcp-ci -- nexus-mcp-ci
```

---

## The Problem It Solves

AI coding agents are token-inefficient by default. An agent trying to understand `verify_credentials()` typically:

1. `Glob("src/**/*.py")` → 120 files returned, agent reads the most likely 8 → **~12,000 tokens**
2. `Grep("verify_credentials")` → 3 matches, agent reads surrounding context → **~4,000 tokens**
3. `Read("auth/middleware.py")` → full 400-line file to understand callers → **~3,000 tokens**

**Total: ~19,000 tokens, 3+ tool calls, no graph relationships.**

With Nexus-MCP:

1. `explain("verify_credentials")` → symbol definition + all callers + all callees + complexity metrics → **~1,500 tokens, 1 tool call**

Or for discovery:

1. `search("credential verification flow")` → top-10 semantically relevant chunks across the codebase → **~2,000 tokens, 1 tool call**

**Estimated savings: 30–60% token reduction per coding session.** The exact numbers depend on codebase size and task type — see the [benchmarks table](#token-efficiency) below.

---

## Quickstart (60 seconds)

```bash
# 1. Install
pip install nexus-mcp-ci

# 2. Register with Claude Code
claude mcp add nexus-mcp-ci -- nexus-mcp-ci

# 3. Verify (in any Claude Code session)
# Claude will automatically use nexus-mcp-ci tools when CLAUDE.md instructs it
```

Then drop a `CLAUDE.md` in your project root:

```markdown
## Code Navigation

Use nexus-mcp-ci tools before built-in file tools:
- Start sessions with `mcp__nexus-mcp__status`; run `index` if needed
- `search` before `Read/Grep`
- `explain` instead of reading a file to understand a symbol
- `impact` before any refactor
```

That's it. Claude will index your project on first use and use Nexus-MCP tools automatically.

---

## How It Works

### Indexing Pipeline (8 steps)

```
Source files
    │
    ├─ Step 1: Discover ──────── walk tree, filter by ext/size/.gitignore
    │
    ├─ Step 2: Parse symbols ─── tree-sitter (parallel ThreadPool)
    │           extracts: functions, classes, methods
    │           captures: name, signature, docstring, line_start/end, language
    │
    ├─ Step 3: Parse graph ────── ast-grep (sequential for consistency)
    │           extracts: call edges, import edges, inheritance edges
    │           output: UniversalGraph(nodes=[], edges=[])
    │
    ├─ Step 4: Transfer graph ── populate rustworkx PyDiGraph
    │           O(1) node lookup by name, Rust-backed traversal
    │
    ├─ Step 5: Chunk ──────────── Symbol → CodeChunk
    │           deterministic IDs: SHA256(file_path + symbol_name + line)
    │           avoids duplicate inserts on incremental reindex
    │
    ├─ Step 6: Embed ──────────── bge-small-en: 384-dim (default) or jina-code: 768-dim via ONNX
    │           lazy-loaded, unloaded after indexing (try/finally)
    │           GPU/MPS auto-detected; falls back to CPU
    │
    ├─ Step 7: Store ──────────── write to LanceDB `chunks` table (12-col PyArrow schema)
    │           rebuild native FTS (Tantivy) index after write
    │
    └─ Step 8: Cleanup ────────── unload model, persist metadata (mtimes for incremental)
                                  save rustworkx graph to SQLite (warm-start recovery)
```

**Incremental reindex:** mtime-based — only changed files are re-processed. Corrupt index detection triggers automatic full rebuild.

### Search Pipeline

```
search("how does auth work")
         │
         ├─► vector_engine.search(query, n=30)  ← cosine similarity on 768-dim embeddings
         │                                         "auth" finds "verify_credentials", "token_check"
         │
         ├─► bm25_engine.search(query, n=30)    ← Tantivy FTS on same LanceDB table
         │                                         fast exact-keyword matching
         │
         ├─► graph_engine.boost(query, n=30)    ← structural relevance score
         │                                         hub symbols (high in/out degree) boosted
         │
         └─► fusion.merge(v_results, b_results, g_results)
                  │
                  │  Reciprocal Rank Fusion: score = Σ weight_i / (k + rank_i)
                  │  default weights: vector=0.5, bm25=0.3, graph=0.2
                  │
                  ├─► reranker.rerank(top_20)   ← FlashRank (optional, 4MB ONNX model, <10ms)
                  │
                  └─► token_budget.truncate()   ← summary / detailed / full
                           │
                           └─► Top-N chunks, scored, formatted
```

### Technology Stack

| Layer | Technology | Decision Rationale |
|-------|-----------|-------------------|
| **Vector store** | LanceDB | mmap disk-backed → ~20–50 MB overhead vs ChromaDB's in-memory model. Native Tantivy FTS means one store for both vector and BM25. ([ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md)) |
| **Embeddings** | bge-small-en (default) or ONNX Runtime + jina-code | bge-small-en is lightweight (384-dim, no trust_remote_code). jina-code is code-specific (161M params, 8192 seq len) on ONNX (~50 MB vs PyTorch ~500 MB). Lazy-load/unload keeps RAM flat after indexing. ([ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md)) |
| **Graph engine** | rustworkx PyDiGraph | Rust-backed, O(1) node lookup, PageRank + centrality algorithms. Thread-safe with RLock. ([ADR-006](docs/adr/ADR-006-rustworkx-graph-engine.md)) |
| **Symbol parser** | tree-sitter 0.21.3 | 25+ languages, incremental parsing, AST-level symbol extraction with metadata. Parallel via ThreadPool. ([ADR-005](docs/adr/ADR-005-dual-parser-strategy.md)) |
| **Graph parser** | ast-grep | Structural pattern matching for call/import/inheritance edges. Sequential run for graph consistency. ([ADR-005](docs/adr/ADR-005-dual-parser-strategy.md)) |
| **Chunking** | Symbol-based | One chunk per function/class. Deterministic SHA256 IDs prevent duplicate inserts. ([ADR-008](docs/adr/ADR-008-code-chunk-strategy.md)) |
| **Re-ranker** | FlashRank (optional) | 4 MB ONNX cross-encoder, <10 ms on CPU for top-20. Graceful passthrough if not installed. |
| **Persistence** | SQLite + LanceDB | Graph in SQLite (warm-start recovery), vectors+FTS in LanceDB, mtimes in JSON. Zero-config. |
| **MCP framework** | FastMCP 2.0 | Stdio transport, automatic tool registration, schema generation. |

---

## Token Efficiency

Measured against equivalent agentic file-browsing workflows on a ~10,000-line Python codebase:

| Task | Without Nexus-MCP | With Nexus-MCP | Reduction |
|------|:-----------------:|:--------------:|:---------:|
| Find relevant code (agent reads 5–10 files) | 5,000–15,000 tokens | 500–2,000 tokens | **70–90%** |
| Understand a symbol (grep + read + trace callers) | 3,000–8,000 tokens, 3–5 calls | 800–2,000 tokens, 1 call | **60–75%** |
| Assess change impact (manual transitive trace) | 10,000–20,000 tokens | 1,000–3,000 tokens | **80–85%** |
| Tool descriptions in context (2 MCP servers) | ~1,700 tokens (17 tools) | ~1,000 tokens (15 tools) | **40%** |
| Search precision (keyword-only needs retries) | 2–3 searches × 2,000 tokens | 1 hybrid search × 1,500 tokens | **60–75%** |

**Typical session savings: 15,000–40,000 tokens (30–60%)** compared to file-browsing agents.

### Three Verbosity Levels

Every tool respects a `verbosity` parameter — agents request exactly the detail they need:

| Level | Token Budget | What's Included |
|-------|:-----------:|-----------------|
| `summary` | ~500 tokens | Counts, scores, file:line pointers only |
| `detailed` | ~2,000 tokens | Signatures, types, line ranges, docstrings |
| `full` | ~8,000 tokens | Full code snippets, all relationships, metadata |

---

## The 15 Tools

### Discovery & Indexing

| Tool | Use When |
|------|----------|
| `index(path)` | First action in any session. Supports comma-separated multi-folder paths. Incremental by default. |
| `status()` | Check index health: symbol count, chunk count, memory usage, engine availability. |
| `health()` | Liveness probe — uptime, which engines are ready. |
| `overview()` | **Replaces `ls` + manual browsing.** File counts, languages, top modules, quality metrics. |
| `architecture()` | System design view: layers, module dependencies, entry points, hub symbols, hotspots. |

### Search

| Tool | Use When |
|------|----------|
| `search(query, mode, language, type, n)` | Primary code discovery. `mode`: `hybrid` (default), `vector`, or `bm25`. |

### Graph Analysis

| Tool | Use When |
|------|----------|
| `find_symbol(name, exact)` | Look up a specific symbol. `exact=False` for fuzzy matching. |
| `find_callers(symbol)` | Who calls this function? Call-graph accurate, not text-search. |
| `find_callees(symbol)` | What does this function call? Trace execution dependencies. |
| `explain(symbol)` | **Replaces `Read` for understanding code.** Graph relationships + semantic context + quality metrics in one call. |
| `analyze(path)` | Code quality: cyclomatic complexity, cognitive complexity, code smells, dependency metrics. |
| `impact(symbol)` | **Run before any refactor.** Transitive change blast radius across the full graph. |

### Memory

| Tool | Use When |
|------|----------|
| `remember(content, tags, type, ttl)` | Persist a decision, note, or context across sessions. Types: `note`, `decision`, `conversation`, `status`, `preference`, `doc`. TTL: `permanent`, `month`, `week`, `day`, `session`. |
| `recall(query, tags, type)` | Retrieve memories by semantic similarity + optional tag/type filters. |
| `forget(id, tags, type)` | Remove stale memories by ID, tag, or type. |

---

## Install

### From PyPI (recommended)

```bash
pip install nexus-mcp-ci

# GPU (CUDA) support — adds ONNX CUDA execution provider
pip install nexus-mcp-ci[gpu]

# FlashRank reranker — adds ~4MB cross-encoder for better search quality
pip install nexus-mcp-ci[reranker]

# Both
pip install nexus-mcp-ci[gpu,reranker]
```

### From Source

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP
./setup.sh           # creates venv, installs, verifies
# or
pip install -e ".[dev]"
```

**Python 3.10–3.13 required.** Optional: `rg` (ripgrep) for 100% search coverage fallback on unindexed files.

> The optional `jina-code` model requires ONNX Runtime. If you see ONNX/Optimum errors:
> ```bash
> pip install "sentence-transformers[onnx]" "optimum[onnxruntime]>=1.19.0"
> ```
> The default `bge-small-en` model needs neither ONNX nor `trust_remote_code`.

---

## MCP Client Setup

### Claude Code

```bash
# Minimal
claude mcp add nexus-mcp-ci -- nexus-mcp-ci

# With the code-specific embedding model (requires trust_remote_code)
claude mcp add nexus-mcp-ci -e NEXUS_EMBEDDING_MODEL=jina-code -- nexus-mcp-ci

# GPU embeddings
claude mcp add nexus-mcp-ci -e NEXUS_EMBEDDING_DEVICE=cuda -- nexus-mcp-ci

# Virtualenv install — pass the full binary path
claude mcp add nexus-mcp-ci -- /path/to/.venv/bin/nexus-mcp-ci
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "nexus-mcp-ci": {
      "command": "nexus-mcp-ci",
      "args": [],
      "env": {
        "NEXUS_EMBEDDING_MODEL": "jina-code"
      }
    }
  }
}
```

### Cursor / Windsurf / Cline / Any MCP Client

```json
{
  "nexus-mcp-ci": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

---

## Agent Integration Patterns

### CLAUDE.md boilerplate (drop into project root)

```markdown
## Code Intelligence — nexus-mcp-ci

Every code task in this project MUST follow this workflow:

1. **Session start**: `mcp__nexus-mcp__status` → if not indexed, `mcp__nexus-mcp__index`
2. **Before any file read**: `mcp__nexus-mcp__search` to locate relevant code
3. **To understand a symbol**: `mcp__nexus-mcp__explain` (not Read)
4. **Before refactoring**: `mcp__nexus-mcp__impact` to assess blast radius
5. **For project orientation**: `mcp__nexus-mcp__overview` or `mcp__nexus-mcp__architecture`
```

### Typical agent tool-call sequence

```
# Session start
status()               → "indexed: True, 8,412 chunks, 1,203 symbols, 87 MB"

# Code discovery
search("JWT token validation", mode="hybrid", n=10)
  → auth/jwt.py:42  validate_token()         score=0.94
  → auth/middleware.py:18  require_auth()    score=0.87
  → tests/test_auth.py:91  test_valid_jwt()  score=0.81

# Deep symbol understanding
explain("validate_token")
  → definition, docstring, params, complexity
  → callers: [require_auth, login_required, api_key_check]
  → callees: [decode_jwt, check_expiry, verify_signature]
  → quality: complexity=6, smells=[], maintainability=A

# Pre-refactor safety check
impact("validate_token")
  → direct callers: 3 symbols
  → transitive impact: 12 symbols across 4 files
  → high-risk: auth/middleware.py (5 dependents)
```

### Multi-folder monorepo indexing

```python
# Index multiple roots in one call — processed sequentially, shared engines
index(path="packages/api/src,packages/shared/src,packages/cli/src")

# Or use the paths parameter for additional roots
index(path="packages/api/src", paths="packages/shared/src,packages/cli/src")
```

---

## Configuration

All settings via `NEXUS_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_EMBEDDING_MODEL` | `bge-small-en` | `bge-small-en` (384-dim, lightweight) or `jina-code` (768-dim, code-optimized) |
| `NEXUS_EMBEDDING_DEVICE` | `auto` | `auto` (CUDA → MPS → CPU), `cuda`, `mps`, `cpu` |
| `NEXUS_STORAGE_DIR` | `.nexus` | Index storage directory |
| `NEXUS_MAX_FILE_SIZE_MB` | `10` | Skip files larger than this |
| `NEXUS_CHUNK_MAX_CHARS` | `4000` | Max chars per code chunk |
| `NEXUS_MAX_MEMORY_MB` | `350` | Memory budget target |
| `NEXUS_SEARCH_MODE` | `hybrid` | `hybrid`, `vector`, or `bm25` |
| `NEXUS_FUSION_WEIGHT_VECTOR` | `0.5` | Vector score weight in RRF |
| `NEXUS_FUSION_WEIGHT_BM25` | `0.3` | BM25 score weight in RRF |
| `NEXUS_FUSION_WEIGHT_GRAPH` | `0.2` | Graph score weight in RRF |
| `NEXUS_PERMISSION_LEVEL` | `full` | `full`, `read`, or `restricted` |
| `NEXUS_RATE_LIMIT_ENABLED` | `false` | Enable per-tool token-bucket rate limiting |
| `NEXUS_AUDIT_ENABLED` | `true` | Structured audit logging with correlation IDs |
| `NEXUS_TRUST_REMOTE_CODE` | `true` | Required for jina-code; set `false` with bge-small-en |
| `NEXUS_LOG_LEVEL` | `INFO` | Logging level |
| `NEXUS_LOG_FORMAT` | `text` | `text` or `json` |

### Embedding Models

| Model | Key | Dims | Max Seq | Backend | `trust_remote_code` |
|-------|-----|:----:|:-------:|---------|:-------------------:|
| BGE Small EN v1.5 (default) | `bge-small-en` | 384 | 512 | PyTorch | No |
| Jina Embeddings v2 Code | `jina-code` | 768 | 8,192 | ONNX | Yes |

**After changing model, re-index.** Embeddings from different models are incompatible.

---

## Comparison

### vs. Other MCP Servers

| Feature | Nexus-MCP | Sourcegraph MCP | Greptile MCP | GitHub MCP | tree-sitter MCP |
|---------|:---:|:---:|:---:|:---:|:---:|
| Fully local / private | ✅ | ❌ infra required | ❌ cloud | ❌ cloud | ✅ |
| Semantic (vector) search | ✅ | ❌ keyword only | ✅ LLM-based | ❌ | ❌ |
| Keyword (BM25) search | ✅ | ✅ | — | ✅ | ❌ |
| Hybrid fusion (RRF) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Code graph (call/import) | ✅ rustworkx | ✅ SCIP | ❌ | ❌ | ❌ |
| Re-ranking | ✅ FlashRank | ❌ | — | ❌ | ❌ |
| Semantic memory (persistent) | ✅ 6 types | ❌ | ❌ | ❌ | ❌ |
| Change impact analysis | ✅ | partial | ❌ | ❌ | ❌ |
| Token-budgeted responses | ✅ 3 levels | ❌ | ❌ | ❌ | ❌ |
| Languages | 25+ | 30+ | many | many | many |
| Cost | **Free** | $$$ | $40/mo | $10–39/mo | Free |
| API keys required | **No** | Yes | Yes | Yes | No |

### vs. AI Code Tools

| Capability | Nexus-MCP | Cursor | Copilot @workspace | Cody | Continue.dev | Aider |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| IDE-agnostic | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| MCP-native | ✅ | partial | ❌ | ❌ | ✅ client | ❌ |
| Fully local | ✅ | partial | ❌ | partial | ✅ | ✅ |
| Hybrid search | ✅ | unknown | unknown | keyword | yes | ❌ |
| Code graph | ✅ | unknown | unknown | ✅ SCIP | basic | ❌ |
| Semantic memory | ✅ persistent | ❌ | ❌ | ❌ | ❌ | ❌ |
| Token-budgeted output | ✅ | — | — | — | — | — |
| Open source | ✅ MIT | ❌ | ❌ | partial | ✅ | ✅ |
| Cost | **Free** | $20–40/mo | $10–39/mo | $0–49/mo | Free | Free |

---

## Development

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP
pip install -e ".[dev]"

pytest -v                    # 441 tests
pytest -m "not slow"         # skip performance benchmarks
pytest tests/test_search.py  # single module
ruff check .                 # lint
```

### Project Structure

```
src/nexus_mcp/
├── server.py              # FastMCP entrypoint — 15 tools, input validation, graceful shutdown
├── config.py              # Settings (NEXUS_ env prefix)
├── state.py               # Global singleton SessionState
├── core/
│   ├── models.py          # Symbol, ParsedFile, CodebaseIndex, Memory
│   ├── graph_models.py    # UniversalNode, Relationship
│   ├── interfaces.py      # IParser, IEngine protocols
│   └── exceptions.py      # NexusException hierarchy
├── parsing/
│   ├── treesitter_parser.py   # Symbol extraction (parallel)
│   ├── astgrep_parser.py      # Structural graph extraction (sequential)
│   ├── language_registry.py   # 25+ language definitions
│   └── file_watcher.py        # Debounced watchdog for live reindex
├── engines/
│   ├── vector_engine.py   # LanceDB cosine similarity search
│   ├── bm25_engine.py     # LanceDB native FTS (Tantivy)
│   ├── graph_engine.py    # rustworkx PyDiGraph with RLock
│   ├── fusion.py          # Reciprocal Rank Fusion
│   └── reranker.py        # FlashRank (optional, graceful degradation)
├── indexing/
│   ├── pipeline.py        # 8-step indexing pipeline
│   ├── embedding_service.py   # ONNX Runtime, GPU/MPS auto-detect
│   ├── parallel_indexer.py    # ThreadPool over files
│   └── chunker.py         # Symbol → CodeChunk with deterministic IDs
├── memory/
│   └── memory_store.py    # LanceDB-backed memory, TTL, 6 types
├── analysis/
│   └── code_analyzer.py   # Cyclomatic/cognitive complexity, smells
├── security/
│   ├── permissions.py     # READ/MUTATE/WRITE tool categories
│   └── rate_limiter.py    # Token-bucket, per-tool, thread-safe
├── middleware/
│   └── audit.py           # Structured audit logs, correlation IDs, field redaction
└── schemas/
    ├── inputs.py          # Pydantic v2 input validation
    └── responses.py       # Pydantic v2 response serialization
```

### Adding a New Tool

1. Add the handler function to `server.py` decorated with `@mcp.tool()`
2. Add input validation model to `schemas/inputs.py`
3. Add permission category to `security/permissions.py`
4. Write tests in `tests/`
5. Update `self_test/demo_mcp.py` to exercise the tool

### Adding a New Language

1. Add entry to `parsing/language_registry.py` with the tree-sitter grammar
2. Add structural patterns to `parsing/astgrep_parser.py` for call/import extraction
3. Add test fixtures in `tests/fixtures/`

---

## Self-Test

Verify your installation exercises all 15 tools end-to-end:

```bash
python self_test/demo_mcp.py                   # built-in sample project
python self_test/demo_mcp.py /path/to/project  # your own codebase
```

Expected output: all 15 tools exercised with pass/fail per tool and a summary.

---

## Known Limitations

- **Sequential graph parsing**: ast-grep runs sequentially (not parallel) to keep the call graph consistent. This is the main indexing bottleneck on large codebases.
- **bge-small-en uses PyTorch**: The lightweight model uses PyTorch instead of ONNX, so it doesn't benefit from the same ~50 MB footprint as jina-code.
- **No incremental graph updates**: Graph is rebuilt in full on incremental reindex (only vector/BM25 are incremental at the chunk level).
- **No SSE transport**: Only stdio transport is currently supported.
- **Language coverage**: 25+ languages, but structural relationship extraction (callers/callees) is most accurate for Python, TypeScript, JavaScript, Go, and Rust. Other languages may have partial graph edges.

---

## Architecture Decision Records

Key decisions are documented in [docs/adr/](docs/adr/):

| ADR | Decision |
|-----|----------|
| [ADR-001](docs/adr/ADR-001-single-mcp-consolidation.md) | Merge two MCP servers into one |
| [ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md) | LanceDB over ChromaDB |
| [ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md) | ONNX Runtime over PyTorch for embeddings |
| [ADR-004](docs/adr/ADR-004-bge-small-default-model.md) | bge-small-en as default embedding model |
| [ADR-005](docs/adr/ADR-005-dual-parser-strategy.md) | Dual parser: tree-sitter + ast-grep |
| [ADR-006](docs/adr/ADR-006-rustworkx-graph-engine.md) | rustworkx for graph algorithms |
| [ADR-007](docs/adr/ADR-007-lancedb-schema-design.md) | 12-column PyArrow schema for LanceDB |
| [ADR-008](docs/adr/ADR-008-code-chunk-strategy.md) | Symbol-based chunking with deterministic IDs |
| [ADR-009](docs/adr/ADR-009-indexing-pipeline-architecture.md) | 8-step indexing pipeline |
| [ADR-010](docs/adr/ADR-010-graph-tools-api-design.md) | Graph tools API: serialization, ambiguity handling |
| [ADR-011](docs/adr/ADR-011-hardening-decisions.md) | Graceful shutdown, corruption recovery, JSON logging |
| [ADR-012](docs/adr/ADR-012-tool-permission-model.md) | READ/MUTATE/WRITE permission categories |
| [ADR-013](docs/adr/ADR-013-pydantic-schemas.md) | Pydantic v2 I/O schemas |
| [ADR-014](docs/adr/ADR-014-rate-limiting.md) | Token-bucket rate limiting (off by default) |

---

## Documentation

- [Installation Guide](docs/INSTALLATION.md) — Prerequisites, client-specific setup, troubleshooting
- [Architecture](docs/ARCHITECTURE.md) — Data flow, component design, memory budget analysis
- [Usage Guide](docs/USAGE_GUIDE.md) — Full tool reference with examples
- [Developer Guide](docs/DEVELOPER_GUIDE.md) — Contributing, adding tools/engines/languages
- [Research Notes](docs/RESEARCH.md) — Library evaluations and technology deep-dives

---

## Acknowledgments

Nexus-MCP consolidates two earlier open-source projects:

- **[CodeGrok MCP](https://github.com/shreyasjagannath/CodeGrok_mcp)** by [rdondeti](https://github.com/rdondeti) (Ravitez Dondeti, MIT) — Contributed the symbol extraction pipeline, embedding service, parallel indexer, core data models, and memory retrieval system.
- **[code-graph-mcp](https://github.com/entrepeneur4lyf/code-graph-mcp)** by [entrepeneur4lyf](https://github.com/entrepeneur4lyf) — Contributed the ast-grep structural parser, rustworkx graph engine, complexity analysis, and relationship extraction.

Source files retain "Ported from" attribution in their module docstrings. See [ADR-001](docs/adr/ADR-001-single-mcp-consolidation.md) for the consolidation rationale.

---

## License

MIT — see [LICENSE](LICENSE) for details.
