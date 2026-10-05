# Backlog

Single list of leftover work, as of 2026-10-03. It replaces `todo.md`. Each item has its
source, its dependency and a verdict. Done work is in `PROGRESS.md` and `CHANGELOG.md`.

Verdicts: **Next** (do soon), **Decide** (needs the owner), **Later**, **Parked** (revisit
only if a concrete need appears).

## Next

Done on 2026-10-03 and removed from this list: tool descriptions and server `instructions`
(issue #5), one registration name (#6), plugin and Codex docs (#8), the tool-name parity test
(#4, #10), and the `nexus-plugin` benchmark condition.

| Item | Source | Depends on | Notes |
|---|---|---|---|
| Routing eval, remaining rows: Tool Search off, the `nexus` skill condition, 3 runs per prompt | docs/EVALS.md | `CLAUDE_CODE_OAUTH_TOKEN` | Main rows done 2026-10-05 (56% to 88%). Decide on `alwaysLoad` only after the Tool-Search rows |
| Make `memory` beat Claude's own file memory for "remember that..."; revisit `analyze` for "review this directory" | docs/EVALS.md | A held-out prompt set | Do not tune on the same 25 prompts; add new prompts first |
| Graph list still lowers hybrid hit@1 slightly after the whole-word fix (0.68 without it, 0.64 with it on `nexus_mcp`) | embedding eval finding 2 | Query set growth | Whole-word matching and stop words cut the loss from 12 points to 4. Re-test with a larger query set before dropping or reweighting the graph list |
| Re-tune RRF weights on identifier-style queries | ROADMAP-2026 item 12 | Query set growth | Current 0.5/0.3/0.2 are untuned |
| Bring memory under the old 350 MB target, or change the target | docs/MEMORY.md | — | Measured 460 MB with the model loaded. Test `embedding_batch_size`, a lower `max_seq_length`, an int8 model, and unloading the model after an idle period |
| `trust_remote_code` default to `false`, enable only for `jina-code` | PROGRESS Phase 6a vs `config.py:52` | — | Code and docs disagree today |
| `suggested_action` and `isError` on error results | ROADMAP-2026 item 5 | — | Errors come back as normal results with an `error` key |
| `compact` search mode and stable JSON shapes | ROADMAP-2026 item 8 | — | `search` always returns snippets up to 2,000 characters |
| Python 3.13 support | pyproject `<3.13` cap | `tree-sitter-language-pack` or per-language wheels | `tree-sitter-languages` has no 3.13 wheel |
| Enforce `max_memory_mb` | issue #1 | — | The setting exists but nothing reads it |
| Re-resolve only affected callers after an incremental reindex | ADR-019 | — | Today every edge is rebuilt (about 9 s at 14,000 files) |
| Extract `INHERITS` edges; docstrings for non-Python graph nodes; `and`/`or` in complexity for other languages | ADR-019 | — | Complexity and Python docstrings exist since 2026-10-03 |
| TS/JS arrow functions as graph nodes; Rust `use crate::` paths | ADR-019 | — | Known gaps in call-edge coverage |
| Transitive callees in `graph` | ADR-017 | Call edges (done) | Only transitive callers exist |
| Full benchmark run and published report | ROADMAP-2026 item 9, PROGRESS Phase 9 | Token; Phase B | Smoke run first; the full run takes several 5-hour windows on the Pro limit |
| Release 2.1.0 (not a patch) | CHANGELOG | All of the above | `find_symbol(name=)` became `symbol_name`, a breaking rename |

## Decide

| Item | Source | Notes |
|---|---|---|
| Licence | POSITIONING D8 | PolyForm Noncommercial limits company use. Gates registry listings and the adoption push |
| Embedding default | `docs/research/embedding-models-2026-10.md` | Evidence says keep `bge-small-en`. Reopen only with larger query sets |
| `docs/POSITIONING.md` and `docs/AGENT-WORKFLOW-CHANGES.md` are local files, not in this repository (the first is git-ignored, the second is untracked). Items below that cite "POSITIONING" refer to the maintainer's copy | maintainer's checkout | Commit them, or move the decisions into this backlog |

## Found by the pre-merge audit

All eight findings were fixed on 2026-10-05 (see the PR): the `fastmcp` lower bound, the word index
for graph relevance, `OAuth2Token`-style names, an audit record for rejected calls, the temp-table
memory migration, a staleness check on the graph tools, `index(B)` over the storage of project A,
and a measured memory table (`docs/MEMORY.md`). Still open from the same audit: none.

## Later

| Item | Source |
|---|---|
| Mermaid output (`format="mermaid"` on `graph` or `map`) | old todo.md 8b; the call-edge blocker is gone |
| Team-shareable project memory (`scope` on `memory`) | old todo.md 8c, ROADMAP-2026 item 11 |
| Persist `analyze` snapshots and show trends | POSITIONING D4 |
| Anchor memories to code entities | POSITIONING D5 |
| Verify the cross-harness memory story end to end | POSITIONING D6 |
| Detect another code-search tool and defer to it | POSITIONING R4 |
| Content hashing instead of mtimes | ADR-009 |
| Re-embed stored memories when the model changes | embedding eval |
| FlashRank research note; ADRs for RRF weights, memory TTL, FlashRank | IMPLEMENTATION_PLAN |
| Test behaviour under weaker local models | POSITIONING |
| Official MCP registry entry (`server.json`), `.mcpb` bundle | ROADMAP-2026 item 6 |
| Lead the README with `memory` and `analyze`, not search and graph | POSITIONING D1 vs ROADMAP-2026 item 10 (these conflict; decide first) |

## Parked

| Item | Source |
|---|---|
| Log ingestion (dynamic awareness) | old todo.md 8d |
| OAuth 2.1 and HTTP transport | ADR-012, `auth_mode=oauth` placeholder |
| Codex live eval (static support only: server `instructions`) | user decision 2026-10-02 |
| FUTURE_CONTRIBUTIONS items 2-7 and 10-16 (pluggable backends, trigram search, dataflow, IaC, CLI mode, ...) | `docs/FUTURE_CONTRIBUTIONS.md` |
| Deprecated aliases for the pre-2.0.0 tool names | ADR-017 |

## Outside this repository

- `~/.claude/settings.json` holds API keys in plaintext. Move them to environment variables.
- The `~/.claude/skills` do not yet route through nexus
  (`docs/AGENT-WORKFLOW-CHANGES.md`).
