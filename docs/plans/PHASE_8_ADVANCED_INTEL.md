# Phase 8: Advanced Intelligence & Visualization

This phase enhances Nexus-MCP's core capabilities to compete with industry standards like `ripgrep` for reliability and `AppMap`/`Nogic` for visualization, while expanding its unique semantic memory feature to support multi-repository workflows.

## User Review Required

> [!IMPORTANT]
> The **Ripgrep Fallback** requires `rg` to be installed on the host system. We will provide a configuration to specify the `rg` path or disable it if preferred.
> **Mermaid diagrams** will be returned as raw text strings (Markdown compatible) which can be rendered in IDEs like VS Code or Obsidian.

## Proposed Changes

### Component: Core Search & Reliability
Add a "Live-Grep" fallback to the `search` tool to ensure 100% code coverage, even for unindexed or stale files.

#### [MODIFY] [server.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/server.py)
- Integrate `ripgrep` execution into the `search` tool with a fallback to `grep`.
- Logic: If hybrid search returns fewer than `limit` results, or if explicitly requested via a new `live_grep` flag, run `rg` across the workspace. If `rg` is missing, fallback to standard `grep -r`.
- Deduplicate `rg`/`grep` results with indexed results using absolute file paths and line numbers.

#### [NEW] [live_grep.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/engines/live_grep.py)
- A lightweight wrapper around the `rg` CLI with a `grep` fallback.
- Handles glob filtering and respects `.gitignore` (for `rg`) or basic exclusion (for `grep`).

---

### Component: Visualization & Architecture
Expose the internal code graph as human-readable diagrams.

#### [NEW] [visualize.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/formatting/visualize.py)
- Implement `to_mermaid(graph, node_id, depth, type="flowchart")`.
- Logic: Walk the `rustworkx` graph starting from `node_id` up to `depth`.
- Generate Mermaid-compatible syntax for callers/callees (flowchart) or class hierarchies (classDiagram).

#### [MODIFY] [server.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/server.py)
- Add `visualize` tool.
- Inputs: `symbol_name`, `depth` (default 2), `diagram_type` (flowchart/class/sequence).

---

### Component: Scalability & Memory
Allow agents to share knowledge across different projects.

#### [MODIFY] [memory_store.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/memory/memory_store.py)
- Update `recall` and `remember` to support a `scope` parameter (`local` vs `global`).
- Global memories will be stored in a shared directory (e.g., `~/.nexus/global_memory`) instead of the project-specific `.nexus` dir.

---

### Component: Dynamic Awareness (Prototype)
Link code chunks to execution data.

#### [NEW] [log_ingestor.py](file:///Users/shreyasjagannath/dev/Nexus-MCP/src/nexus_mcp/analysis/log_ingestor.py)
- Parse JSON logs and extract file/line metadata.
- Associate log counts/errors with existing `CodeChunk` IDs in LanceDB.

## Verification Plan

### Automated Tests
- `pytest tests/test_live_grep.py`: Verify `rg` output parsing and deduplication.
- `pytest tests/test_visualize.py`: Verify Mermaid string generation for a sample graph.
- `pytest tests/test_global_memory.py`: Verify memories persisted in one project are recallable in another.

### Manual Verification
- Run `nexus-mcp` in Claude Desktop and call `visualize` on a known symbol.
- Perform a search for a string in a newly created file (unindexed) to verify Ripgrep fallback.
