# Nexus-MCP

[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/card.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

**The only MCP server with hybrid search + code graph + semantic memory — fully local.**

Nexus-MCP is a unified, local-first code intelligence server built for the [Model Context Protocol](https://modelcontextprotocol.io). It combines vector search, BM25 keyword search, and structural graph analysis into a single process — giving AI agents precise, token-efficient code understanding without cloud dependencies.

---

## Why Nexus-MCP?

AI coding agents waste tokens. A lot of them. Every time an agent reads full files to find a function, grep-searches for keywords that miss semantic intent, or makes multiple tool calls across disconnected servers — tokens burn. Nexus-MCP fixes this.

### Token Efficiency: The Numbers

| Scenario | Without Nexus | With Nexus | Savings |
|----------|:---:|:---:|:---:|
| **Find relevant code** (agent reads 5-10 files manually) | 5,000–15,000 tokens | 500–2,000 tokens (summary mode) | **70–90%** |
| **Understand a symbol** (grep + read file + read callers) | 3,000–8,000 tokens across 3-5 tool calls | 800–2,000 tokens in 1 `explain` call | **60–75%** |
| **Assess change impact** (manual trace through codebase) | 10,000–20,000 tokens | 1,000–3,000 tokens via `impact` tool | **80–85%** |
| **Tool descriptions in context** (2 separate MCP servers) | ~1,700 tokens (17 tools) | ~1,000 tokens (15 consolidated) | **40%** |
| **Search precision** (keyword-only misses, needs retries) | 2–3 searches × 2,000 tokens | 1 hybrid search × 1,500 tokens | **60–75%** |

**Estimated savings per coding session:** 15,000–40,000 tokens (30–60% reduction) compared to standalone agentic file browsing.

### Three Verbosity Levels

Every tool respects a token budget — agents request only the detail they need:

| Level | Budget | What's Returned | Use Case |
|-------|:---:|---|---|
| `summary` | ~500 tokens | Counts, scores, file:line pointers | Quick lookups, triage |
| `detailed` | ~2,000 tokens | Signatures, types, line ranges, docstrings | Normal development |
| `full` | ~8,000 tokens | Full code snippets, relationships, metadata | Deep analysis |

### vs. Standalone Agentic Development (No Code MCP)

Without a code intelligence server, AI agents must:
- **Read entire files** to find one function (~500–2,000 tokens/file, often 5–10 files per query)
- **Grep for keywords** that miss semantic intent ("auth" won't find "verify_credentials")
- **Manually trace call chains** by reading file after file
- **Lose all context between sessions** — no persistent memory

Nexus-MCP replaces this with targeted retrieval: semantic search returns the exact chunks needed, graph queries trace relationships instantly, and memory persists across sessions.

### vs. Competitor MCP Servers

| Feature | Nexus-MCP | Sourcegraph MCP | Greptile MCP | GitHub MCP | tree-sitter MCP |
|---------|:---:|:---:|:---:|:---:|:---:|
| **Local / private** | Yes | No (infra required) | No (cloud) | No (cloud) | Yes |
| **Semantic search** | Yes (embeddings) | No (keyword) | Yes (LLM-based) | No (keyword) | No |
| **Keyword search** | Yes (BM25) | Yes | N/A | Yes | No |
| **Hybrid fusion** | Yes (RRF) | No | No | No | No |
| **Code graph** | Yes (rustworkx) | Yes (SCIP) | No | No | No |
| **Re-ranking** | Yes (FlashRank) | No | N/A | No | No |
| **Semantic memory** | Yes (6 types) | No | No | No | No |
| **Change impact** | Yes | Partial | No | No | No |
| **Token budgeting** | Yes (3 levels) | No | No | No | No |
| **Languages** | 25+ | 30+ | Many | Many | Many |
| **Cost** | Free | $$$ | $40/mo | $10–39/mo | Free |
| **API keys needed** | No | Yes | Yes | Yes | No |

### vs. AI Code Tools (Cursor, Copilot, Cody, etc.)

| Capability | Nexus-MCP | Cursor | Copilot @workspace | Sourcegraph Cody | Continue.dev | Aider |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **IDE-agnostic** | Yes | No | No | No | No | Yes |
| **MCP-native** | Yes | Partial | No | No | Yes (client) | No |
| **Fully local** | Yes | Partial | No | Partial | Yes | Yes |
| **Hybrid search** | Yes | Unknown | Unknown | Keyword | Yes | No |
| **Code graph** | Yes | Unknown | Unknown | Yes (SCIP) | Basic | No |
| **Semantic memory** | Yes (persistent) | No | No | No | No | No |
| **Token-budgeted responses** | Yes | N/A | N/A | N/A | N/A | N/A |
| **Open source** | Yes (MIT) | No | No | Partial | Yes | Yes |
| **Cost** | Free | $20–40/mo | $10–39/mo | $0–49/mo | Free | Free |

**Nexus-MCP's unique combination:** No other tool delivers hybrid search + code graph + semantic memory + token budgeting + full privacy in a single MCP server.

---

## Key Features

- **Hybrid search** — Vector (semantic) + BM25 (keyword) + graph (structural) fused via Reciprocal Rank Fusion, then re-ranked with FlashRank
- **Code graph** — Structural analysis via rustworkx: callers, callees, imports, inheritance, change impact
- **Dual parsing** — tree-sitter (symbol extraction) + ast-grep (structural relationships), 25+ languages
- **Semantic memory** — Persistent knowledge store with TTL expiration, 6 memory types, semantic recall
- **Explain & Impact** — "What does this do?" and "What breaks if I change it?" in single tool calls
- **Token-budgeted responses** — Three verbosity levels (summary/detailed/full) keep context windows lean
- **Multi-folder indexing** — Index multiple directories in one call, processed folder-by-folder with shared engines
- **Incremental indexing** — Only re-processes changed files; file watcher support
- **Multi-model embeddings** — 2 models (jina-code default, bge-small-en), GPU/MPS auto-detection
- **Low memory** — <350MB RAM target (ONNX Runtime ~50MB, mmap vectors, lazy model loading)
- **Fully local** — Zero cloud dependencies, no API keys, all processing on your machine
- **15 tools, one server** — Consolidates what previously required 2 MCP servers (17 tools) into one

## Prerequisites

- **Python 3.10 to 3.13**
- **pip** (comes with Python)

## Install

### Option 1: pip install from PyPI (recommended)

```bash
pip install nexus-mcp-ci
```

With optional extras:

```bash
# With GPU (CUDA) support
pip install nexus-mcp-ci[gpu]

# With FlashRank reranker for better search quality
pip install nexus-mcp-ci[reranker]

# Both
pip install nexus-mcp-ci[gpu,reranker]
```

### Option 2: From source (for development)

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP

# Setup script (creates venv, installs, verifies)
./setup.sh

# Or manual install with dev deps
pip install -e ".[dev]"
```

> **Note:** The default embedding model (`jina-code`) requires ONNX Runtime. This is included automatically. If you see errors about missing ONNX/Optimum, run:
> ```bash
> pip install "sentence-transformers[onnx]" "optimum[onnxruntime]>=1.19.0"
> ```
> To use a lighter model that doesn't need `trust_remote_code`, set `NEXUS_EMBEDDING_MODEL=bge-small-en`.

See the full [Installation Guide](docs/INSTALLATION.md) for all options, MCP client integration, and troubleshooting.

## Run

```bash
nexus-mcp
```

The server starts on stdio (the default MCP transport). Point your MCP client at the `nexus-mcp` command.

## Add to Your MCP Client

### Claude Code

```bash
# Basic setup
claude mcp add nexus-mcp-ci -- nexus-mcp-ci

# With a specific embedding model
claude mcp add nexus-mcp-ci -e NEXUS_EMBEDDING_MODEL=bge-small-en -- nexus-mcp-ci
```

> **Tip:** If you installed in a virtual environment, use the full path so the MCP client finds the right Python:
> ```bash
> claude mcp add nexus-mcp-ci -- /path/to/Nexus-MCP/.venv/bin/nexus-mcp-ci
> ```

### Claude Desktop

Add to your config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "nexus-mcp-ci": {
      "command": "nexus-mcp-ci",
      "args": []
    }
  }
}
```

### Cursor / Windsurf / Cline / Other MCP Clients

Add to your MCP client's server config:

```json
{
  "nexus-mcp-ci": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

See the full [Installation Guide](docs/INSTALLATION.md) for client-specific instructions.

## MCP Tools (15)

### Core
| Tool | Description |
|------|-------------|
| `status` | Server status, indexing stats, memory usage, next-tool hints |
| `health` | Readiness/liveness probe (uptime, engine availability) |
| `index` | Index a codebase (full, incremental, or multi-folder) |
| `search` | **Preferred over Grep/Glob.** Semantic search returning code snippets, absolute paths, and scores |

### Graph Analysis
| Tool | Description |
|------|-------------|
| `find_symbol` | **Preferred over Grep** for definitions — returns location, types, and call relationships |
| `find_callers` | Find all direct callers via call graph (more accurate than text search) |
| `find_callees` | Trace execution flow — all functions called by a given function |
| `analyze` | Code complexity, dependencies, smells, and quality metrics |
| `impact` | **Use before refactoring.** Transitive change impact analysis |
| `explain` | **Preferred over Read** for understanding symbols — graph + vector + analysis |
| `overview` | **Preferred over Glob/ls.** Project overview: files, languages, symbols, quality |
| `architecture` | **Preferred over manual browsing.** Layers, dependencies, entry points, hubs |

### Memory
| Tool | Description |
|------|-------------|
| `remember` | Store a semantic memory with tags and TTL |
| `recall` | Search memories by semantic similarity |
| `forget` | Delete memories by ID, tags, or type |

## Configuration

All settings can be overridden via `NEXUS_` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_STORAGE_DIR` | `.nexus` | Storage directory for indexes |
| `NEXUS_EMBEDDING_MODEL` | `jina-code` | Embedding model (`jina-code`, `bge-small-en`) |
| `NEXUS_EMBEDDING_DEVICE` | `auto` | Device for embeddings: `auto` (CUDA > MPS > CPU), `cuda`, `mps`, `cpu` |
| `NEXUS_MAX_FILE_SIZE_MB` | `10` | Skip files larger than this |
| `NEXUS_CHUNK_MAX_CHARS` | `4000` | Max code snippet size per chunk |
| `NEXUS_MAX_MEMORY_MB` | `350` | Memory budget |
| `NEXUS_SEARCH_MODE` | `hybrid` | Search mode: `hybrid`, `vector`, or `bm25` |
| `NEXUS_FUSION_WEIGHT_VECTOR` | `0.5` | Vector engine weight in RRF |
| `NEXUS_FUSION_WEIGHT_BM25` | `0.3` | BM25 engine weight in RRF |
| `NEXUS_FUSION_WEIGHT_GRAPH` | `0.2` | Graph engine weight in RRF |
| `NEXUS_LOG_LEVEL` | `INFO` | Logging level |
| `NEXUS_LOG_FORMAT` | `text` | Log format: `text` or `json` |

## Self-Test Demo

Verify your installation by running the end-to-end demo that exercises all 15 tools:

```bash
python self_test/demo_mcp.py                  # Uses built-in sample project
python self_test/demo_mcp.py /path/to/project  # Or test against your own codebase
```

See [self_test/README.md](self_test/README.md) for details.

## Development

```bash
pip install -e ".[dev]"     # Install with dev deps
pytest -v                   # Run tests (441 tests)
pytest -m "not slow"        # Skip performance benchmarks
ruff check .                # Lint
nexus-mcp-ci                # Run server
```

## How It Works

```
search("how does auth work")
  |
  |-- vector_engine.search(query, n=30)    -- semantic similarity (embeddings)
  |-- bm25_engine.search(query, n=30)      -- keyword matching (exact terms)
  |-- graph_engine.boost(query, n=30)      -- structural relevance (callers/callees)
  |                                            |
  |              Reciprocal Rank Fusion (weights: 0.5 / 0.3 / 0.2)
  |                                            |
  |                        FlashRank re-ranking (top 20)
  |                                            |
  |                      Token budget truncation (summary/detailed/full)
  |                                            |
  v
  Top-N results, formatted to verbosity level
```

## Architecture

| Component | Technology | Why |
|-----------|-----------|-----|
| **Vector store** | LanceDB | Disk-backed, mmap, ~20-50MB overhead, native FTS |
| **Embeddings** | ONNX Runtime + jina-code (default) | ~50MB vs PyTorch ~500MB, GPU/MPS auto-detection, 3 models supported |
| **Graph engine** | rustworkx | Rust-backed, O(1) node/edge lookup, PageRank, centrality |
| **Symbol parser** | tree-sitter | 25+ languages, AST-level symbol extraction |
| **Graph parser** | ast-grep | Structural pattern matching for calls/imports/inheritance |
| **Chunking** | Symbol-based | One chunk per function/class, deterministic IDs |
| **Re-ranker** | FlashRank (optional) | 4MB ONNX model, <10ms for top-20 |
| **Persistence** | SQLite + LanceDB | Graph in SQLite, vectors in Lance, zero-config |

## Documentation

- [Installation Guide](docs/INSTALLATION.md) — Prerequisites, install steps, MCP client integration, troubleshooting
- [Architecture](docs/ARCHITECTURE.md) — System design, data flow, components, memory budget
- [Usage Guide](docs/USAGE_GUIDE.md) — Tool reference, configuration, best practices
- [Developer Guide](docs/DEVELOPER_GUIDE.md) — Setup, testing, contributing, adding tools/engines
- [ADRs](docs/adr/) — 11 Architecture Decision Records
- [Research Notes](docs/RESEARCH.md) — Deep dives on libraries and technology choices

## Acknowledgments

Nexus-MCP consolidates and extends two earlier projects:

- **[CodeGrok MCP](https://github.com/shreyasjagannath/CodeGrok_mcp)** by [rdondeti](https://github.com/rdondeti) (Ravitez Dondeti) — Semantic code search with tree-sitter parsing, embedding service, parallel indexing, and memory retrieval. Core models, symbol extraction, and the embedding pipeline were ported from CodeGrok. Originally licensed under MIT.
- **[code-graph-mcp](https://github.com/entrepeneur4lyf/code-graph-mcp)** by [entrepeneur4lyf](https://github.com/entrepeneur4lyf) — Code graph analysis with ast-grep structural parsing, rustworkx graph engine, and complexity analysis. Graph models, relationship extraction, and code analysis were ported from code-graph-mcp.

Individual source files retain "Ported from" attribution in their module docstrings. See [ADR-001](docs/adr/ADR-001-single-mcp-consolidation.md) for the rationale behind the consolidation.

## License

MIT — see [LICENSE](LICENSE) for details.
