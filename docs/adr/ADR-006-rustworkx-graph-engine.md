# ADR-006: rustworkx for In-Memory Code Graph

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
code-graph-mcp used rustworkx (Rust-backed Python graph library) for storing and querying code relationships. We needed a graph engine for call graph traversal, impact analysis, and symbol lookup. The question was whether to keep rustworkx or switch to a graph database.

## Decision
Keep rustworkx PyDiGraph as the graph engine. Port `RustworkxCodeGraph` from code-graph-mcp with thread-safe RLock, indexed lookups by type/language/name, and lightweight node payloads ({id, name, type, file, line}).

## Consequences
- **Easier:** No external DB server needed, 10-100x faster than NetworkX, Rust-backed performance, already production-tested in code-graph-mcp, supports PageRank/betweenness/SCC/cycle detection
- **Harder:** In-memory only (need SQLite persistence for graph serialization in Phase 4), graph lost on crash without persistence, memory grows with codebase size (~50MB for 50K nodes)

## Alternatives Considered
- **Neo4j:** Rejected — requires running a separate server, overkill for local dev tool, adds 500MB+ memory
- **NetworkX:** Rejected — pure Python, 10-100x slower than rustworkx for same operations
- **igraph:** Rejected — C-based, less Pythonic API, rustworkx has better typing support
- **SQLite graph tables:** Rejected — SQL traversal queries are complex and slow for multi-hop graph operations
