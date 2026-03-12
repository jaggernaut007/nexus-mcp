# Nexus-MCP

Unified MCP server: hybrid search + code graph + semantic memory. Target: <350MB RAM.

## Using Nexus-MCP Tools

Nexus-MCP is registered as an MCP server (`nexus-mcp`). **Use its 15 tools actively** to understand, navigate, and work with codebases more effectively.

### Workflow: Always index first

Before using any search/graph/analysis tools, index the codebase:

```
mcp__nexus-mcp__index(path="/path/to/codebase")
```

For subsequent sessions or after file changes, use incremental mode (auto-detected).

### When to use each tool

| Tool | When to use |
|------|-------------|
| `index` | **First thing** when working with a new or changed codebase. Re-run after significant file changes. |
| `status` | Check if a codebase is indexed, how many symbols/chunks exist, memory usage. |
| `health` | Verify the server and all engines are running before starting work. |
| `search` | **Primary tool** for finding relevant code. Use for any "where is...", "how does...", "find..." query. Supports `mode=hybrid` (default), `vector`, or `bm25`. Use language/type filters to narrow results. |
| `find_symbol` | Look up a specific symbol by name. Use `exact=False` for fuzzy matching when unsure of the name. |
| `find_callers` | Understand who calls a function — use before refactoring to assess blast radius. |
| `find_callees` | Understand what a function depends on — use to trace execution flow. |
| `analyze` | Get code quality metrics: complexity, code smells, dependencies, maintainability. Use when reviewing or improving code. |
| `impact` | **Before making changes**: assess transitive impact of modifying a symbol. Shows all affected symbols and files. |
| `explain` | Get a combined graph + vector + analysis explanation of any symbol. Use for onboarding to unfamiliar code. |
| `overview` | Get a high-level project overview: file counts, languages, symbol types, directory structure, quality metrics, top modules. Use when starting work on an unfamiliar project. |
| `architecture` | Document the project architecture: layers, module dependencies, class hierarchies, entry points, hub symbols, complexity hotspots. Use for understanding system design. |
| `remember` | Store project context, decisions, or notes as semantic memories with tags. Use to persist context across conversations. |
| `recall` | Retrieve stored memories by semantic similarity. Check for existing context before starting new work. |
| `forget` | Clean up outdated memories by ID, tags, or type. |

### Best practices

- **Search before reading files**: Use `search` to find relevant code instead of manually browsing. It's faster and finds semantic matches.
- **Use `impact` before refactoring**: Always check change impact before modifying shared symbols.
- **Use `explain` for unfamiliar code**: Combines graph relationships, related code, and quality metrics in one call.
- **Store decisions with `remember`**: When making architectural decisions or noting important context, store it so future sessions have access.
- **Check `recall` at session start**: Query memories for existing project context before asking the user to repeat information.
- **Use `find_callers`/`find_callees` for dependency tracing**: These are more reliable than text search for understanding call graphs.
- **Use `overview` when starting a new project**: Get a quick summary of project structure, languages, and quality before diving in.
- **Use `architecture` for design understanding**: See layers, dependencies, entry points, and hub symbols to understand system design.
- **Use `analyze` for code reviews**: Get objective quality metrics to guide review feedback.
- **Re-index incrementally after changes**: Run `index` again after making significant edits — incremental mode only processes changed files.

## Structure (all implemented)

```
src/nexus_mcp/
├── server.py              # FastMCP server, 15 tools + health + input validation + graceful shutdown
├── config.py              # Settings with NEXUS_ env prefix (security, audit, rate limit)
├── state.py               # Session state singleton + shutdown()
├── core/
│   ├── models.py          # Symbol, ParsedFile, CodebaseIndex, Memory
│   ├── graph_models.py    # UniversalNode, Relationship
│   ├── interfaces.py      # IParser, IEngine
│   └── exceptions.py      # NexusException hierarchy + Auth/RateLimit errors
├── parsing/
│   ├── treesitter_parser.py   # Symbol extraction → embeddings
│   ├── astgrep_parser.py      # Structural analysis → graph
│   ├── language_registry.py   # Merged language support (25+ langs)
│   └── file_watcher.py        # Debounced watchdog
├── engines/
│   ├── graph_engine.py    # rustworkx PyDiGraph
│   ├── vector_engine.py   # LanceDB vector search (IEngine) + validate()
│   ├── bm25_engine.py     # LanceDB native FTS
│   ├── fusion.py          # Reciprocal Rank Fusion
│   └── reranker.py        # FlashRank (optional)
├── analysis/
│   └── code_analyzer.py   # Complexity, smells, deps
├── memory/
│   └── memory_store.py    # LanceDB-backed memory with TTL
├── indexing/
│   ├── embedding_service.py   # ONNX Runtime + bge-small (trust_remote_code guard)
│   ├── parallel_indexer.py    # ThreadPool
│   ├── pipeline.py            # discover → parse → chunk → embed → store + corrupt index detection
│   └── chunker.py             # Symbol → CodeChunk
├── formatting/
│   ├── token_budget.py        # Token counting/truncation
│   └── response_builder.py   # Structured responses
├── security/
│   ├── permissions.py     # READ/MUTATE/WRITE tool categories + PermissionPolicy
│   └── rate_limiter.py    # Token bucket rate limiter (per-tool, thread-safe)
├── middleware/
│   └── audit.py           # Audit logging with correlation IDs + field redaction
├── schemas/
│   ├── inputs.py          # Pydantic v2 input validation models
│   └── responses.py       # Pydantic v2 response serialization models
└── persistence/
    └── store.py               # SQLite graph persistence
self_test/
├── demo_mcp.py            # End-to-end demo exercising all 15 tools
└── README.md              # Usage, sample project, troubleshooting
```

