# Usage Guide

## Getting Started

### Installation

```bash
# Install from PyPI
pip install nexus-mcp-ci

# Optional: with FlashRank reranker for better search quality
pip install nexus-mcp-ci[reranker]

# Optional: with GPU (CUDA) support
pip install nexus-mcp-ci[gpu]
```

Or install from source for development:

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP
pip install -e ".[dev]"
```

### Running the Server

```bash
nexus-mcp-ci
```

The server starts on stdio (the default MCP transport). Configure your MCP client to connect to `nexus-mcp-ci`. The `nexus-mcp` command is an alias for the same entry point.

### MCP Client Configuration

**Claude Code:**

```bash
claude mcp add nexus-mcp -- nexus-mcp-ci
```

**Claude Desktop** (add to `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "command": "nexus-mcp-ci",
      "args": []
    }
  }
}
```

**Other MCP clients** (Cursor, Windsurf, Cline, etc.):

```json
{
  "nexus-mcp": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

See the full [Installation Guide](INSTALLATION.md) for client-specific instructions.

## Tool Reference

Nexus-MCP exposes 10 tools. The v2.0.0 release merged the old 15 tools; see the [CHANGELOG](../CHANGELOG.md) for the old-to-new mapping and [ADR-017](adr/ADR-017-tool-consolidation.md) for the reason.

### Discovery & Indexing

#### `index`
Index a codebase directory. Call it before any other tool except `status` and `health`.

```
# Single directory
index(path="/path/to/your/project")

# Multiple directories (comma-separated)
index(path="/path/to/project/src,/path/to/project/lib,/path/to/project/tests")

# Multiple directories (using paths parameter)
index(path="/path/to/project/src", paths="/path/to/project/lib,/path/to/project/tests")
```

Parameters:
- `path` — Absolute path to the codebase directory. Accepts comma-separated paths for multi-folder indexing.
- `paths` — (Optional) Additional comma-separated paths to index alongside `path`.

When you give multiple paths, the server indexes each folder in sequence (discover → parse → embed → store) and merges the results into shared engines. This keeps peak RAM low. The server removes duplicate files across overlapping paths.

Returns indexing statistics: file count, symbol count, chunk count, graph size and timing. Later calls run an incremental reindex (only changed files). The tool reports progress while it runs. When it finishes, a file watcher keeps the index fresh (`NEXUS_AUTO_WATCH`, default on).

#### `status`
Check whether a codebase is indexed, index size, engine availability and memory usage.

```
status()
```

Returns version, indexing state, chunk counts, graph stats, peak RSS memory and a `hint` field. If files changed since the last index, the result includes `stale` and `staleness_warning`, and the server starts a background reindex. The staleness check runs at most once every `NEXUS_STALENESS_CHECK_INTERVAL` seconds (default 15).

#### `health`
Liveness and readiness probe: uptime and which engines are up. Use `status` to check index freshness.

```
health()
```

#### `map`
Project overview: files, languages, modules and structure. Use it before you list directories or open files one by one.

```
map(detail="summary")        # files, languages, symbol counts, quality, top modules
map(detail="architecture")   # layers, dependencies, classes, entry points, hub symbols
map(detail="full")           # both
```

`map` replaces the old `overview()` and `architecture()` tools.

### Search

#### `search`
Finds code by meaning or keyword: semantic + keyword + graph search, with code snippets.

```
search(query="authentication middleware", limit=10, language="python", mode="hybrid")
```

Parameters:
- `query` — Natural language or code query
- `limit` — Max results (1-100, default 10)
- `language` — Filter by language (e.g., "python", "javascript")
- `symbol_type` — Filter by type (e.g., "function", "class")
- `mode` — "hybrid" (default), "vector", or "bm25"
- `rerank` — Enable FlashRank reranking (default True)
- `live_grep` — Force the live-grep fallback (`rg`, then `grep`) (default False)
- `detail` — "compact" (default) or "full"

Returns results with `filepath` (relative), `code_snippet`, `symbol_name`, `line_start`/`line_end`, and a `hint` field. With `detail="compact"` each of the top 3 results carries its whole symbol, up to 2000 characters (cut at a line boundary and marked `(truncated)` when longer), so the agent can answer without a file read. The other results get about 240 characters. The `# path:line` header line, the fields that repeat the snippet (`signature`, `docstring`), the internal fields (`id`, `score`, `rrf_score`, `_fusion_sources`, `absolute_path`) and empty values are left out. Measured on a 460-file project, a compact result is about 7,000 characters, 40% of a full one.

Results in test files come after results in source files, unless the query itself asks for tests (a word such as "test", "fixture" or "mock", or a name such as `test_login`). No result is removed. On a 45,000-chunk index of django, where 70% of the chunks are tests, this raised hit@1 from 6 of 12 to 8 of 12 task queries. Hybrid mode fuses vector and keyword results; the graph list is off by default (`NEXUS_FUSION_WEIGHT_GRAPH=0`). With `detail="full"` the snippet is up to 2000 characters and those fields are present. Raw embedding vectors are always stripped. The tool falls back to live grep on its own when hybrid results are sparse. If the index looks stale, the result carries a non-null `warning`. A background reindex starts, and the results still return at once.

### Graph Analysis

#### `find_symbol`
Finds a symbol definition by name. Returns file path, line numbers, docstring, type and the graph relationships of the symbol.

```
find_symbol(symbol_name="UserService", exact=True)
```

Use `exact=False` for fuzzy substring matching.

#### `graph`
Trace the call graph of a symbol. This tool replaces `find_callers`, `find_callees` and `impact`.

```
graph(symbol_name="authenticate", direction="callers")        # who calls this
graph(symbol_name="process_request", direction="callees")     # what this calls
graph(symbol_name="DatabaseConnection", direction="callers",
      transitive=True, max_depth=5)                            # change blast radius
```

Parameters:
- `symbol_name` — Name of the function or symbol
- `direction` — "callers" (default) or "callees"
- `transitive` — `True` returns the full transitive change impact. It works only with `direction="callers"`. Run it before you refactor a shared symbol.
- `max_depth` — Maximum traversal depth for `transitive=True` (default 10)
- `detail` — "compact" (default): name, type, file and lines for each symbol. "full": adds docstring, complexity, signature types and the node id.

With `transitive=True` and `detail="compact"`, `impacted_symbols` lists at most 40 symbols and sets `truncated: true` when there are more. A compact result is about 20% of the size of a full one. `total_impacted` is the true count, and `impacted_files` always names every impacted symbol.

A callers result (immediate or transitive) also has `references`: every line where the name appears as a whole word, from a live text search of the project.

```
"references": {"total_files": 3, "total_lines": 6, "truncated": false,
               "files": {"src/agents/budget.py": [12], "src/agents/runtime.py": [4, 88]}}
```

The caller list holds only the calls that static analysis resolved, so it is a lower bound. `references` is the complete text answer, with source files before test files, so a separate grep is not needed. It lists at most 40 files and 8 lines per file; `truncated` says when the caps cut it, and the totals always count everything. A name with no graph node (a constant, or a language without graph support) returns its `references` with an empty caller list instead of an error.

> **Limit:** call edges are static and name-based. Dynamic dispatch, callbacks and reflection are invisible, and a name shared by several definitions gets no edge. Treat results as a lower bound and use `search` for other call sites. See [Known Limitations](../README.md#known-limitations).

#### `explain`
Preferred over Read for understanding a symbol. Combines graph relationships, semantic search results and code metrics.

```
explain(symbol_name="Router", verbosity="detailed")
```

Verbosity levels: "summary" (concise), "detailed" (default), "full" (everything).

#### `analyze`
Run code analysis on the indexed codebase: complexity metrics, dependency analysis, code smells and quality scores.

```
analyze(path="src/auth/")
```

The optional `path` parameter filters analysis to a subdirectory or file.

### Memory

#### `memory`
Persist and retrieve project context across sessions. This tool replaces `remember`, `recall` and `forget`.

```
# Store
memory(action="store",
       content="The auth service uses JWT tokens with 24h expiry",
       memory_type="decision", tags="auth,jwt", ttl="permanent")

# Search
memory(action="search", query="how does authentication work?", limit=5, tags="auth")

# Delete
memory(action="delete", tags="temporary")
memory(action="delete", memory_type="session")
memory(action="delete", memory_id="abc-123")
```

Parameters:
- `action` — "store", "search" or "delete"
- `content` — Text to store (`action="store"`)
- `query` — Natural language query (`action="search"`)
- `memory_id`, `memory_type`, `tags` — Filters for search and delete. `memory_type` and `tags` also set the type and tags when you store.
- `ttl` — "permanent" (default), "month", "week", "day" or "session"
- `project` — Project name for scoping (`action="store"`, default "default")
- `limit` — Maximum results (`action="search"`, default 5)

Memory types: note (default), decision, conversation, status, preference, doc.

With `NEXUS_PERMISSION_LEVEL=read`, `memory` allows `action="search"` only. `store` and `delete` need the `full` level.

## Configuration

Set via environment variables before starting the server:

```bash
# Embedding model selection
export NEXUS_EMBEDDING_MODEL=bge-small-en       # Default: bge-small-en (384d, lightweight)
                                                 # jina-code (768d) is deprecated
export NEXUS_EMBEDDING_DEVICE=auto               # auto (CUDA > MPS > CPU), cuda, mps, cpu

# Search tuning
export NEXUS_SEARCH_MODE=hybrid          # hybrid, vector, or bm25
export NEXUS_FUSION_WEIGHT_VECTOR=0.5    # Vector weight in RRF
export NEXUS_FUSION_WEIGHT_BM25=0.3      # BM25 weight in RRF
export NEXUS_FUSION_WEIGHT_GRAPH=0       # Graph weight in RRF. 0 (default) = graph list off
export NEXUS_WARM_START=true             # Load the index and the model while the server starts

# Resource limits
export NEXUS_MAX_FILE_SIZE_MB=10         # Skip files larger than this
export NEXUS_MAX_MEMORY_MB=350           # Memory budget target

# Logging
export NEXUS_LOG_LEVEL=INFO              # DEBUG, INFO, WARNING, ERROR
export NEXUS_LOG_FORMAT=json             # text or json (json for production)

# Storage
export NEXUS_STORAGE_DIR=.nexus          # Where indexes are stored

# Freshness
export NEXUS_AUTO_WATCH=true             # Debounced file watcher starts after index()
export NEXUS_STALENESS_CHECK_INTERVAL=15 # Seconds between status()/search() staleness checks

# Security and observability
export NEXUS_PERMISSION_LEVEL=full       # full or read (read allows query-only tools)
export NEXUS_AUDIT_ENABLED=true          # Audit log with correlation IDs
export NEXUS_RATE_LIMIT_ENABLED=false    # Per-tool token-bucket rate limiting
```

See the [README configuration table](../README.md#configuration) for every variable.

### Embedding Models

Nexus-MCP recommends one embedding model, `bge-small-en`. `jina-code` still loads, with a warning, so an existing index keeps working; it is deprecated. Only registered model names are accepted; custom model names raise a `ConfigurationError`.

| Model | HuggingFace ID | Dims | Code-specific? | Notes |
|-------|---------------|------|:-:|---|
| `bge-small-en` (default) | `BAAI/bge-small-en-v1.5` | 384 | No | Smallest download (~50MB), general text, PyTorch backend |
| `jina-code` (deprecated) | `jinaai/jina-embeddings-v2-base-code` | 768 | Yes | ONNX, needs `trust_remote_code` and a 612 MiB download; slow CPU index. Removal in a future major release |

GPU/MPS auto-detection (`NEXUS_EMBEDDING_DEVICE=auto`) tries CUDA first, then Apple MPS, then falls back to CPU. For explicit GPU support, install with `pip install -e ".[gpu]"`.

## Best Practices

### Indexing

1. **Index from the project root** — Point `index` at the top-level directory, not a subdirectory. This ensures .gitignore is respected and relative paths are meaningful.

2. **Use multi-folder indexing for monorepos** — For projects with multiple source roots (e.g., `src/`, `lib/`, `plugins/`), pass them as comma-separated paths in a single `index` call. Each folder is processed sequentially to keep RAM usage low.

3. **Let incremental reindex handle changes** — After the first full index, subsequent `index` calls only process changed files. No need to clear and re-index.

4. **Check status after indexing** — Use `status` to verify chunk counts and graph stats look reasonable.

### Searching

1. **Start with hybrid mode** — The default `hybrid` mode combines all three engines for the best results. Only switch to `vector` or `bm25` if you have a specific reason.

2. **Use filters to narrow results** — The `language` and `symbol_type` filters are applied before search, making results more relevant and queries faster.

3. **Adjust verbosity for context** — When using `explain`, start with "summary" for quick overviews and "detailed" for investigation.

### Memory

1. **Tag everything** — Tags make recall much more effective. Use consistent tag conventions (e.g., "auth", "api", "bug").

2. **Use TTL for ephemeral context** — Set `ttl="session"` or `ttl="day"` for temporary context that shouldn't persist.

3. **Use memory types semantically** — "decision" for architectural choices, "note" for observations, "status" for current state.

### Performance

1. **Monitor memory** — Check `status()` memory stats periodically. Peak RSS should stay under 350MB for typical codebases.

2. **Large codebases** — For codebases >10K files, consider increasing `NEXUS_MAX_WORKERS` for faster parallel parsing, or increasing `NEXUS_EMBEDDING_BATCH_SIZE` for faster embedding.

3. **Search latency** — If search is slow, try `mode="vector"` (skips BM25 and graph) or reduce the `limit`.
