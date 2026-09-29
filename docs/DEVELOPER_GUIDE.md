# Developer Guide

## Development Setup

```bash
# Clone the repository
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Optional: install reranker for full feature set
pip install -e ".[reranker]"

# Verify installation
pytest -v
ruff check .
nexus-mcp-ci --help
```

## Project Structure

```
Nexus-MCP/
├── src/nexus_mcp/          # Source code (7,200+ lines)
│   ├── server.py           # FastMCP server: 10 thin tool wrappers, entry point
│   ├── core_api.py         # Tool logic + input validation, importable without MCP
│   ├── config.py           # Settings with NEXUS_ env prefix
│   ├── state.py            # Session state singleton
│   ├── core/               # Data models, interfaces, exceptions
│   ├── parsing/            # tree-sitter + ast-grep parsers
│   ├── engines/            # Vector, BM25, graph, fusion, reranker
│   ├── analysis/           # Code complexity and quality analysis
│   ├── memory/             # Semantic memory store
│   ├── indexing/           # Pipeline, embedding service, chunker
│   ├── formatting/         # Token budget, response builder
│   ├── persistence/        # SQLite graph persistence
│   ├── security/           # Permission categories, rate limiter
│   └── middleware/         # Audit logging
├── tests/                  # 607 tests across 45 files
├── benchmarks/             # Token-efficiency benchmark harness (ADR-018)
├── self_test/              # End-to-end demo of all 10 tools
├── plugin/                 # Claude Code plugin: MCP registration + routing skill
├── docs/                   # Architecture, ADRs, research notes
│   ├── adr/                # 18 Architecture Decision Records
│   └── research/           # Research notes on libraries
├── pyproject.toml          # Build config, dependencies
├── AGENTS.md               # Source of truth for agent harnesses
├── CLAUDE.md               # Claude Code context
├── PROGRESS.md             # Phase tracking
└── LICENSE                 # PolyForm Noncommercial 1.0.0
```

## Running Tests

```bash
# Full suite (607 tests)
pytest -v

# Skip slow performance benchmarks (this is what CI runs)
pytest -v -m "not slow"

# Run specific test file
pytest tests/test_security.py -v

# Run with coverage (if pytest-cov installed)
pytest --cov=nexus_mcp --cov-report=term-missing
```

### Test Categories

| File | Tests | Purpose |
|------|-------|---------|
| `test_tools_basic.py` | 28 | Core tools (index, search, status), background reindex |
| `test_graph_tools.py` | 17 | `find_symbol` and `graph` (callers/callees direction) |
| `test_analyze_tool.py` | 6 | `analyze` tool |
| `test_impact_tool.py` | 6 | `graph(transitive=True)` change-impact analysis |
| `test_explain_tool.py` | 5 | `explain` tool with verbosity levels |
| `test_hybrid_search.py` | 8 | Hybrid search, fusion, reranking |
| `test_live_grep.py` | 4 | rg/grep fallback |
| `test_permissions.py` | 20 | READ/MUTATE/WRITE categories, action-aware `memory` |
| `test_rate_limiter.py` | 11 | Token-bucket rate limiter |
| `test_audit.py` | 10 | Audit logging and field redaction |
| `test_security.py` | 19 | Input validation, SQL injection, path traversal |
| `test_e2e.py` | 8 | End-to-end lifecycle, corrupt index recovery |
| `test_performance.py` | 4 | Performance benchmarks (marked slow) |
| `test_memory_usage.py` | 5 | RSS monitoring and memory stability |
| `test_memory_store.py` | 22 | Memory store CRUD, TTL, tags |
| `test_vector_engine.py` | 20 | LanceDB vector engine CRUD |
| `test_bm25_engine.py` | 19 | LanceDB full-text search |
| `test_pipeline.py` | 27 | Indexing pipeline, incremental reindex, staleness |
| `test_chunker.py` | 22 | Symbol-to-chunk conversion |
| `test_bench_*.py` | 128 | Benchmark harness (`benchmarks/`) |
| ... | ... | Run `pytest --collect-only -q` for the full list |

### Test Conventions

- **Naming:** `test_{function}_{scenario}`
- **Fixtures:** Shared setup in `tests/conftest.py` (`mini_codebase`, `mini_codebase_with_calls`, `_setup_indexed`, `_call_tool`)
- **No mocking of core models** (Symbol, ParsedFile, etc.)
- **Both happy path and error cases** required
- **Fast:** Each test <5s, full suite <30s
- **File system tests** use `tmp_path` fixture

## Linting

```bash
# Check for issues
ruff check .

# Auto-fix
ruff check --fix .

# Configuration in pyproject.toml: E, F, W, I rules, 100 char line length
```

## Adding a New MCP Tool

Each tool has two parts. The logic lives in `core_api.py`, so other processes can import it without MCP. The MCP wrapper in `server.py` adds the permission guard, rate limiter and audit log.

1. Add the logic as a plain function in `core_api.py`:
   ```python
   def my_tool(param: str) -> dict[str, Any]:
       """Tool description."""
       # Validate input
       err = validate_query(param)
       if err:
           return err

       # Require indexing if needed
       state, err = require_indexed()
       if err:
           return err

       # Tool logic here
       return {"result": "..."}
   ```

2. Add input validation if the tool accepts user input. Use `validate_path`, `validate_symbol_name` or `validate_query`, or add a new `validate_*` helper in `core_api.py`.