## Commands

```bash
./setup.sh                 # Setup script (venv + install + verify)
pip install -e ".[dev]"    # Manual install with dev deps
pytest -v                  # Run tests (441 tests)
pytest -m "not slow"       # Skip performance benchmarks
ruff check .               # Lint
nexus-mcp                  # Run server
python self_test/demo_mcp.py  # Run self-test demo (all 15 tools)
claude mcp add nexus-mcp -- nexus-mcp  # Add to Claude Code
```

## Key Decisions

- LanceDB replaces ChromaDB (mmap, disk-backed vectors) — [ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md)
- ONNX Runtime replaces PyTorch (~50MB vs ~500MB) — [ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md)
- bge-small-en default; CodeRankEmbed opt-in — [ADR-004](docs/adr/ADR-004-bge-small-default-model.md)
- Dual parsing: tree-sitter (symbols) + ast-grep (graph) — [ADR-005](docs/adr/ADR-005-dual-parser-strategy.md)
- rustworkx for graph algorithms (Rust-backed) — [ADR-006](docs/adr/ADR-006-rustworkx-graph-engine.md)
- LanceDB schema: 12-column PyArrow, flat search — [ADR-007](docs/adr/ADR-007-lancedb-schema-design.md)
- Symbol-based chunking with deterministic IDs — [ADR-008](docs/adr/ADR-008-code-chunk-strategy.md)
- 8-step indexing pipeline with incremental reindex — [ADR-009](docs/adr/ADR-009-indexing-pipeline-architecture.md)
- Graph tools API: serialization, ambiguity, path filtering — [ADR-010](docs/adr/ADR-010-graph-tools-api-design.md)
- Hardening: shutdown, corruption recovery, validation, JSON logging — [ADR-011](docs/adr/ADR-011-hardening-decisions.md)
- Tool permission model: READ/MUTATE/WRITE categories — [ADR-012](docs/adr/ADR-012-tool-permission-model.md)
- Pydantic v2 I/O schemas: internal validation, .model_dump() serialization — [ADR-013](docs/adr/ADR-013-pydantic-schemas.md)
- Token bucket rate limiting: per-tool, off by default — [ADR-014](docs/adr/ADR-014-rate-limiting.md)

## Gotchas

1. State is global singleton in state.py
2. Models lazy-loaded, unloaded after indexing (try/finally ensures cleanup)
3. LanceDB tables: `chunks` (vectors), `memories` (memory layer)
4. Graph engine is thread-safe with RLock
5. All tools require `index` first except `status` and `health`
6. Filter values in vector_engine are SQL-escaped to prevent injection
7. Pipeline `_pipeline` in server.py is protected by a threading lock
8. Input validation runs at tool entry (null bytes, length limits, path traversal)
9. Graceful shutdown persists graph state on SIGTERM/SIGINT
10. Corrupt indexes are auto-detected and rebuilt on incremental_index
11. Permission default is `full` (backward compat); set `NEXUS_PERMISSION_LEVEL=read` for restricted
12. Audit logging is on by default; set `NEXUS_AUDIT_ENABLED=false` to disable
13. Rate limiting is off by default (stdio); enable via `NEXUS_RATE_LIMIT_ENABLED=true`
14. `trust_remote_code` defaults to `false`; embedding_service respects this setting
15. Pydantic schemas are internal only; FastMCP tool signatures use simple params
16. New exceptions (AuthenticationError, AuthorizationError, RateLimitError) in exceptions.py
