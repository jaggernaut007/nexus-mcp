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
nexus-mcp
```

The server starts on stdio (the default MCP transport). Configure your MCP client to connect to `nexus-mcp`.

### MCP Client Configuration

**Claude Code:**

```bash
claude mcp add nexus-mcp -- nexus-mcp-ci
```

**Claude Desktop** (add to `~/Library/Application Support/Claude/claude_desktop_config.json`):

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

**Other MCP clients** (Cursor, Windsurf, Cline, etc.):

```json
{
  "nexus-mcp-ci": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

See the full [Installation Guide](INSTALLATION.md) for client-specific instructions.

## Tool Reference

### Core Tools

#### `index`
Index a codebase directory. Must be called before any other tool (except `status`).

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

When multiple paths are provided, each folder is indexed sequentially (discover → parse → embed → store) and results are merged into shared engines. This keeps peak RAM low since each folder's data is freed before processing the next. Duplicate files across overlapping paths are automatically deduplicated.

Returns indexing statistics: file count, symbol count, chunk count, timing. On subsequent calls with a single path, performs incremental reindexing (only changed files).

#### `search`
Preferred over Grep/Glob for finding code. Semantic + keyword + graph search with code snippets.

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

Returns results with `filepath` (relative), `absolute_path`, `code_snippet` (truncated to 2000 chars), `score`, `symbol_name`, `line_start`/`line_end`, and a `hint` field. Raw embedding vectors are stripped from results.

#### `status`
Check server health, indexing stats, and memory usage.

```
status()
```

Returns version, indexing state, chunk counts, graph stats, peak RSS memory, and a `hint` field suggesting which tools to use next.

### Graph Analysis Tools

#### `find_symbol`
Preferred over Grep for finding symbol definitions. Returns file path, line numbers, docstring, type annotations, and all relationships (callers, callees).

```
find_symbol(name="UserService", exact=True)
```

Use `exact=False` for case-insensitive fuzzy matching.

#### `find_callers`
Find all functions that call a given symbol. More accurate than Grep — uses the call graph, so no false positives from comments or strings.

```
find_callers(symbol_name="authenticate")
```

#### `find_callees`
Trace execution flow — find all functions called by a given function. More reliable than reading source and manually tracing imports.

```
find_callees(symbol_name="process_request")
```

#### `analyze`
Run code analysis on the indexed codebase: complexity metrics, dependency analysis, code smells, and quality scores.

```
analyze(path="src/auth/")
```

The optional `path` parameter filters analysis to a subdirectory.

#### `impact`
Use before refactoring. Transitive change impact analysis — shows all functions affected if a given symbol changes.

```
impact(symbol_name="DatabaseConnection", max_depth=5)
```

#### `explain`
Preferred over Read for understanding code symbols. Combines graph analysis, vector search, and code metrics into a structured explanation.

```
explain(symbol_name="Router", verbosity="detailed")
```

Verbosity levels: "summary" (concise), "detailed" (default), "full" (everything).

### Project Documentation Tools

#### `overview`
Get a high-level overview of the indexed project: file counts, language breakdown, symbol counts by type, directory structure, quality metrics, and top modules.

```
overview()
```

No parameters required. Returns a structured summary of the entire indexed project.

#### `architecture`
Document the architecture of the indexed project: layers, module dependencies, class hierarchies, entry points, hub symbols (highest connectivity), and complexity hotspots.

```
architecture()
```

No parameters required. Returns architectural analysis with layers, dependencies, classes, entry points, and structural insights.

### Memory Tools

#### `remember`
Store a semantic memory for later recall.

```
remember(
    content="The auth service uses JWT tokens with 24h expiry",
    memory_type="decision",
    tags="auth,jwt",
    ttl="permanent"
)
```

Memory types: note, decision, conversation, status, preference, doc.
TTL options: permanent, month, week, day, session.

#### `recall`
Search memories by semantic similarity.

```
recall(query="how does authentication work?", limit=5, tags="auth")
```

#### `forget`
Delete memories by ID, tags, or type.

```
forget(tags="temporary")
forget(memory_type="session")
forget(memory_id="abc-123")
```

## Configuration

Set via environment variables before starting the server:

```bash
# Embedding model selection
export NEXUS_EMBEDDING_MODEL=jina-code          # Default: jina-code (768d, code-specific)
                                                 # Options: bge-small-en (384d)
export NEXUS_EMBEDDING_DEVICE=auto               # auto (CUDA > MPS > CPU), cuda, mps, cpu

# Search tuning
export NEXUS_SEARCH_MODE=hybrid          # hybrid, vector, or bm25
export NEXUS_FUSION_WEIGHT_VECTOR=0.5    # Vector weight in RRF
export NEXUS_FUSION_WEIGHT_BM25=0.3      # BM25 weight in RRF
export NEXUS_FUSION_WEIGHT_GRAPH=0.2     # Graph weight in RRF

# Resource limits
export NEXUS_MAX_FILE_SIZE_MB=10         # Skip files larger than this
export NEXUS_MAX_MEMORY_MB=350           # Memory budget target

# Logging
export NEXUS_LOG_LEVEL=INFO              # DEBUG, INFO, WARNING, ERROR
export NEXUS_LOG_FORMAT=json             # text or json (json for production)

# Storage
export NEXUS_STORAGE_DIR=.nexus          # Where indexes are stored
```

### Embedding Models

Nexus-MCP supports two embedding models. Only registered model names are accepted; custom model names raise a `ConfigurationError`.

| Model | HuggingFace ID | Dims | Code-specific? | Notes |
|-------|---------------|------|:-:|---|
| `jina-code` (default) | `jinaai/jina-embeddings-v2-base-code` | 768 | Yes | Best code search quality, ONNX |
| `bge-small-en` | `BAAI/bge-small-en-v1.5` | 384 | No | Smallest download (~50MB), general text |

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
