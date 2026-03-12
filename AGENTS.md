# Nexus-MCP Agent Guidelines

## Architecture
Single MCP server consolidating CodeGrok + code-graph-mcp. 12 tools, <350MB RAM.

## Stack
- **LanceDB**: vectors + FTS (replaces ChromaDB)
- **ONNX Runtime**: inference (replaces PyTorch)
- **bge-small-en**: default embedding model (50MB)
- **rustworkx**: in-memory directed graph
- **tree-sitter + ast-grep**: dual parsing

## Key Constraints
- Python ≥3.10, type hints everywhere
- All modules lazy-import heavy deps
- Models unloaded after indexing (`del model; gc.collect()`)
- Batch embedding size=32
- Graph payloads: {id, name, type, file, line} only

## Testing
- Tests first (spec-driven)
- `pytest -v` for all tests
- `ruff check .` must pass
- Target: 140+ tests by Phase 5

## Code Style
- ruff for linting (line-length=100)
- Frozen dataclasses for immutable models
- ABC interfaces for swappable components
- Thread-safe singletons with locks
