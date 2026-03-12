# Nexus-MCP

Unified MCP server with hybrid search, code graph analysis, and semantic memory. Indexes codebases into vector + graph engines for fast, relevant code search.

## Features

- **Hybrid search** — Vector (semantic), BM25 (keyword), and graph (structural) search fused via Reciprocal Rank Fusion
- **Code graph** — Structural analysis via rustworkx (calls, imports, inheritance)
- **Dual parsing** — tree-sitter (symbols) + ast-grep (relationships), 25+ languages
- **Semantic memory** — Store and recall project knowledge with TTL-based expiration
- **Incremental indexing** — Only re-processes changed files
- **Low memory** — Target <350MB RAM (ONNX Runtime, mmap vectors, lazy model loading)
- **Fully local** — No API keys, no cloud dependencies

## Install

```bash
# Option 1: Setup script (recommended — creates venv, installs, verifies)
git clone https://github.com/shreyasjagannath/Nexus-MCP.git
cd Nexus-MCP
./setup.sh

# Option 2: Manual install
pip install -e ".[dev]"
```

See the full [Installation Guide](docs/INSTALLATION.md) for all options, MCP client integration, and troubleshooting.

## Run

```bash
nexus-mcp
```

## Add to Claude Code

```bash
claude mcp add nexus-mcp -- nexus-mcp
```

## MCP Tools (13)

### Core
| Tool | Description |
|------|-------------|
| `status` | Server status, indexing stats, memory usage |
| `health` | Readiness/liveness probe (uptime, engine availability) |
| `index` | Index a codebase (full or incremental) |
| `search` | Hybrid code search with language/type filters and reranking |

### Graph Analysis
| Tool | Description |
|------|-------------|
| `find_symbol` | Look up a symbol by name — definition, location, relationships |
| `find_callers` | Find all direct callers of a function |
| `find_callees` | Find all functions called by a given function |
| `analyze` | Code complexity, dependencies, smells, and quality metrics |
| `impact` | Transitive change impact analysis |
| `explain` | Combined graph + vector + analysis explanation of a symbol |

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
| `NEXUS_EMBEDDING_MODEL` | `bge-small-en` | Embedding model |
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

Verify your installation by running the end-to-end demo that exercises all 13 tools:

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
nexus-mcp                   # Run server
```

## Architecture

- **LanceDB** — Disk-backed vectors + native full-text search (mmap, ~20-50MB overhead)
- **ONNX Runtime** — Embedding inference (~50MB vs PyTorch ~500MB)
- **rustworkx** — Rust-backed graph engine for code structure
- **Dual parser** — tree-sitter (symbol extraction) + ast-grep (structural relationships)
- **Symbol-based chunking** — One chunk per symbol with deterministic IDs

## Self-Test / Demo

Verify your installation by running the self-test, which exercises all 13 MCP tools end-to-end:

```bash
python self_test/demo_mcp.py                  # Uses a built-in sample project
python self_test/demo_mcp.py /path/to/project  # Or point at your own codebase
```

See [self_test/README.md](self_test/README.md) for details on what is tested and troubleshooting tips.

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
