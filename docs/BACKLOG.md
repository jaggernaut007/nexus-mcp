# Backlog

Single list of leftover work, as of 2026-10-03. It replaces `todo.md`. Each item has its
source, its dependency and a verdict. Done work is in `PROGRESS.md` and `CHANGELOG.md`.

Verdicts: **Next** (do soon), **Decide** (needs the owner), **Later**, **Parked** (revisit
only if a concrete need appears).

North star (2026-10-08): lower token use with the same accuracy. See `CLAUDE.md`. Rank
each item below by the tokens that it can save.

## Next

Done on 2026-10-03 and removed from this list: tool descriptions and server `instructions`
(issue #5), one registration name (#6), plugin and Codex docs (#8), the tool-name parity test
(#4, #10), and the `nexus-plugin` benchmark condition.

| Item | Source | Depends on | Notes |
|---|---|---|---|
| Routing eval, remaining rows: Tool Search off and the `nexus` skill condition | docs/EVALS.md | `CLAUDE_CODE_OAUTH_TOKEN` | `mcp-only`, Tool Search on, 3 runs: 56% (10 tools, 2026-10-05) to 97% (8 tools, 2026-10-26). Decide on `alwaysLoad` only after the Tool-Search rows |
| Make `memory` beat Claude's own file memory for "remember that..."; revisit `analyze` for "review this directory" | docs/EVALS.md | A held-out prompt set | Do not tune on the same 25 prompts; add new prompts first |
| Cost split of the 12 nexus runs of 2026-10-07 ($0.63): 42% is the cache read of the prompt on each turn (19,000 tokens x 47 turns), 57% is cache writes (one third of them the cold prompt on turn 1, the rest tool results). Tool results: `search` 54,500 characters, Read 51,800, `graph` 23,500. A smaller `search` result is thus worth about 2% of the cost; fewer turns and fewer reads of long functions are worth more | trace analysis 2026-10-08 | live run | A snippet budget by rank (whole symbols until 4,000 characters are used) was simulated: it made the result larger and covered only 66 of 250 lines that the agent read later, because those lines are in functions longer than 2,000 characters. Not built. |
| Rule for a tool, from the 12 tasks of 2026-10-07 with the cold-cache write taken out: output tokens for each turn are equal with and without nexus (259 and 266), so reasoning follows turns. One turn costs about $0.0098 and 1,000 result characters about $0.0012: a result may be 8,000 characters larger than a grep result if it saves one turn. Nexus adds 4,900 prompt tokens to each turn ($0.069 over the run) and cost 6% more ($0.587 against $0.553) | trace analysis 2026-10-08 | live run with 3 reps | Plan: removals (reranker, graph list, jina-code, `explain`, `health`, unused parameters), then parser data (resolved names, outlines, module cards), DeepSeek summaries as an eval, agent notes. |
| Live rerun of the jobscout suite with 3 reps and warm-up calls: DONE 2026-10-08. nexus $0.459 against $0.571 for Grep and Read (-19.7%), 34 turns against 55, 36 of 36 correct in both | docs/EVALS.md | - | Open: the prompt of nexus is still 3,600 tokens larger on every turn; the two impact questions cost $0.004 to $0.005 more than Grep and Read (repeated `graph` calls; `find_symbol` with Grep and Read). Next steps: parser data (resolved names, outlines, module cards), DeepSeek summaries as an eval, agent notes, removal of `jina-code`. |
| **Nexus costs 8.9% more than Grep and Read on django** ($0.973 against $0.894) although it uses 16% fewer turns (78 against 93): it wins on questions that take 18 to 24 turns with Grep and Read (`SQLCompiler.as_sql` -21%, `URLResolver.resolve` -10%) and loses on lookups that take 2 or 3 turns (+$0.002 to +$0.019) | live run 2026-10-27, docs/EVALS.md | The raw traces in `benchmarks/results/raw/runs-django-turns` | Cost split DONE 2026-10-27 (docs/EVALS.md): cache writes +$0.111, prompt reads only +$0.011, output -$0.042. The cause is result size: `find_symbol` 9,200 characters for each call against 3,400 for a django Read; a class lookup sends up to 6,000 characters of the body. Prices (fitted, exact): $0.20 read, $4 write, $10 output per million. (1) An outline for a class in `find_symbol`: DONE 2026-10-27, commit `a67e5b4`, django nexus $0.973 to $0.863 (-3.4% against Grep and Read), jobscout $0.459 to $0.446; watch jobscout gateway flow (5 to 10 turns; the agent used Grep, not `Class.method`, after an outline). (2) A smaller code budget for a call with several names: TRIED AND DISCARDED 2026-10-28 (`find_symbol` output -25% on django, but cost $0.863 to $0.902; branch `discarded-find-symbol-budget`). The same run showed about 20% cost noise over 3 runs for each question, so changes of a few percent need more runs. Next: (3) fix `find_symbol` with a dotted name and `exact=False`, which always returns "not found" (5 of 9 calls on `Field.validate`), (4) shorter server instructions (probably written to the cache once per question; not checked), (5) more runs for each question before a keep-or-drop decision. Accept a change only if neither jobscout nor django gets worse |
| Benchmark tasks on a large project that the model does not know | benchmark analysis 2026-10-06 | A private or recent large repository | In the django smoke test the agent went straight to `django/core/validators.py` from memory. A famous repository measures the model's memory, not the tool |
| Lower the fixed context per turn: a nexus run carries about 4,600 more cached tokens in every turn than a baseline run (16,700 against 12,100) | benchmark traces 2026-10-06 | — | Test fewer tools in the default set (`search`, `graph`, `explain`, `map`) and shorter descriptions. Rerun the routing eval after each change |
| A reranker that helps on code (the FlashRank path was removed 2026-10-26: it lowered hit@1 on two of four suites) | docs/research/embedding-models-2026-10.md, round 5 | — | A code-trained cross-encoder is the only candidate. The bar: no suite gets worse, and under 100 ms for a query |
| Re-tune RRF weights on identifier-style queries | ROADMAP-2026 item 12 | Query set growth | Current 0.5 (vector) and 0.3 (bm25) are untuned. The graph list is off since 2026-10-06 |
| Bring memory under the old 350 MB target, or change the target | docs/MEMORY.md | — | Measured 460 MB with the model loaded. Test `embedding_batch_size`, a lower `max_seq_length`, an int8 model, and unloading the model after an idle period |
| `trust_remote_code` default to `false` (only the deprecated `jina-code` needs it); remove `jina-code` in the next major release, which makes the flag unnecessary | PROGRESS Phase 6a vs `config.py:52` | — | Code and docs disagree today |
| `suggested_action` and `isError` on error results | ROADMAP-2026 item 5 | — | Errors come back as normal results with an `error` key |
| Stable JSON shapes (the `compact` mode itself is done, see `detail` on `search` and `graph`) | ROADMAP-2026 item 8 | — | Live rerun 2026-10-06 on a 460-file project: 23/23 correct, median cost $0.063 to $0.051 (baseline $0.030), fresh tokens 11.0k to 8.0k. Total tokens did not move (cached prompt) |
| Index speed on a large project: django (45,744 chunks) took 46 minutes on CPU | benchmark 2026-10-06 | — | Not a priority (owner, 2026-10-06: index time may be long). Measured options: `max_seq_length` 256 is 1.5 times faster, MPS is 2.4 times faster, and 70% of the chunks are tests |
| Check the login with a real call at runner start: a bad token failed 16 runs with `401` before anyone saw it | benchmark 2026-10-06 | — | `require_login` only checks that the variable is set |
| Python 3.13 support | pyproject `<3.13` cap | `tree-sitter-language-pack` or per-language wheels | `tree-sitter-languages` has no 3.13 wheel |
| Enforce `max_memory_mb` | issue #1 | — | The setting exists but nothing reads it |
| `nexus_mcp.__version__` says 2.0.0 while the package is 2.0.3; `status` reports it | 2026-10-05 benchmark trace | — | Derive it from the package metadata or bump it with each release, and test that it equals `pyproject.toml` |
| Fusion weights: hybrid still loses to vector on prose queries in Flask after the graph list was dropped (0.59 against 0.64 hit@1) | docs/research/embedding-models-2026-10.md, rounds 2 and 3 | Query set growth | Tune the vector and bm25 weights on the `flask` and `jobscout` suites split by query kind (roadmap item 12) |
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
