# Nexus-MCP Agent Guidelines

[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/card.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

## Architecture
Single MCP server consolidating CodeGrok + code-graph-mcp. 10 tools, <350MB RAM.

## Stack
- **LanceDB**: vectors + FTS (replaces ChromaDB)
- **ONNX Runtime**: inference (replaces PyTorch)
- **bge-small-en**: default embedding model (50MB)
- **rustworkx**: in-memory directed graph
- **tree-sitter + ast-grep**: dual parsing

## Key Constraints
- **Python 3.10, 3.11, or 3.12** (Python 3.13+ is not yet supported by tree-sitter-languages)
- **pip** (comes with Python)
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

## Git Workflow
- Never edit, commit, merge, rebase or push directly on `main`/`master`. Each session works on its own branch (in a worktree for Claude Code).
- Commit on the branch, then ship with `~/.claude/scripts/ship.sh`: it rebases on `main`, fast-forwards `main` and pushes. Direct pushes to `main` are blocked by a hook.
- `ship.sh` refuses a dirty tree or a run from `main`; set `$SHIP_CHECK` (e.g. `ruff check . && pytest -v`) to gate the ship, `--no-push` to skip the push.
- Bypass only when the user asks for work on `main` in chat: `export CLAUDE_ALLOW_MAIN=1`.

## Cross-harness compatibility
`AGENTS.md` is the single source of truth for every agent harness driving this repo. Per-tool bridges:
- Claude Code → `CLAUDE.md` + `.claude/rules/`
- Codex CLI → reads this file natively (keep it lean)
- Cline → `.clinerules/00-source-of-truth.md`; no parallel Memory Bank
- Antigravity CLI → `GEMINI.md` thin bridge, speculative until its discovery mechanism is documented
Durable rules go here (portable) — never into a tool-specific file.

