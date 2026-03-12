# ADR-011: Phase 5 Hardening Decisions

## Status: Accepted
## Date: 2026-03-12

## Context

Phase 5 hardens Nexus-MCP for production. Several design decisions were needed for graceful shutdown, corrupt index recovery, structured logging, input validation, and memory monitoring.

## Decisions

### Graceful Shutdown

**Decision:** Signal handlers in `main()`, cleanup logic in `state.shutdown()`.

SIGTERM/SIGINT handlers registered in `main()` call `state.shutdown()` which persists the graph via `GraphPersistence` and sets a shutdown flag. The `server.run()` call is wrapped in try/finally as a safety net.

**Rationale:** `state.py` already holds all engine references. Putting cleanup there avoids import cycles and keeps `server.py` thin.

### Corrupt Index Detection

**Decision:** Three-layer validation before incremental indexing — metadata file integrity, vector engine schema validation, automatic full rebuild on failure.

`pipeline._validate_index()` checks: (1) metadata file exists and is valid JSON with `mtimes` key, (2) `vector_engine.validate()` confirms table exists with expected schema columns. On any failure, the corrupt artifacts are deleted and a full rebuild is triggered.

**Rationale:** Full rebuild is simpler and more reliable than attempting to patch a corrupt index. The cost is a one-time re-index, which is acceptable given it runs on developer machines.

### JSON Structured Logging

**Decision:** Stdlib `logging` with a custom `JsonFormatter` class (~15 lines). Activated via `NEXUS_LOG_FORMAT=json` env var (already supported in config).

**Alternatives considered:** structlog — adds a new dependency for minimal gain. The project already uses stdlib logging throughout.

### Input Validation

**Decision:** Three validation helpers inside `create_server()`:
- `_validate_path()` — resolves symlinks, checks `is_dir()`, rejects null bytes
- `_validate_symbol_name()` — max 500 chars, no null bytes, requires `\w` character
- `_validate_query()` — max 10,000 chars, no null bytes, rejects empty/whitespace

Applied at the entry point of each tool before any processing.

**Rationale:** Validates at the system boundary (user input via MCP) rather than deep in the stack. Early rejection prevents unnecessary work and provides clear error messages.

### Memory Monitoring

**Decision:** `resource.getrusage(RUSAGE_SELF).ru_maxrss` in the `status` tool, with platform normalization (macOS returns bytes, Linux returns KB).

**Alternatives considered:** `tracemalloc` — adds 10-20% overhead and only tracks Python allocations, missing ONNX Runtime, LanceDB mmap, and rustworkx native memory. `psutil` — adds a dependency. `resource` is zero-cost and part of stdlib.

## Consequences

- All MCP tool inputs are validated before processing
- Graceful shutdown preserves graph state on SIGTERM/SIGINT
- Corrupt indexes are automatically detected and rebuilt
- JSON logging available for production deployments
- Memory monitoring via `status` tool with no performance impact
