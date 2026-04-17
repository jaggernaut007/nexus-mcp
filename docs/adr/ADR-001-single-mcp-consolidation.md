# ADR-001: Consolidate CodeGrok + code-graph-mcp into Single MCP Server

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
We had two separate MCP servers — CodeGrok (vector search + memory) and code-graph-mcp (AST analysis + call graphs). Running both simultaneously consumed 1-2GB+ RAM, required two MCP connections, and prevented cross-engine intelligence (e.g., combining semantic search with call graph traversal).

## Decision
Consolidate both servers into a single MCP server (Nexus-MCP) with one process, one connection, and 15 tools. Port reusable code from both repos into a unified `src/nexus_mcp/` package structure.

## Consequences
- **Easier:** Single connection for clients, shared state, cross-engine tools (explain, impact), halved memory overhead
- **Harder:** Larger single codebase to maintain, all tools share one process (a crash affects everything), must carefully namespace ported code to avoid conflicts

## Alternatives Considered
- **Keep two servers, add RPC bridge:** Rejected — adds latency, complexity, and doesn't solve memory duplication
- **Microservice split with shared DB:** Rejected — overengineered for a local dev tool; adds deployment complexity
- **Keep two servers as-is:** Rejected — doesn't solve the core UX problem of managing two connections