3. Add a thin wrapper inside `create_server()` in `server.py`:
   ```python
   @mcp.tool()
   @_audited
   def my_tool(
       param: Annotated[str, "What this parameter does"],
   ) -> dict[str, Any]:
       """Use when ... (one routing-oriented sentence for MCP clients)."""
       guard_err = _guard("my_tool")
       if guard_err:
           return guard_err
       return core_api.my_tool(param)
   ```

4. Register the tool in `TOOL_PERMISSIONS` in `security/permissions.py`.

5. Write tests in a new `tests/test_my_tool.py` or add to an existing file.

6. Update the tool table in `README.md`, the tool reference in `docs/USAGE_GUIDE.md`, `llms.txt` and `plugin/skills/nexus-mcp/SKILL.md`. Regenerate `llms-full.txt` with `python scripts/build_llms_full.py`.

7. Update the tool count in `CLAUDE.md` and `AGENTS.md`.

Keep the tool surface small. [ADR-017](adr/ADR-017-tool-consolidation.md) explains why the project merged 15 tools into 10. Prefer a new `action` or `detail` parameter on an existing tool to a new tool.

## Adding a New Engine

1. Create `engines/my_engine.py` implementing the `IEngine` interface (or a custom interface).

2. Wire it into `IndexingPipeline.__init__()` in `pipeline.py`.

3. Expose it via `SessionState` in `state.py` (add property + setter).

4. Wire it into `core_api.py` in the `index` function (store reference on state).

5. Write tests in `tests/test_my_engine.py`.

## Architecture Decision Records

All significant design decisions are documented in `docs/adr/`. When making a key decision:

1. Copy `docs/adr/ADR-000-template.md`
2. Number it sequentially (the next free number is ADR-019)
3. Document: Context, Decision, Alternatives Considered, Consequences
4. Add a reference to `CLAUDE.md` under "Key Decisions"

## Key Design Patterns

### Singleton State
`state.py` uses a module-level singleton. Always access via `get_state()`. Reset with `reset_state()` in tests.

### Lazy Loading
Engines are `None` until indexing runs. The embedding model is loaded during indexing and unloaded afterward to free RAM. FlashRank loads on first rerank call.

### Defensive Validation
All tool inputs are validated at entry (null bytes, length limits, path traversal). SQL filter values are escaped. This happens in `core_api.py` (the `validate_*` helpers) before any business logic.

### Graceful Degradation
If FlashRank isn't installed, reranking falls through to passthrough. If BM25 fails, hybrid search continues with remaining engines. Each engine failure is logged but doesn't crash the server.

## Debugging

### Enable Debug Logging
```bash
NEXUS_LOG_LEVEL=DEBUG nexus-mcp-ci
```

### JSON Logging (for structured log analysis)
```bash
NEXUS_LOG_FORMAT=json nexus-mcp-ci
```

### Check Index Health
Use the `status` tool to verify:
- `indexed: true` — Codebase has been indexed
- `vector_chunks > 0` — Vector store has data
- `bm25_fts_ready: true` — Full-text search index built
- `graph.total_nodes > 0` — Graph has structure
- `memory.peak_rss_mb < 350` — Within memory budget

## Code Provenance

Nexus-MCP was built by consolidating two earlier projects. Each source file that was ported retains a "Ported from" line in its module docstring documenting its origin.

- **CodeGrok MCP** — Original author: rdondeti (Ravitez Dondeti). Licensed under MIT.
- **code-graph-mcp** — Original author: [entrepeneur4lyf](https://github.com/entrepeneur4lyf).

| Component | Origin | Files |
|-----------|--------|-------|
| Core models (Symbol, ParsedFile, Memory) | CodeGrok MCP | `core/models.py`, `core/interfaces.py`, `core/exceptions.py` |
| tree-sitter parser | CodeGrok MCP | `parsing/treesitter_parser.py` |
| Embedding service (ONNX) | CodeGrok MCP | `indexing/embedding_service.py` |
| Parallel indexer | CodeGrok MCP | `indexing/parallel_indexer.py` |
| Graph models (UniversalNode) | code-graph-mcp | `core/graph_models.py` |
| ast-grep parser | code-graph-mcp | `parsing/astgrep_parser.py` |
| Graph engine (rustworkx) | code-graph-mcp | `engines/graph_engine.py` |
| Code analyzer | code-graph-mcp | `analysis/code_analyzer.py` |
| File watcher | code-graph-mcp | `parsing/file_watcher.py` |
| Language registry | Both (merged) | `parsing/language_registry.py` |

Everything else (vector engine, BM25, fusion, reranker, memory store, pipeline, formatting, persistence, server, all hardening) was written fresh for Nexus-MCP.

## Release Process

1. Update version in `pyproject.toml`
   (the CI workflow `.github/workflows/publish.yml` builds and publishes to PyPI)
2. Run full test suite: `pytest -v`
3. Run linter: `ruff check .`
4. Run Snyk scan
5. Build: `python -m build`
6. Test install: `pip install dist/nexus_mcp_ci-*.whl` in a clean venv
7. Verify: `nexus-mcp-ci` starts and `status` works
8. Update `CHANGELOG.md`, and regenerate `llms-full.txt` (`python scripts/build_llms_full.py`)
