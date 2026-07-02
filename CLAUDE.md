# Nexus-MCP

Unified MCP server: hybrid search + code graph + semantic memory. Target: <350MB RAM.

## MANDATORY: Use Nexus-MCP Tools Before Built-in Tools

Nexus-MCP is registered as an MCP server (`nexus-mcp-ci`). **You MUST use Nexus-MCP tools as your PRIMARY method for understanding and navigating code.** This is a BLOCKING REQUIREMENT — do not skip it.

### Required workflow for EVERY code task

1. **Start of session**: Run `mcp__nexus-mcp__status` to check index state. If not indexed, run `mcp__nexus-mcp__index`.
2. **Before reading any file**: Use `mcp__nexus-mcp__search` to find relevant code first. Do NOT jump straight to Read/Grep/Glob.
3. **Before exploring a symbol**: Use `mcp__nexus-mcp__find_symbol`, `mcp__nexus-mcp__explain`, `mcp__nexus-mcp__find_callers`, or `mcp__nexus-mcp__find_callees` instead of grepping for it.
4. **Before refactoring**: Use `mcp__nexus-mcp__impact` to assess blast radius.
5. **For project understanding**: Use `mcp__nexus-mcp__overview` or `mcp__nexus-mcp__architecture` instead of manually browsing directories.
6. **Only use Read/Grep/Glob AFTER** Nexus-MCP tools have identified the specific files/lines you need to examine or edit.

**Why this matters**: Nexus-MCP provides semantic search, call graph analysis, and code intelligence that built-in tools cannot. Using Read/Grep first means you miss semantic matches, don't understand call relationships, and waste time manually browsing.

### Workflow: Always index first

Before using any search/graph/analysis tools, index the codebase:

```
# Single directory
mcp__nexus-mcp__index(path="/path/to/codebase")

# Multiple directories (comma-separated) — indexed folder-by-folder
mcp__nexus-mcp__index(path="/path/to/src,/path/to/lib,/path/to/tests")

# Or use the paths parameter for additional directories
mcp__nexus-mcp__index(path="/path/to/src", paths="/path/to/lib,/path/to/tests")
```

For subsequent sessions or after file changes, use incremental mode (auto-detected).

### When to use each tool

| Tool | When to use |
|------|-------------|
| `index` | **First thing** when working with a new or changed codebase. Supports comma-separated paths for multi-folder indexing (processed folder-by-folder). Re-run after significant file changes. |
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

- **NEVER use Grep/Glob/Read as your first action for code exploration**: Always start with `search`, `find_symbol`, or `overview`. Only fall back to built-in tools for reading specific files identified by Nexus-MCP, or for files outside the indexed codebase.
- **Search before reading files**: Use `search` to find relevant code instead of manually browsing. It's faster and finds semantic matches.
- **Use `impact` before refactoring**: Always check change impact before modifying shared symbols.
- **Use `explain` for unfamiliar code**: Combines graph relationships, related code, and quality metrics in one call.
- **Store decisions with `remember`**: When making architectural decisions or noting important context, store it so future sessions have access.
- **Check `recall` at session start**: Query memories for existing project context before asking the user to repeat information.
- **Use `find_callers`/`find_callees` for dependency tracing**: These are more reliable than text search for understanding call graphs.
- **Use `overview` when starting a new project**: Get a quick summary of project structure, languages, and quality before diving in.
- **Use `architecture` for design understanding**: See layers, dependencies, entry points, and hub symbols to understand system design.
- **Use `analyze` for code reviews**: Get objective quality metrics to guide review feedback.
- **Use multi-folder indexing for monorepos**: Pass comma-separated paths to `index` for projects with multiple source roots. Each folder is processed sequentially to keep RAM low.
- **Re-index incrementally after changes**: Run `index` again after making significant edits — incremental mode only processes changed files.

### Known limitations

- **The call graph only sees static edges.** `find_callers`, `find_callees`, and
  `impact` are built from tree-sitter + ast-grep parsing, not runtime tracing. They
  will **miss**: dynamic dispatch (Python monkey-patching, Ruby metaprogramming),
  calls made through callbacks/closures/lambdas (e.g. `.map(lambda x: foo(x))` shows
  no edge to `foo`), and reflection-based calls. Treat `impact` results as a lower
  bound on blast radius, not an exhaustive one — especially in highly dynamic
  languages (Python, Ruby) or callback-heavy code (JS/TS).
