# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] - 2026-07-02

### Breaking Changes

Tool surface consolidated from 15 tools to 10, to reduce Tool-Search-era description
overhead and follow current MCP tool-design guidance (fewer, richer tools). No
deprecated aliases — this is a clean break. See
[ADR-017](docs/adr/ADR-017-tool-consolidation.md) for the full rationale.

| Old tool | New call |
|---|---|
| `find_callers(symbol_name)` | `graph(symbol_name, direction="callers")` |
| `find_callees(symbol_name)` | `graph(symbol_name, direction="callees")` |
| `impact(symbol_name, max_depth)` | `graph(symbol_name, transitive=True, max_depth=...)` |
| `overview()` | `map(detail="summary")` |
| `architecture()` | `map(detail="architecture")` |
| `remember(content, ...)` | `memory(action="store", content=..., ...)` |
| `recall(query, ...)` | `memory(action="search", query=..., ...)` |
| `forget(memory_id, tags, memory_type)` | `memory(action="delete", memory_id=..., tags=..., memory_type=...)` |

Upgrade: `pip install -U nexus-mcp-ci` (or update the plugin), then update any
`CLAUDE.md`/skill content that hardcodes the old tool names to the mapping above.
Response shapes are unchanged for every merged case — only the tool name and, for
`memory`, the addition of an `action` parameter, changed.

### Added

- **Auto-reindex + staleness detection.** A debounced file watcher
  (`NEXUS_AUTO_WATCH`, default on) keeps the index fresh after edits; `status()`/
  `search()` now surface `stale`/`staleness_warning`/`warning` fields and trigger a
  non-blocking background reindex instead of silently serving stale results.
  ([ADR-015](docs/adr/ADR-015-auto-watch-and-staleness-detection.md))
- **Live indexing progress.** `index()` is now async and streams real MCP progress
  notifications instead of blocking silently for minutes on large repos.
- **Claude Code plugin + skill.** Nexus-MCP can now be installed as a plugin
  (`/plugin marketplace add jaggernaut007/nexus-mcp`) bundling the MCP server
  registration with a routing skill, replacing the old always-loaded `CLAUDE.md`
  block.
- **Known-limitations documentation** for the call graph's static-analysis blind
  spots (dynamic dispatch, closures, reflection) in `CLAUDE.md`/`README.md`.

### Changed

- `multi_index()` is now incremental-aware: it mtime-diffs like `incremental_index()`
  when the indexed root set hasn't changed, instead of always doing a full rebuild.
- `graph()`'s permission category is READ (previously `impact` was categorized
  MUTATE, reflecting computational cost rather than actual data mutation — `graph()`
  never writes anything, so READ is the correct category for the merged tool).
- `memory()`'s permission category is action-aware: `action="search"` is READ,
  `action="store"`/`"delete"` are WRITE — preserving the exact access boundaries the
  three separate tools had before consolidation
  ([ADR-017](docs/adr/ADR-017-tool-consolidation.md)).

### Fixed

- `analyze()` now rejects path traversal (`path="../../../etc"`) with a clear error
  instead of silently returning empty results.
- Dead `schemas/` package (Pydantic models never wired into any tool at runtime)
  removed. ([ADR-016](docs/adr/ADR-016-remove-unused-pydantic-schemas.md))

## [1.0.1] - 2026-04-18

### Added
- **15 Unified MCP Tools**: Complete rollout of the consolidated toolset across search, graph analysis, and semantic memory.
- **Hybrid Search Flow**: Integrated Vector, BM25, and Graph-based relevance with Reciprocal Rank Fusion (RRF) and FlashRank re-ranking.
- **Live Grep Fallback**: New `LiveGrepEngine` providing 100% code coverage fallback using `rg` or standard `grep` for unindexed/new files.
- **Visual Graph Generation**: Ability to export code relationships as Mermaid-compatible diagrams (via `architecture` and `explain` tools).
- **Glama Registry Optimization**:
    - Added `Annotated` types to all tool parameters for rich discovery and high TDQS scores.
    - Implemented `glama.json` build specification for Python 3.12 compatibility.
    - Integrated `mcp-proxy` support for cloud-hosted registry inspection.
- **CPU-Only Docker Build**: Optimized Dockerfile with specialized pip index (`https://download.pytorch.org/whl/cpu`) to eliminate 500MB+ of unnecessary CUDA/GPU libraries.

### Changed
- **Parser Hardening**: Replaced silent exception handlers in `AstGrepParser` and `FileWatcher` with detailed debug logging to resolve Bandit B110/B112 findings.
- **Tech Stack Refresh**: Switched to `jina-code` as the default embedding model (768d) for better code-specific semantic performance.
- **Architecture Documentation**: Synchronized the full documentation suite (ARCHITECTURE.md, PROJECT_INFO.md) to reflect the 15-tool system and 14 ADRs.

### Fixed
- **Glama Build Failures**: Resolved Python version mismatch (forced 3.12) and `spawn ENOENT` errors by correctly configuring `uv run` and PATH injection.
- **Memory Management**: Enforced strict <350MB RAM budget by unloading models after indexing and using lazy-loading for heavy dependencies.

### Security
- **Bandit Audit**: Achieved 0 issues status across the entire codebase.
- **Input Validation**: Hardened all tool entry points with strict parameter validation via FastMCP/Pydantic.

---
*Note: This release marks the transition of Nexus-MCP from an experimental consolidation to an industrial-grade coding intelligence server.*
