# Nexus-MCP

Local MCP server that cuts coding-agent tokens: the agent reads less code to find the right file. Hybrid search + code graph, with an opt-in memory. Memory (measured, docs/MEMORY.md): ~90MB idle, ~120MB after a restore, ~270MB at `search` (model loaded), ~310MB while indexing a small project. The default model meets the old <350MB target (3.0.0, ONNX on the CPU).

## North star

Lower token use for an agent, with the same accuracy. Set by the owner on 2026-10-08.

- **Measure:** the tokens and the cost of a task in the live benchmark (`benchmarks/`),
  against the Grep and Read condition.
- **Gate:** accuracy must not fall. Check hit@1 on the four retrieval suites
  (`python -m evals.retrieval.run`) and the answer score of the benchmark.
- Keep a change only when it lowers the measure and passes the gate. Record the two
  numbers, before and after, in `CHANGELOG.md`.

## Use Nexus-MCP Tools Before Built-in Tools

Nexus-MCP is registered as an MCP server (`nexus-mcp`) with 8 tools: `index`,
`status`, `search`, `find_symbol`, `graph`, `analyze`, `map`,
`memory`. Prefer these over Read/Grep/Glob for exploring an indexed codebase — start
with `status`, index if needed, then `search`/`find_symbol`/`graph` before falling
back to built-in tools for files nexus-mcp has already identified.

**Full workflow guidance, the tool reference table, and best practices live in the
`nexus-mcp` skill**, not here — that content changes per-task and belongs in
on-demand skill context, not an always-loaded block in this file. Install it via:

```
/plugin marketplace add jaggernaut007/Nexus-MCP
/plugin install nexus-mcp@nexus-mcp
```

