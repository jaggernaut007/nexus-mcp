# Research Index

Research notes for libraries and technologies used in Nexus-MCP. Check here before implementing with any external library.

| Library | Status | Notes |
|---------|--------|-------|
| LanceDB | Documented in [RESEARCH.md](../RESEARCH.md#1-lancedb) | API reference, FTS, gotchas, memory profile. Phase 2: PyArrow schema, flat search, SQL-style filters |
| Embedding Models | Documented in [RESEARCH.md](../RESEARCH.md#2-embedding-models) | jina-code (default), bge-small-en; ONNX strategy, GPU/MPS auto-detection |
| rustworkx | Documented in [RESEARCH.md](../RESEARCH.md#3-rustworkx-code-graph) | Graph algorithms, memory, porting guide |
| ONNX Runtime | Documented in [RESEARCH.md](../RESEARCH.md#2-embedding-models) | PyTorch replacement, quantization |
| sentence-transformers | Used in Phase 1-2 | Model loading wrapper; lazy load + unload pattern in `embedding_service.py` |
| FastMCP | Used in Phase 2+ | v3.1 API: `FastMCP(name)`, `@mcp.tool()`. Tools in `mcp._local_provider._components` (key: `tool:{name}`). `FunctionTool.fn` for raw function access. No `description` kwarg in v3 |
| FlashRank | Needs research (Phase 4) | Re-ranking API, model loading |
| tree-sitter | Ported from CodeGrok | 9 languages, ThreadLocalParserFactory for parallel parsing |
| ast-grep | Ported from code-graph-mcp | 25+ languages, structural patterns. Sequential only (internal counters not thread-safe) |
| watchdog | Ported from code-graph-mcp | Debounced watcher with async support |
| pathspec | Used in Phase 2 | .gitignore matching; use `"gitignore"` pattern (not deprecated `"gitwildmatch"`) |
| pyarrow | Used in Phase 2 | LanceDB schema definition; `pa.list_(pa.float32(), dims)` for vector columns |
| rich | Optional (self_test) | Console tables, panels, colored output. Graceful fallback to plain print when not installed |
