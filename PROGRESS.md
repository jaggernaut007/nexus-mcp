# Project Progress: Nexus-MCP

[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/card.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

## North star

Lower token use for an agent, with the same accuracy. Set by the owner on 2026-10-08.

- **Measure:** the tokens and the cost of a task in the live benchmark (`benchmarks/`),
  against the Grep and Read condition.
- **Gate:** accuracy must not fall. Check hit@1 on the four retrieval suites
  (`python -m evals.retrieval.run`) and the answer score of the benchmark.
- Keep a change only when it lowers the measure and passes the gate. Record the two
  numbers, before and after, in `CHANGELOG.md`.

## MIT licence and install channels — 2026-11-06 (branch, not released)

Decisions: MIT for every new release ([ADR-023](docs/adr/ADR-023-mit-licence.md)), for adoption. A
paid licence key (Polar, $5 per developer per month) was built and tested in the sandbox, then
removed; ADR-022 is superseded. The default install is "ask your agent" or `uvx nexus-mcp-ci setup`.

- [x] `nexus-mcp-ci setup` (8 clients, `--dry-run`, `--uninstall`), `llms-install.md`, plugin on `uvx`
- [x] Channel files: `mcpb/` (Claude Desktop and Smithery), `gemini-extension.json`, VS Code and
  Cursor links, `docker/Dockerfile.stdio`, `action.yml`, `publish.yml` jobs, `scripts/bump_version.py`
- [x] `smithery.yaml` removed: Smithery takes only HTTP servers there. Local servers go as `.mcpb`.
- [ ] **Owner:** GitHub Sponsors (optional), then `.github/FUNDING.yml`
- [ ] **Owner:** Smithery publish, GitHub Marketplace tick, Cline issue, Context7 (see
  [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md))
- [ ] Not verified here: `mcpb pack` (npm install needs approval), the Docker build (no Docker
  daemon), the real clients (Gemini CLI, Cursor, VS Code, Desktop)

## Phase 1: Scaffold + Port Core Modules — COMPLETE
- [x] Project structure created
- [x] pyproject.toml with all dependencies
- [x] AGENTS.md, CLAUDE.md, PROGRESS.md, .gitignore
- [x] .claude/hooks.json, agents, rules
- [x] Port core/models.py from CodeGrok
- [x] Port core/interfaces.py from CodeGrok (+ IEngine)
- [x] Port core/exceptions.py from CodeGrok
- [x] Port core/graph_models.py from code-graph-mcp
- [x] Port parsing/treesitter_parser.py from CodeGrok
- [x] Port parsing/language_registry.py (merged)
- [x] Port engines/graph_engine.py from code-graph-mcp
- [x] Port parsing/astgrep_parser.py from code-graph-mcp
- [x] Port analysis/code_analyzer.py from code-graph-mcp
- [x] Port parsing/file_watcher.py from code-graph-mcp
- [x] Port indexing/embedding_service.py from CodeGrok
- [x] Port indexing/parallel_indexer.py from CodeGrok
- [x] Fresh stubs: config.py, server.py, state.py
- [x] Tests for ported modules — 112 tests, all passing in 2.8s
- [x] pip install + ruff clean
- [x] ADRs written (ADR-001 through ADR-006)
- [x] Post-phase review: code-reviewer agent
- [x] Post-phase review: docs-writer agent

## Phase 2: Indexing Pipeline + Vector Search — COMPLETE
- [x] indexing/chunker.py — Symbol → CodeChunk conversion (deterministic IDs, formatted text)
- [x] engines/vector_engine.py — LanceDB IEngine (add, search with filters, delete, upsert, clear)
- [x] indexing/pipeline.py — Orchestrator: discover → dual parse → chunk → embed → store
- [x] server.py — 3 MCP tools: `index`, `search`, `status` (enhanced)
- [x] Incremental re-indexing via mtime-based change detection
- [x] SQL injection prevention in LanceDB filter clauses
- [x] try/finally for embedding model unload (memory safety)
- [x] Thread-safe pipeline initialization in server
- [x] Tests: 72 new tests (22 chunker, 20 vector engine, 19 pipeline, 11 tools) — 184 total
- [x] ruff clean, Snyk scan clean (0 issues)
- [x] ADRs written (ADR-007 through ADR-009)
- [x] Post-phase review: code-reviewer agent + docs-writer agent

## Phase 3: Graph Tools + Code Analysis — COMPLETE
- [x] tests/conftest.py — Shared fixtures and helpers extracted from test_tools_basic.py
- [x] tests/test_graph_tools.py — 14 tests: find_symbol, find_callers, find_callees
- [x] tests/test_analyze_tool.py — 5 tests: analyze tool with path filtering
- [x] tests/test_impact_tool.py — 6 tests: impact analysis with transitive callers
- [x] server.py — 5 new MCP tools: `find_symbol`, `find_callers`, `find_callees`, `analyze`, `impact`
- [x] Shared helpers: `_require_indexed`, `_serialize_node`, `_serialize_relationship`, `_resolve_symbol`
- [x] Tests: 25 new tests — 209 total, all passing in ~12s
- [x] ruff clean, Snyk scan clean (0 issues)
- [x] ADR-010: Graph Tools API Design
- [x] Post-phase review complete
## Phase 4: Hybrid Search + Memory — COMPLETE
- [x] engines/bm25_engine.py — LanceDB Tantivy FTS wrapper
- [x] engines/fusion.py — Reciprocal Rank Fusion with multi-engine dedup
- [x] engines/reranker.py — FlashRank two-stage reranker (optional dep). Removed on 2026-10-26: it lowered hit@1 (see CHANGELOG).
- [x] memory/memory_store.py — LanceDB-backed semantic memory with TTL
- [x] formatting/token_budget.py — Token estimation and verbosity levels
- [x] formatting/response_builder.py — Structured response building
- [x] persistence/store.py — SQLite graph persistence for warm-start
- [x] server.py — 4 new tools: `explain`, `remember`, `recall`, `forget` (12 total)
- [x] Tests: 113 new tests — 322 total, all passing
- [x] ruff clean

## Phase 5: Hardening + Ship — COMPLETE
- [x] Input validation — path traversal, null bytes, symbol name length, query length
- [x] Graceful shutdown — SIGTERM/SIGINT handlers, graph persistence on shutdown
- [x] Corrupt index detection — metadata + schema validation, auto-rebuild
- [x] JSON structured logging — `NEXUS_LOG_FORMAT=json` via stdlib JsonFormatter
- [x] Memory monitoring — RSS via resource.getrusage in `status` tool
- [x] PyPI packaging — classifiers, keywords, URLs in pyproject.toml
- [x] README rewrite — all 15 tools, full config table, architecture section
- [x] ADR-011: Hardening decisions
- [x] Tests: 35 new tests (security, e2e, performance, memory) — 357 total, all passing
- [x] ruff clean

## Phase 6: MCP Production Readiness — COMPLETE
- [x] 6a: trust_remote_code mitigation — `config.py` defaults `trust_remote_code=False`, `embedding_service.py` respects it
- [x] 6b: Tool permission model — READ/MUTATE/WRITE categories in `security/permissions.py`
  - Static registry maps all 15 tools to categories
  - PermissionPolicy with allowed/denied categories and per-tool overrides
  - Preset policies: DEFAULT_POLICY (read-only), FULL_ACCESS_POLICY
  - `NEXUS_PERMISSION_LEVEL=full` default for backward compat; `read` for restricted
- [x] ~~6c: Pydantic v2 strict input/output schemas — `schemas/inputs.py`, `schemas/responses.py`~~
  **Removed 2026-07-02 (ADR-016)**: never wired into any tool at runtime (tools kept
  using inline `_validate_*` helpers + hand-built dicts); deleted as drift-prone dead
  code rather than left in place unused.
- [x] 6d: Audit logging with correlation IDs — `middleware/audit.py`
  - AuditLogger with structured AuditRecord dataclass
  - Correlation IDs via UUID4 (12-char hex)
  - Sensitive field redaction (token, password, secret, api_key, credential)
  - Long param truncation (>500 chars)
  - Configurable via `NEXUS_AUDIT_ENABLED`, `NEXUS_AUDIT_LOG_FILE`
- [x] 6e: Token bucket rate limiting — `security/rate_limiter.py`
  - Per-tool rate/burst overrides (e.g., index: 0.1/s burst 2, search: 10/s burst 20)
  - Thread-safe with Lock
  - Off by default (`NEXUS_RATE_LIMIT_ENABLED=false`) for stdio transport
  - Configurable via `NEXUS_RATE_LIMIT_DEFAULT_RATE`, `NEXUS_RATE_LIMIT_DEFAULT_BURST`
- [x] 6f: Health endpoint + new exception types
  - `health` tool in server.py: uptime, engine readiness
  - New exceptions: AuthenticationError, AuthorizationError, RateLimitError
- [x] 6g: OAuth 2.1 — deferred until HTTP transport needed (`auth_mode=oauth` reserved)
- [x] New config settings: trust_remote_code, auth_mode, default_permission_level, audit_enabled, audit_log_file, rate_limit_enabled, rate_limit_default_rate, rate_limit_default_burst
- [x] Tests: 84 new tests — 441 total, all passing
- [x] ruff clean, Snyk scan clean
- [x] ADR-012: Tool Permission Model
- [x] ~~ADR-013: Pydantic Schemas~~ — superseded by ADR-016 (2026-07-02, schemas deleted)
- [x] ADR-014: Rate Limiting

## Self-Test Demo — COMPLETE
- [x] self_test/demo_mcp.py — End-to-end demo exercising all 15 MCP tools directly (bypasses transport)
- [x] self_test/README.md — Usage guide, sample project description, troubleshooting
- [x] 26/26 checks passing against built-in sample project
- [x] Supports user-provided codebase path or auto-generated temp project
- [x] Optional `rich` integration for colorized table output

## Phase 7: Distribution & Launch — COMPLETE
- [x] GitHub repository optimization — description, 20 topics, social preview image
- [x] AI discoverability files — llms.txt, llms-full.txt, CITATION.cff, smithery.yaml
- [x] PyPI publication — `pip install nexus-mcp-ci` via GitHub Actions trusted publishing
- [x] GitHub Actions CI/CD — `.github/workflows/publish.yml` (auto-publish on release)
- [x] GitHub release v1.0.1
- [x] awesome-mcp-servers PR submitted (punkpeye/awesome-mcp-servers #3152)
- [x] Launch posts drafted — HN, Reddit (x3), Twitter/X, Dev.to, LinkedIn
- [x] Website/resume copy prepared
- [x] pyproject.toml keywords expanded (15 keywords)
- [x] SEO/GEO strategy documented (docs-pre/SEO_GEO_STRATEGY.md)

## Phase 8: Advanced Intelligence & Visualization — SUPERSEDED
- [x] 8a: Ripgrep fallback for search reliability (100% coverage)
- [ ] ~~8b: Visual graph export (Mermaid.js)~~ — was falsely marked complete; never implemented. See [docs/BACKLOG.md](docs/BACKLOG.md) for viability verdict.
- [ ] ~~8c: Global semantic memory (cross-repository)~~ — reframed, see [docs/BACKLOG.md](docs/BACKLOG.md)
- [ ] ~~8d: Dynamic awareness (Log ingestion/linking)~~ — deferred, see [docs/BACKLOG.md](docs/BACKLOG.md)

Superseded 2026-07-02 by [docs/ROADMAP-2026.md](docs/ROADMAP-2026.md), which sets the
current P0/P1/P2 priorities (P0/P1 complete — see ADR-015, ADR-016, ADR-017 below).
Remaining 8b/8c/8d items were evaluated against the v2.0.0 architecture; see
[docs/BACKLOG.md](docs/BACKLOG.md) for per-item verdicts.

## Phase 9: Token-Efficiency Benchmark (ROADMAP-2026 P2 item 9) — HARNESS BUILT
- [x] `benchmarks/` harness: nexus-mcp vs. baseline Claude Code on 1,000+ file repos
  (django ~2,900 files, home-assistant/core ~26,000 files; 18 hand-verified tasks
  across conceptual/impact/architecture/needle categories)
- [x] Metrics: wasted-read ratio, tokens-to-answer, cost/task (`benchmarks/scoring.py`,
  `benchmarks/transcript.py`)
- [x] Runner + report generator (`benchmarks/runner.py`, `benchmarks/report.py`);
  128 unit tests on the pure parsing/scoring/aggregation logic, `pytest`/`ruff` clean
- [x] ADR-018 (benchmark methodology); code-reviewer pass (ITERATE → all 5 findings
  fixed: rep-count-from-max, incremental writes + per-run error capture, token
  totals None on crashed runs, `group_by` test, FastMCP-internals note)
- [x] Triple-agent audit (code-review + testing + docs, run in parallel):
  code-review ITERATE fixed (HIGH: `.nexus` index was landing outside the repo
  dir it was built for, so every nexus-condition run silently re-indexed from
  scratch inside its own budget/timeout window — same bug transitively broke
  `setup_repos.sh`'s idempotency skip-check; MEDIUM/LOW: timeout now kills the
  whole process group instead of orphaning `claude`'s children,
  `report.load_records` skips malformed JSONL lines instead of aborting);
  testing gaps closed (`run_once` now has direct baseline/nexus/timeout tests
  instead of only being exercised through mocks, plus `_preindex_one.py`,
  both CLI `main()`s, and several previously-untested public helpers); docs
  gaps closed (README: full task-suite YAML schema, `retrieval_tokens_est`
  metric, named default model `claude-sonnet-5`; docstrings on
  `group_by`/`main`/`load_task_suite`/`repo_dir_for`/`normalize_path`'s
  precondition). 128 harness tests total, full suite 585 passing, ruff clean.
- [ ] Live run against real repos + published report — requires `bash
  benchmarks/setup_repos.sh` (clones ~2 large repos) and headless `claude` runs
  (now `--permission-mode dontAsk` plus an allowlist, no bypass flag), plus usage
  or spend (~$2 smoke, ~$20-50 full run). Not run yet — needs explicit user go-ahead.
  See `benchmarks/README.md` for the exact commands.

## Routing Evals, Embedding Eval, Call Edges — started 2026-10-02
Plan: `~/.claude/plans/go-through-the-docs-glistening-sketch.md` (phases A-F).
- [x] Phase A code: `evals/routing/` (prompts, scoring, runner, report), `shop_repo`
  fixture, benchmark harness moved from bypass flag to `dontAsk` + allowlist, new
  `mcp-only` condition, Tool Search on/off switch.
- [ ] Phase A live run 1 — blocked: the isolated config dir has no login. Needs
  `claude setup-token` and `CLAUDE_CODE_OAUTH_TOKEN` in the shell.
- [x] Phase D (done before B so the eval can run first on the unchanged descriptions):
  `CALLS` edges from `indexing/call_resolver.py`, ADR-019, docs and site updated,
  `self_test/demo_mcp.py` now fails on an empty call graph.
- [x] Phase C code (2026-10-03): `evals/retrieval/` (metrics, queries, candidates, runner),
  model name stored in index metadata with automatic rebuild on a change, `onnx_file`
  registry key, `max_seq_length` applied, one `model_dimensions()` helper, packaging
  defaults set to `bge-small-en` (issue #7), `_has_optimum` guard fixed (issue #9).
- [x] Phase C live comparison (2026-10-03): 3 ONNX int8 candidates against the shipped model.
  Result: keep `bge-small-en`; no candidate cleared the 5-point bar and all used more
  memory. Findings on fusion and memory are in
  [docs/research/embedding-models-2026-10.md](docs/research/embedding-models-2026-10.md).
- [x] Phase F: [docs/BACKLOG.md](docs/BACKLOG.md) replaces `todo.md`.
- [x] Phase B (2026-10-03): server `instructions`, rewritten descriptions, enums, annotations,
  one registration name, plugin and Codex docs, `docs/AGENT_ROUTING.md`, tool contract tests
  (`tests/test_tool_contract.py`).
- [x] Routing eval live runs (2026-10-05): pass rate 56% -> 88%, nexus tool called first 27% -> 86%
  (25 prompts, `mcp-only`, Tool Search on, one run each). See docs/EVALS.md.
- [ ] Phase E: benchmark smoke run, then the full run (needs a second go-ahead).

## Adopter Evaluation — 2026-11-03 (PRs #14 to #19, merged; released in 2.2.0)
- [x] PR 1 (#14): `nexus-mcp-ci eval` quick tier (free, offline), `data where|clear|export-memories`,
  `NEXUS_OFFLINE`, no-network test. `--example` is the Nexus-MCP source: "Likely fit",
  1,234 against 5,385 median tokens. Real repos: Chromium `base/` (3,638 files) "Strong fit",
  950 against 17,998; pallets/click (91 files, URL path) "Strong fit", 1,301 against 24,774.
- [x] PR 2 (#15): `eval --agent` (Claude, Codex experimental), presets, cost gate, `eval doctor`.
- [x] PR 3 (#16): `benchmarks/published/results.json` drives every published number; CI `--check`.
- [x] PR 4 (#17): `eval share`, issue form, `community-results.yml`; security review fixed.
- [x] PR 5: evaluate-first calls to action, plugin skill and agent, `server.json`, docs index and pages.
- [x] First paid agent-tier runs (`claude:haiku`, 2 tasks, about $0.02 to $0.03 a run). Too few
  tasks for a cost number.
- [ ] A larger paid run (6 tasks, Haiku and Sonnet) for a publishable agent-tier number.
- [x] Labels `eval-result` and `eval-accepted`; Actions may open PRs; repo description, homepage
  and topics set (2026-11-03).
- [x] All six PRs merged to `main` (2026-11-03). Release 2.2.0 (#20) publishes to PyPI and the
  MCP Registry.
- [ ] Owner steps after the release: re-claim on Glama; submit to Context7; open PRs to the
  awesome-mcp-servers lists; delete the six merged branches.
- [x] `check_docs.py` and the offline lychee link check in CI; CONFIGURATION.md (generated),
  FAQ.md, RELEASING.md. CI runs on every PR, and all five PRs are green.
- [x] Site data-flow SVG (PR 5). PR 6: `benchmarks/runner.py --repo-path/--generate`,
  `evals/retrieval --root --generate`.
- Decided 2026-11-03: no `storage` field in `status`. It cost +23 tokens per call (175 → 198)
  and lowered nothing, so the north-star rule drops it. `nexus-mcp-ci data where` and
  docs/PRIVACY.md answer "where is my data?" instead.

## GitNexus Comparison on Chromium `base/` — 2026-11-04 (branch worktree-bench-gitnexus-chromium)
- [x] `benchmarks/tasks/chromium-base.yaml` (8 C++ tasks) and a temporary `gitnexus` condition.
- [x] C++ parser fixes: declarator names, `.h` parsed as C++, `::` lookups, export macros,
  and no regex backtracking in the macro mask (ac813e4).
- [x] Published: Grep and Read 62,450 tokens / $0.057; Nexus-MCP 82,900 / $0.062; GitNexus
  1.6.12 164,192 / $0.085 (medians per task, 24 of 24 correct each), with fairness notes.
- Decided 2026-11-04 (owner): remove all GitNexus tooling from the repo (the condition, its
  MCP config, the setup scripts and their tests). Keep the published numbers as
  documentation and keep the general benchmark, which runs on any repo. The "Fair
  comparisons" list in `benchmarks/README.md` says how a fair comparison is run.
- [ ] Push the branch (owner).
- [ ] TODO: trim the fixed overhead on each turn. Nexus adds about 3.7k tokens to each call
  (tool schemas 2,320, server instructions 444, skill about 410). At equal turns this is the
  whole Chromium gap. The agent used only `find_symbol`, `graph`, `search` and `map`;
  `status`, `index`, `analyze` and `memory` cost about 1,060 tokens on each turn. Make the
  descriptions shorter and remove the text that the instructions and the skill both say.
  It helps every repo and language. Gate: routing eval (`evals/routing`) and hit@1.
- [ ] TODO: give one absolute `root` in the `find_symbol`, `graph` and `search` responses
  (about 20 tokens). All 9 failed Reads in the 72 Chromium runs dropped the leading `base/`
  of a relative path; nexus had 6 of them (`core_api.py:651` strips `absolute_path`). It
  helps where the repo folder and its top folder have similar names (`django/django/`) or
  the working folder is not the index root.
- [ ] TODO: measure both fixes on Chromium (C++) and Django (Python) with a nexus-only rerun,
  and record before and after in CHANGELOG.md.

## Discoverability & Setup Audit — 2026-09-23
- [x] Audited agent-facing surfaces (tool descriptions, README, `llms.txt`,
  `llms-full.txt`, `docs/USAGE_GUIDE.md`, plugin skill, marketplace manifests)
  against the registered 10-tool surface from ADR-017. No code changed.
- [x] 7 issues filed, each with file:line evidence:
  - [#4](https://github.com/jaggernaut007/nexus-mcp/issues/4) stale pre-2.0.0 tool names in agent-facing docs and AI manifests
  - [#5](https://github.com/jaggernaut007/nexus-mcp/issues/5) no server-level MCP `instructions`
  - [#6](https://github.com/jaggernaut007/nexus-mcp/issues/6) `mcp__nexus-mcp__*` prefix matches no single documented `claude mcp add` line
  - [#7](https://github.com/jaggernaut007/nexus-mcp/issues/7) `glama.json`/`smithery.yaml` default `jina-code`, code default is `bge-small-en`
  - [#8](https://github.com/jaggernaut007/nexus-mcp/issues/8) bundled Claude Code plugin + routing skill undocumented
  - [#9](https://github.com/jaggernaut007/nexus-mcp/issues/9) `_has_optimum` imports the wrong module — 3 slow tests fail on plain `pytest`
  - [#10](https://github.com/jaggernaut007/nexus-mcp/issues/10) no tool-selection eval; the routing claim of ADR-017 is unmeasured
- [x] Reproduced #9: `.venv/bin/python -m pytest -q` → 3 failed, 598 passed, 1 skipped
  (`ModuleNotFoundError: No module named 'optimum.onnxruntime'`). CI hides it via
  `-m "not slow"` in `.github/workflows/ci.yml`.
- [x] `ruff check .` clean at time of audit.

## Documentation Currency Audit — 2026-09-29
- [x] Audited live docs against the code with nexus (`search`, `graph`, `explain`,
  `map`) plus `pytest --collect-only`, `config.py` and `server.py`.
- [x] Fixed stale content in `README.md`, `CLAUDE.md`, `AGENTS.md`, `llms.txt`,
  `docs/{USAGE_GUIDE,DEVELOPER_GUIDE,PROJECT_INFO,ARCHITECTURE,INSTALLATION}.md`,
  `self_test/README.md` and the plugin skill: pre-2.0.0 tool names (15 tools),
  test counts (607 now), default model (`bge-small-en`), `core_api.py` layer, the
  `_pipeline` lock location, ADR-018, env vars, MIT/"all rights reserved" text.
- [x] Added `scripts/build_llms_full.py`; `llms-full.txt` is now generated from the
  source docs instead of hand-copied. `embedding_service.py` docstring named the wrong default.
- [x] Historical docs left as written: `docs/adr/`, `IMPLEMENTATION_PLAN.md`,
  `RESEARCH.md`, `ROADMAP-2026.md`, `FUTURE_CONTRIBUTIONS.md`, `docs/plans/`, `CHANGELOG.md`.
- [x] **Code gap found:** nothing created `CALLS` edges. Fixed 2026-10-02 (ADR-019).
  Cyclomatic complexity and Python docstrings on ast-grep nodes added 2026-10-03.
- [ ] Not changed (behavior, not docs): `Dockerfile`, `smithery.yaml`, `glama.json`
  default to `jina-code` while the package defaults to `bge-small-en` (issue #7).

## Recent Decisions
| Date | Decision | Rationale | ADR |
|------|----------|-----------|-----|
| 2026-03-11 | Single MCP consolidation | Halve memory, single connection | [ADR-001](docs/adr/ADR-001-single-mcp-consolidation.md) |
| 2026-03-11 | LanceDB over ChromaDB | mmap disk-backed, native FTS | [ADR-002](docs/adr/ADR-002-lancedb-over-chromadb.md) |
| 2026-03-11 | ONNX Runtime over PyTorch | 50MB vs 500MB RAM | [ADR-003](docs/adr/ADR-003-onnx-runtime-over-pytorch.md) |
| 2026-03-11 | bge-small-en default | 10x smaller download | [ADR-004](docs/adr/ADR-004-bge-small-default-model.md) |
| 2026-03-11 | Dual parser (tree-sitter + ast-grep) | Best of both worlds | [ADR-005](docs/adr/ADR-005-dual-parser-strategy.md) |
| 2026-03-11 | rustworkx graph engine | Rust-backed, no DB server | [ADR-006](docs/adr/ADR-006-rustworkx-graph-engine.md) |
| 2026-03-11 | LanceDB schema design | PyArrow schema, flat search, 12 columns | [ADR-007](docs/adr/ADR-007-lancedb-schema-design.md) |
| 2026-03-11 | Symbol-based chunking | Chunk per symbol, deterministic IDs | [ADR-008](docs/adr/ADR-008-code-chunk-strategy.md) |
| 2026-03-11 | Pipeline architecture | 8-step flow, dual parser, incremental reindex | [ADR-009](docs/adr/ADR-009-indexing-pipeline-architecture.md) |
| 2026-03-12 | Graph tools API design | Serialization, ambiguity handling, path filtering | [ADR-010](docs/adr/ADR-010-graph-tools-api-design.md) |
| 2026-03-12 | Tool permission model | READ/MUTATE/WRITE categories, transport-aware | [ADR-012](docs/adr/ADR-012-tool-permission-model.md) |
| 2026-03-12 | ~~Pydantic v2 schemas~~ | Strict I/O validation, FastMCP-compatible — **superseded 2026-07-02, see ADR-016** | [ADR-013](docs/adr/ADR-013-pydantic-schemas.md) |
| 2026-03-12 | Token bucket rate limiting | Per-tool rates, off by default for stdio | [ADR-014](docs/adr/ADR-014-rate-limiting.md) |
| 2026-04-17 | Phase 8 Expansion | Compete with ripgrep/Cursor/AppMap | N/A |
| 2026-07-02 | Auto-watch + staleness detection | Daily-driver trust: index never silently goes stale | [ADR-015](docs/adr/ADR-015-auto-watch-and-staleness-detection.md) |
| 2026-07-02 | Remove unused Pydantic schemas | Dead code never wired into any tool at runtime | [ADR-016](docs/adr/ADR-016-remove-unused-pydantic-schemas.md) |
| 2026-07-02 | Tool consolidation 15→10 | Fewer richer tools route better under Tool Search | [ADR-017](docs/adr/ADR-017-tool-consolidation.md) |
| 2026-07-03 | Token-efficiency benchmark | Drive real `claude` CLI; wasted-read ratio + tokens-to-answer; honest, reproducible | [ADR-018](docs/adr/ADR-018-token-efficiency-benchmark.md) |
| 2026-10-02 | Static call-edge resolution | Empty `graph()` hid the main value claim; resolve names after the last batch, skip ambiguous names | [ADR-019](docs/adr/ADR-019-call-edge-resolution.md) |