(Requires `pip install nexus-mcp-ci` first — the plugin registers the MCP server, it
doesn't install the Python package.) See `plugin/skills/nexus-mcp/SKILL.md` in this
repo to read the skill content directly.

### Known limitations

- **Call edges are static and name-based.**
  - `graph()` and the `callers`/`callees` fields of `find_symbol()` come from parsing, not runtime tracing.
  - Edges exist for Python, JavaScript, TypeScript, Go, Java, Rust, C, C++, C# and PHP. Python has the best coverage. ADR-019 gives the other languages.
  - A call gets an edge only when exactly one callee fits: in the same file, in an imported file, or with a unique name in the index.
  - A name shared by several definitions gets no edge. Treat results as a lower bound.
  - `analyze` complexity is cyclomatic and approximate. Only Python counts `and`/`or`, and only Python functions carry docstrings.
- **The call graph only sees static edges.** `graph()` is built from
  parsing, not runtime tracing. It will **miss**: dynamic dispatch (Python monkey-patching, Ruby
  metaprogramming), calls made through callbacks/closures/lambdas (e.g.
  `.map(lambda x: foo(x))` shows no edge to `foo`), and reflection-based calls. Treat
  `graph(transitive=True)` results as a lower bound on blast radius, not an exhaustive
  one — especially in highly dynamic languages (Python, Ruby) or callback-heavy code
  (JS/TS).
- **Graph coverage varies by language.** ast-grep structure (functions, classes,
  imports) exists for Python, JavaScript, TypeScript, Go, Java, Rust, C, C++, C# and PHP only
  (`FUNCTION_KINDS`, `CLASS_KINDS`, `_extract_imports` in `astgrep_parser.py`).
  Bash and Kotlin get tree-sitter symbols and embeddings but no graph nodes.
- **Auto-reindex has a detection lag, not a guarantee of freshness.** With
  `NEXUS_AUTO_WATCH` enabled (default), edits are picked up after a debounce window
  (a few seconds); `status()`/`search()` additionally run a throttled staleness
  check (see `NEXUS_STALENESS_CHECK_INTERVAL`, default 15s) as a safety net. Between
  an edit and the next debounce/throttle window, search results can reflect
  slightly stale code — this is a bounded window, not silent indefinite staleness.

## Git Workflow
Global default applies (see `AGENTS.md` -> Git Workflow):
1. Call `EnterWorktree` before the first edit (branches off `main`).
2. Commit on that branch.
3. Run `~/.claude/scripts/ship.sh` (suggested gate: `SHIP_CHECK="ruff check . && pytest -v"`).
4. Call `ExitWorktree`; the branch stays for history.

## Structure (all implemented)

```
src/nexus_mcp/
├── server.py              # FastMCP server: 8 thin tool wrappers (guard, audit) + graceful shutdown
├── core_api.py            # Transport-agnostic tool logic, input validation, _pipeline + lock
├── config.py              # Settings with NEXUS_ env prefix (security, audit, rate limit, auto-watch)
├── setup_clients.py       # `nexus-mcp-ci setup`: add the server and the skill to an MCP client
├── links.py               # Link registry (user-level file, atomic write), ADR-027
├── linked.py              # Attach linked repo stores read-only, skip reasons, staleness
├── state.py               # Session state singleton (+ `linked` list) + shutdown()
├── lifecycle.py           # Idle timeout, orphan exit, signals; finish() always ends the process
├── core/
│   ├── models.py          # Symbol, ParsedFile, CodebaseIndex, Memory
│   ├── graph_models.py    # UniversalNode, Relationship
│   ├── interfaces.py      # IParser, IEngine
│   └── exceptions.py      # NexusException hierarchy + Auth/RateLimit errors
├── parsing/
│   ├── treesitter_parser.py   # Symbol extraction → embeddings
│   ├── astgrep_parser.py      # Structural analysis → graph
│   ├── http_extractor.py      # Routes and HTTP client calls → node metadata (ADR-027)
│   ├── language_registry.py   # Merged language support (25+ langs)
│   └── file_watcher.py        # Debounced watchdog
├── engines/
│   ├── graph_engine.py    # rustworkx PyDiGraph
│   ├── live_grep.py       # rg/grep fallback
│   ├── vector_engine.py   # LanceDB vector search (IEngine) + validate()
│   ├── bm25_engine.py     # LanceDB native FTS
│   ├── fusion.py          # Reciprocal Rank Fusion of vector + BM25; exact names first
├── analysis/
│   └── code_analyzer.py   # Complexity, smells, deps
├── memory/
│   └── memory_store.py    # LanceDB-backed memory with TTL
├── indexing/
│   ├── embedding_service.py   # Embeddings: OnnxEncoder (int8 bge-small-en, CPU only); TorchEncoder (extra `torch`, GPU when present)
│   ├── parallel_indexer.py    # ThreadPool
│   ├── call_resolver.py       # CALLS edges from callee names, after the last parse batch (ADR-019)
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
│   └── store.py               # SQLite graph persistence
└── evaluate/                  # `nexus-mcp-ci eval` (quick + agent tier), `data`, `share` (ADR-020, ADR-021)
benchmarks/                # Token-efficiency benchmark harness (ADR-018)
evals/                     # routing/ (does the agent pick the right tool), retrieval/ (hit@1); docs/EVALS.md
self_test/
├── demo_mcp.py            # End-to-end demo exercising the 8 tools and the Python API
└── README.md              # Usage, sample project, troubleshooting
llms.txt, llms-full.txt    # AI-readable docs; rebuild llms-full.txt with scripts/build_llms_full.py
.claude-plugin/
└── marketplace.json       # /plugin marketplace add jaggernaut007/Nexus-MCP
plugin/
├── .claude-plugin/plugin.json   # Plugin metadata
├── .mcp.json                    # Registers the nexus-mcp-ci MCP server
└── skills/nexus-mcp/SKILL.md    # Tool routing guidance (loads on demand)
```

## Commands

```bash
pip install nexus-mcp-ci   # Install from PyPI
pip install -e ".[dev]"    # Install from source with dev deps
./setup.sh                 # Setup script (venv + install + verify)
pytest -v                  # Run the tests
pytest -m "not slow"       # Skip performance benchmarks
ruff check .               # Lint
nexus-mcp-ci               # Run server
python self_test/demo_mcp.py  # Run self-test demo (the 8 tools and the Python API)
nexus-mcp-ci eval --example   # Adopter eval on the Nexus-MCP source (free, offline)
python scripts/publish_results.py  # Regenerate every published benchmark number
claude mcp add nexus-mcp -- nexus-mcp-ci  # Add to Claude Code
```

## Key Decisions

- LanceDB replaces ChromaDB (mmap, disk-backed vectors) — [ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md)
- ONNX Runtime replaces PyTorch — [ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md); made true for the default model by [ADR-026](docs/adr/ADR-026-cpu-only-onnx-embeddings.md)
- bge-small-en default — [ADR-004](docs/adr/ADR-004-bge-small-default-model.md); int8 ONNX on the CPU, PyTorch optional for a GPU, jina-code removed in 3.0.0 — [ADR-026](docs/adr/ADR-026-cpu-only-onnx-embeddings.md)
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
- Tool consolidation 15→10 (`graph`/`map`/`memory`), action-aware permission categories — [ADR-017](docs/adr/ADR-017-tool-consolidation.md)
- Token-efficiency benchmark harness (`benchmarks/`) — [ADR-018](docs/adr/ADR-018-token-efficiency-benchmark.md)
- Call-edge resolution: static, name-based, precision first, rebuilt after each index — [ADR-019](docs/adr/ADR-019-call-edge-resolution.md)
- Adopter evaluation in the package (`eval`), ground truth from the parser — [ADR-020](docs/adr/ADR-020-adopter-evaluation.md)
- One source for published numbers; opt-in sharing, no telemetry — [ADR-021](docs/adr/ADR-021-results-publishing-and-sharing.md)
- Server lifecycle: idle timeout (3 h), orphan exit, `os._exit` after a bounded cleanup — [ADR-022](docs/adr/ADR-022-server-lifecycle-and-exit.md)
- Linked repos: one index per repo, a user-level registry, service calls as the connection (draft, phases 2.1 to 2.3) — [ADR-027](docs/adr/ADR-027-linked-repos.md)
- MIT licence, commercial licence key removed again — [ADR-023](docs/adr/ADR-023-commercial-licence-check.md) (superseded by [ADR-024](docs/adr/ADR-024-mit-licence.md))

## Gotchas

1. State is global singleton in state.py. A new process attaches the stored index through
   `core_api.restore_session()` (`NEXUS_AUTO_RESTORE`, default `true`; tests set it `false`).
   `server.main()` also runs `core_api.warm_up()` in a background thread (`NEXUS_WARM_START`,
   default `true`): it attaches the index and loads the embedding model before the first call
2. Models lazy-loaded, unloaded after indexing (try/finally ensures cleanup)
3. LanceDB tables: `chunks` (vectors), `memories` (memory layer)
4. Graph engine is thread-safe with RLock
5. All tools require `index` first except `status`
6. Filter values in vector_engine are SQL-escaped to prevent injection
7. Pipeline `_pipeline` in core_api.py is protected by `_pipeline_lock`, a threading lock, held for the full duration of `index()` (not just creation) so background reindexes can safely detect "busy" via a non-blocking acquire
8. Input validation runs at tool entry in `core_api.py` (null bytes, length limits, path traversal)
9. Graceful shutdown persists graph state on SIGTERM/SIGINT/SIGHUP, stdin close, idle
   (`NEXUS_IDLE_TIMEOUT`, default 3 h) and orphan exit. `lifecycle.finish()` then calls
   `os._exit`, so `server.main()` never returns and `atexit` handlers do not run. Tests
   replace `lifecycle._hard_exit` in `conftest.py`
10. Corrupt indexes are auto-detected and rebuilt on incremental_index
11. Permission default is `full` (backward compat); set `NEXUS_PERMISSION_LEVEL=read` for restricted
12. Audit logging is on by default; set `NEXUS_AUDIT_ENABLED=false` to disable
13. Rate limiting is off by default (stdio); enable via `NEXUS_RATE_LIMIT_ENABLED=true`
14. No model needs `trust_remote_code`: the setting and `NEXUS_TRUST_REMOTE_CODE` were removed with jina-code in 3.0.0 (an old variable is ignored)
15. `schemas/` was removed (dead code, never wired into any tool); FastMCP tool signatures + the `validate_*` helpers in core_api.py are the only validation
16. New exceptions (AuthenticationError, AuthorizationError, RateLimitError) in exceptions.py
17. Only registered embedding models are supported; custom model names raise ConfigurationError (`jina-code` gives a removal message)
18. The ONNX model (`bge-small-en`) runs on the CPU only. The PyTorch model (`bge-small-en-torch`) uses a GPU when present: `NEXUS_EMBEDDING_DEVICE` defaults to `auto` (CUDA, then MPS, then CPU) and applies to the PyTorch model only. A GPU value for the ONNX model is ignored with a warning; a GPU that is not present falls back to the CPU with a warning. The model name is the identity of the index: `bge-small-en` and `bge-small-en-torch` differ a little, so a switch rebuilds the index. `ONNX_MAX_BATCH` (1) caps one ONNX call: a batch of 32 peaked above 1.3 GB, and the int8 model quantizes with one scale for each batch, so a chunk's vector changed with its batch. Do not raise it without re-reading ADR-026
19. Multi-folder indexing processes each folder sequentially; `state.codebase_paths` tracks all roots, `state.codebase_path` is the first root for backward compat
20. `NEXUS_AUTO_WATCH` (default `true`) starts a debounced file watcher per indexed root after `index()` completes; on change it triggers a background incremental reindex (skipped, not queued, if one is already in flight)
21. `status()`/`search()` run a throttled staleness check (`NEXUS_STALENESS_CHECK_INTERVAL`, default 15s) and surface `stale`/`staleness_warning`/`warning` fields; a stale result still returns immediately and kicks a background reindex rather than blocking
22. `multi_index()` is now incremental-aware: it only does a full rebuild the first time or when the set of indexed roots changes; otherwise it mtime-diffs like `incremental_index()`
23. Linked repos (ADR-027): `nexus-mcp-ci link <path>` writes `~/.nexus-mcp/links.json` (`NEXUS_LINKS_FILE`), keyed by the root of the project that you run it in. The server attaches each linked store read-only into `state.linked` (restore and `index`), never writes to it, and trusts nothing in it: roots and `repo_id` must match the registry. A bad link is skipped with a `skip_reason` that `status` shows. A linked store is always `<repo>/.nexus`; `NEXUS_STORAGE_DIR` does not apply to it. `search` and `find_symbol` also look in each attached linked repo (phase 2.2): the query is embedded once, results show `<name>/<path>`, and the linked BM25 engine is read-only (`IndexingPipeline(read_only=True)`), so a search never writes to a linked store. `graph` does not cross repos yet

## Embedding Models

| Model | Key | Dims | Seq Len | Backend | Notes |
|-------|-----|------|---------|---------|-------|
| BGE Small EN v1.5, int8 | `bge-small-en` | 384 | 512 | ONNX Runtime, CPU | **Default**. 32 MB file from `Xenova/bge-small-en-v1.5`, pinned commit |
| BGE Small EN v1.5, fp32 | `bge-small-en-torch` | 384 | 512 | PyTorch, GPU when present (CUDA, MPS), else CPU | Optional: `pip install 'nexus-mcp-ci[torch]'`. About 2 times faster than the default on Apple Silicon (MPS) |

`jina-code` was removed in 3.0.0 (ADR-026).

### Changing the embedding model

Set the `NEXUS_EMBEDDING_MODEL` environment variable:

```bash
# The default is bge-small-en. Another registered model is chosen like this:
NEXUS_EMBEDDING_MODEL=<model> nexus-mcp-ci

# For Claude Code MCP config
claude mcp add nexus-mcp -e NEXUS_EMBEDDING_MODEL=<model> -- nexus-mcp-ci
```

### ONNX on the CPU, and the optional PyTorch GPU model

The default model runs on the CPU. There is no GPU path for ONNX. To use a GPU (CUDA, or Metal
on Apple Silicon), install PyTorch. It is a large install:

```bash
pip install 'nexus-mcp-ci[torch]'
NEXUS_EMBEDDING_MODEL=bge-small-en-torch nexus-mcp-ci
```

**Important**: After changing the embedding model, you must re-index your codebase — embeddings from different models are not compatible.