- **Graph fidelity varies by language.** Python, JavaScript/TypeScript, C/C++, Go,
  and Java get full call-graph edges (both tree-sitter symbols and ast-grep
  structure). Rust, Ruby, PHP, Swift, C#, Scala, Lua, and Dart get ast-grep
  structural parsing but weaker symbol-level precision. Bash gets tree-sitter
  symbols only — no call graph.
- **Auto-reindex has a detection lag, not a guarantee of freshness.** With
  `NEXUS_AUTO_WATCH` enabled (default), edits are picked up after a debounce window
  (a few seconds); `status()`/`search()` additionally run a throttled staleness
  check (see `NEXUS_STALENESS_CHECK_INTERVAL`, default 15s) as a safety net. Between
  an edit and the next debounce/throttle window, search results can reflect
  slightly stale code — this is a bounded window, not silent indefinite staleness.

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
│   ├── embedding_service.py   # Multi-model embeddings: jina-code, bge-small-en (ONNX + GPU/MPS)
│   ├── parallel_indexer.py    # ThreadPool
│   ├── pipeline.py            # discover → parse → chunk → embed → store + corrupt index detection + multi-folder
│   └── chunker.py             # Symbol → CodeChunk
├── formatting/
│   ├── token_budget.py        # Token counting/truncation
│   └── response_builder.py   # Structured responses
├── security/
│   ├── permissions.py     # READ/MUTATE/WRITE tool categories + PermissionPolicy
│   └── rate_limiter.py    # Token bucket rate limiter (per-tool, thread-safe)
├── middleware/
│   └── audit.py           # Audit logging with correlation IDs + field redaction
└── persistence/
    └── store.py               # SQLite graph persistence
