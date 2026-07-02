# Nexus-MCP Strategy & Roadmap — July 2026

Synthesis of three research passes (2026-07-02): competitive landscape, agent-harness
trends (Jan–Jul 2026), and an internal product-quality audit.

North star: **efficient token utilization + high-performance, intelligent, accurate context.**

---

## Verdict: still relevant, but the pitch and packaging must change

The "is semantic indexing dead?" debate settled in 2026 into a three-way consensus:

1. **Agentic grep is the default baseline** — Claude Code, Codex CLI, Gemini CLI ship no
   vector index, and frontier models (Opus 4.8 / Sonnet 5 class) roughly halved
   tool-calling token cost twice since late 2025. On small/medium repos, grep won.
2. **Indexes win on token efficiency, not search quality.** Turbopuffer's ContextBench:
   baseline Claude Code wastes 1-in-3 file reads; grep 1-in-5; grep+semantic **1-in-8**.
   Cursor: +12.5% answer accuracy on 1,000+ file repos. The value concentrates in
   large/legacy/monorepo codebases.
3. **Graphs beat both for structural queries.** codebase-memory-mcp's arXiv paper: 10x
   fewer tokens, dominates callers/impact/hub queries — the one category where an index
   *beats* (not just matches) agentic search.

Nobody credible ships **hybrid retrieval + call graph + memory in one local, low-RAM
server**. That is nexus-mcp's lane. But two projects already own adjacent mindshare:

| Competitor | Stars (6/2026) | Owns | Weakness we exploit |
|---|---|---|---|
| **Serena** (oraios) | ~26k (+85k plugin installs) | LSP precision + symbolic *editing* | Context bloat, slow on big repos, no semantic search, no impact analysis, per-language LSP servers |
| **codebase-memory-mcp** (DeusData) | ~24k | Zero-dep binary, graph, published benchmark | No semantic/conceptual search at all (83% vs 92% answer quality) |
| **claude-context** (Zilliz) | ~12k | Hybrid BM25+vector | Needs Milvus + paid embedding API — its LanceDB community fork validates our architecture |
| Claude Code native (LSP + Explore) | — | Free, zero-setup symbol nav | Immature, no conceptual search, no transitive impact, token-hungry on large refactors |

**Positioning (2026 pitch):** *"LSP answers 'where is this symbol.' Nexus answers 'where
is this concept,' 'what breaks if I change it,' and 'what did we decide last month' —
one local server, <350MB, no cloud, no API keys, no per-language LSP processes."*
Complement grep/LSP, don't fight them. Be honest about scope: below ~20k LOC, the
skill should tell the agent to just grep (community has internalized this; overclaiming
earns "am I the only one not finding value?" threads).

---

## P0 — Trust: daily-driver blockers (audit: readiness is 70–75%) — ✅ DONE 2026-07-02

Nothing else matters if users silently search stale code. Implemented, tested (455
tests passing, `ruff` clean), and verified end-to-end against a real index run. Full
execution plan: `~/.claude/plans/staged-churning-pinwheel.md`; design rationale:
[ADR-015](adr/ADR-015-auto-watch-and-staleness-detection.md),
[ADR-016](adr/ADR-016-remove-unused-pydantic-schemas.md).

1. ✅ **Staleness detection + auto-reindex.** Implemented as throttled mtime-diffing
   (`IndexingPipeline.check_staleness`, `NEXUS_STALENESS_CHECK_INTERVAL`, default 15s) —
   `status()`/`search()` surface `stale`/`staleness_warning`/`warning` and fire a
   non-blocking background reindex. Branch switches are covered for free by the same
   mtime diff (checkout touches mtimes) — no separate git-HEAD tracking needed, a
   deliberate simplification over the original sketch above.
   `parsing/file_watcher.py` is now wired in behind `NEXUS_AUTO_WATCH` (default on),
   with debounced incremental reindex per indexed root.
