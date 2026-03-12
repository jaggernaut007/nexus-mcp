# ADR-005: Dual Parser Strategy (tree-sitter + ast-grep)

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok used tree-sitter for symbol extraction (names, signatures, docstrings, code snippets). code-graph-mcp used ast-grep for structural analysis (call graphs, imports, inheritance). Each parser excels at a different task and neither fully replaces the other.

## Decision
Keep both parsers in Nexus-MCP with distinct roles:
- **tree-sitter** → extracts symbols → feeds vector engine (embeddings)
- **ast-grep** → extracts structure → feeds graph engine (rustworkx)

Merge language support into a unified registry (`parsing/language_registry.py`) covering 25+ languages.

## Consequences
- **Easier:** Best-of-both-worlds accuracy, each parser runs on its strength, unified language registry simplifies file-to-parser routing
- **Harder:** Two parser dependencies to maintain, some overlap in what they can extract (both can find functions/classes), need clear separation of responsibilities to avoid confusion

## Alternatives Considered
- **tree-sitter only:** Rejected — tree-sitter is great for symbol extraction but weak at structural analysis (call sites, imports)
- **ast-grep only:** Rejected — ast-grep doesn't extract docstrings, signatures, or code snippets as cleanly
- **unified custom parser:** Rejected — would duplicate effort already done well by both tools; maintenance burden too high