self_test/
├── demo_mcp.py            # End-to-end demo exercising all 15 tools
└── README.md              # Usage, sample project, troubleshooting
```

## Commands

```bash
pip install nexus-mcp-ci   # Install from PyPI
pip install -e ".[dev]"    # Install from source with dev deps
./setup.sh                 # Setup script (venv + install + verify)
pytest -v                  # Run tests (441 tests)
pytest -m "not slow"       # Skip performance benchmarks
ruff check .               # Lint
nexus-mcp-ci               # Run server
python self_test/demo_mcp.py  # Run self-test demo (all 15 tools)
claude mcp add nexus-mcp -- nexus-mcp-ci  # Add to Claude Code
```

## Key Decisions

- LanceDB replaces ChromaDB (mmap, disk-backed vectors) — [ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md)
- ONNX Runtime replaces PyTorch (~50MB vs ~500MB) — [ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md)
- bge-small-en default; jina-code alternative — [ADR-004](docs/adr/ADR-004-bge-small-default-model.md)
- Dual parsing: tree-sitter (symbols) + ast-grep (graph) — [ADR-005](docs/adr/ADR-005-dual-parser-strategy.md)
- rustworkx for graph algorithms (Rust-backed) — [ADR-006](docs/adr/ADR-006-rustworkx-graph-engine.md)
- LanceDB schema: 12-column PyArrow, flat search — [ADR-007](docs/adr/ADR-007-lancedb-schema-design.md)
- Symbol-based chunking with deterministic IDs — [ADR-008](docs/adr/ADR-008-code-chunk-strategy.md)
- 8-step indexing pipeline with incremental reindex — [ADR-009](docs/adr/ADR-009-indexing-pipeline-architecture.md)
- Graph tools API: serialization, ambiguity, path filtering — [ADR-010](docs/adr/ADR-010-graph-tools-api-design.md)
- Hardening: shutdown, corruption recovery, validation, JSON logging — [ADR-011](docs/adr/ADR-011-hardening-decisions.md)
- Tool permission model: READ/MUTATE/WRITE categories — [ADR-012](docs/adr/ADR-012-tool-permission-model.md)
- ~~Pydantic v2 I/O schemas: internal validation, .model_dump() serialization~~ — [ADR-013](docs/adr/ADR-013-pydantic-schemas.md), **superseded by [ADR-016](docs/adr/ADR-016-remove-unused-pydantic-schemas.md)**: the schema layer was never wired into any tool at runtime and was deleted
- Token bucket rate limiting: per-tool, off by default — [ADR-014](docs/adr/ADR-014-rate-limiting.md)
- Auto-watch + throttled staleness detection: warn-and-background-reindex, mtime-diff covers branch switches — [ADR-015](docs/adr/ADR-015-auto-watch-and-staleness-detection.md)

## Gotchas

1. State is global singleton in state.py
2. Models lazy-loaded, unloaded after indexing (try/finally ensures cleanup)
3. LanceDB tables: `chunks` (vectors), `memories` (memory layer)
4. Graph engine is thread-safe with RLock
5. All tools require `index` first except `status` and `health`
6. Filter values in vector_engine are SQL-escaped to prevent injection
7. Pipeline `_pipeline` in server.py is protected by a threading lock, held for the full duration of `index()` (not just creation) so background reindexes can safely detect "busy" via a non-blocking acquire
8. Input validation runs at tool entry (null bytes, length limits, path traversal)
9. Graceful shutdown persists graph state on SIGTERM/SIGINT
10. Corrupt indexes are auto-detected and rebuilt on incremental_index
11. Permission default is `full` (backward compat); set `NEXUS_PERMISSION_LEVEL=read` for restricted
12. Audit logging is on by default; set `NEXUS_AUDIT_ENABLED=false` to disable
13. Rate limiting is off by default (stdio); enable via `NEXUS_RATE_LIMIT_ENABLED=true`
14. `trust_remote_code` defaults to `true` (required only for the jina-code model; the default bge-small-en does not need it); set `NEXUS_TRUST_REMOTE_CODE=false` to disable
15. `schemas/` was removed (dead code, never wired into any tool); FastMCP tool signatures + inline `_validate_*` helpers in server.py are the only validation
16. New exceptions (AuthenticationError, AuthorizationError, RateLimitError) in exceptions.py
17. Only registered embedding models are supported; custom model names raise ConfigurationError
18. GPU/MPS auto-detected; set `NEXUS_EMBEDDING_DEVICE=cpu` to force CPU
19. Multi-folder indexing processes each folder sequentially; `state.codebase_paths` tracks all roots, `state.codebase_path` is the first root for backward compat
20. `NEXUS_AUTO_WATCH` (default `true`) starts a debounced file watcher per indexed root after `index()` completes; on change it triggers a background incremental reindex (skipped, not queued, if one is already in flight)
21. `status()`/`search()` run a throttled staleness check (`NEXUS_STALENESS_CHECK_INTERVAL`, default 15s) and surface `stale`/`staleness_warning`/`warning` fields; a stale result still returns immediately and kicks a background reindex rather than blocking
22. `multi_index()` is now incremental-aware: it only does a full rebuild the first time or when the set of indexed roots changes; otherwise it mtime-diffs like `incremental_index()`

## Embedding Models

| Model | Key | Dims | Seq Len | Backend | trust_remote_code | Notes |
|-------|-----|------|---------|---------|-------------------|-------|
| BGE Small EN v1.5 | `bge-small-en` | 384 | 512 | PyTorch | No | **Default**. Lightweight general-purpose |
| Jina Embeddings v2 Code | `jina-code` | 768 | 8192 | ONNX | Yes | Code-specific, 161M params |

### Changing the embedding model

Set the `NEXUS_EMBEDDING_MODEL` environment variable:

```bash
# Use jina-code (code-specific, requires trust_remote_code)
NEXUS_EMBEDDING_MODEL=jina-code nexus-mcp-ci

# Or set in your shell profile
export NEXUS_EMBEDDING_MODEL=jina-code

# For Claude Code MCP config
claude mcp add nexus-mcp-ci -e NEXUS_EMBEDDING_MODEL=jina-code -- nexus-mcp-ci
```

### GPU / MPS acceleration

Device is auto-detected by default (`NEXUS_EMBEDDING_DEVICE=auto`):
- **CUDA**: Detected via `torch.cuda.is_available()` or onnxruntime CUDAExecutionProvider
- **MPS** (Apple Silicon): Detected via `torch.backends.mps` or onnxruntime CoreMLExecutionProvider
- **CPU**: Fallback

For CUDA GPU support, install the gpu extra: `pip install nexus-mcp-ci[gpu]`

To force a specific device: `NEXUS_EMBEDDING_DEVICE=cpu` or `NEXUS_EMBEDDING_DEVICE=cuda`

**Important**: After changing the embedding model, you must re-index your codebase — embeddings from different models are not compatible.
