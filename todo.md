# Deferred / Won't Fix Now

## AST grep integration

## Old Phase 8 items (docs/plans/PHASE_8_ADVANCED_INTEL.md) — viability vs. v2.0.0

Evaluated 2026-07-03 against the current 10-tool architecture (`docs/ROADMAP-2026.md`,
ADR-017). Verdicts below; none of this is scheduled — P2 item 9 (token-efficiency
benchmark) comes first.

### 8b: Mermaid graph visualization — viable, reshaped, not scheduled
v2.0.0 deliberately consolidated 15 tools into 10 outcome-oriented ones (ADR-017), so
this should NOT ship as a new standalone `visualize` tool. It fits as a
`format="mermaid"` output option on `graph()` (call-graph flowchart) and/or `map()`
(architecture diagram) — no new tool surface, same routing budget.
Blocking caveat found during exploration: the ast-grep parser
(`src/nexus_mcp/parsing/astgrep_parser.py`) only ever emits `CONTAINS` and `IMPORTS`
edges — `CALLS`/`INHERITS` exist in the graph model and `graph()`'s API but nothing
populates them on a real index. A Mermaid call-graph would render near-empty
(root node only) until CALLS-edge extraction is built, which is a separate, bigger
piece of work. Worth doing after real call edges exist, not before.

### 8c: Global semantic memory (cross-repository) — reframed, not scheduled
Superseded by ROADMAP-2026 P2 item 11: reframe memory as **team-shareable per-repo**
memory (lives with the repo, shared across engineers via the index), explicitly NOT a
per-user global store — the roadmap calls out Mem0/OpenMemory (~58k stars) as already
owning the cross-session personal-memory category, and says not to compete head-on
with a default-on native Claude feature there. If pursued, it becomes a `scope`
param on `memory()` (`local` vs. `global`/`team`), but the framing and demand for it
should come from item 11's positioning work, not from the old Phase 8 spec.

### 8d: Dynamic awareness (log ingestion/linking) — defer, won't-fix-now
Not mentioned anywhere in ROADMAP-2026 and cuts against the stated north star
("efficient token utilization... fewer wasted tokens wins in 2026") — it adds a new
ingestion pipeline, a new LanceDB table, and ongoing maintenance surface for a feature
with no validated user pull. Revisit only if a concrete user request materializes;
until then this stays parked here rather than in an active phase plan.