2. ✅ **Progress reporting during indexing.** `index()` is now `async def` with
   `ctx: Context` injection; the `ParallelProgress` callback bridges to
   `ctx.report_progress()` (throttled to ~2/sec so large repos don't flood the client).
3. ✅ **Fixed all three deferred bugs:**
   - `multi_index()` is now incremental-aware (mtime-diffs when the root set matches).
   - `analyze()` rejects path traversal (`is_relative_to`-style boundary check).
   - Dead Pydantic `schemas/` package deleted outright (ADR-016) rather than wired in —
     nothing depended on it, and wiring it in was out of scope for a trust-focused pass.
4. ✅ **Documented graph limitations** — new section in `CLAUDE.md` and `README.md`
   covering dynamic dispatch, closures/callbacks, and per-language graph fidelity.
5. ⬜ **Still open, deferred to a future small pass:** recovery-hint errors
   (`suggested_action` field on error payloads, e.g. "index corrupt → run index"). Not
   part of the Phase 1 scope — low effort, no design risk, safe to pick up any time.

## P1 — Packaging: ride the 2026 distribution rails (skills/plugins/subagents)

The current 150-line "MANDATORY" CLAUDE.md block is the documented 2026 anti-pattern
(always-loaded procedure text). Anthropic's own guidance: *"pairing an MCP connection
with a skill that teaches Claude how to use it is far better than either alone."*

6. **Ship a Claude Code plugin**: MCP server + skill in one installable unit.
   - SKILL.md body <500 tokens; routing-rule description ("Use when asking where/how/what-breaks
     across a codebase and grep would need multiple guesses"); companion reference files.
   - Skill encodes: index-first workflow, staleness handling, honest scoping (<20k LOC → grep),
     and the Explore-subagent pattern (nexus `search` as a Haiku Explore agent's first move).
   - Publish: official MCP registry, mcp.so, Smithery, Glama; `.mcpb` bundle for Desktop.
7. **Consolidate 15 tools → ~9 outcome-oriented tools** (Tool Search is now default —
   each name+description must route independently; fewer round-trips wins):
   - `find_callers` + `find_callees` + `impact` → `graph(symbol, direction, transitive)`
   - `overview` + `architecture` → `map(depth=summary|architecture)`
   - `remember` + `recall` + `forget` → `memory(action, ...)`
   - Rewrite every description as a when-to-use routing rule; mention live-grep fallback
     and default reranking.
8. **Token-frugal, code-mode-friendly responses:**
   - `compact` mode default for search (id, name, file:line, score, 1-line signature);
     bodies on request. Stable documented JSON shapes so programmatic tool calling can
     filter results in-sandbox.

## P2 — Differentiation: win the benchmark war

9. **Publish a token-efficiency benchmark** (the metric that lands in 2026):
   ContextBench-style wasted-read ratio + total tokens-to-answer on 1,000+ file repos —
   nexus vs. baseline Claude Code vs. Serena vs. codebase-memory-mcp. codebase-memory-mcp
   went 0→24k stars in 4 months largely on one honest benchmark + one-line install.
10. **Lead marketing with graph tools** (`impact`, callers, hubs) — the only proven
    "beats agentic search" category — with hybrid semantic search as the foothold layer.
11. **Reframe memory as team-shareable project memory** (semantically searchable, lives
    with the repo, shared across engineers) vs. Claude's per-user auto-memory; optional
    MEMORY.md surfacing integration. Don't compete head-on with a default-on native feature.
12. **Ranking improvements:** path-affinity and hub-centrality boosts in fusion;
    keep flat search (RAM budget) but tune RRF weights on the benchmark.
13. **Monorepo story:** multi-folder indexing already exists — market it; Sourcegraph
    charges enterprise money for this.

## Explicitly deprioritized

- **Symbolic editing** (Serena's wedge) — large lift, overlaps with native LSP-backed
  editing; revisit only after P0–P2.
- **Competing on memory alone** — Mem0/OpenMemory own that category (~58k stars).
- **More embedding models / bigger indexes** — the field stopped rewarding "better
  vectors"; it rewards fewer wasted tokens.

## Sources

Key references: Serena (github.com/oraios/serena), codebase-memory-mcp
(arXiv 2603.27277), Zilliz claude-context, Turbopuffer ContextBench coverage
(startuphub.ai), Cursor semsearch blog, Anthropic tool-search / code-execution-with-MCP /
writing-tools-for-agents engineering posts, code.claude.com skills & best-practices docs.
Full citations in the 2026-07-02 research session.
