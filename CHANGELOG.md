# Changelog

All notable changes to this project will be documented in this file.

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
