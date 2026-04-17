# Project Progress: Nexus-MCP

[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/card.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

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
- [x] engines/reranker.py — FlashRank two-stage reranker (optional dep)
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
- [x] README rewrite — all 12 tools, full config table, architecture section
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
- [x] 6c: Pydantic v2 strict input/output schemas — `schemas/inputs.py`, `schemas/responses.py`
  - Input models: IndexInput, SearchInput, SymbolNameInput, AnalyzeInput, ImpactInput, RememberInput, RecallInput, ForgetInput
  - Response models: StatusResponse, HealthResponse, IndexResponse, SearchResponse, FindSymbolResponse, CallersResponse, CalleesResponse, AnalyzeResponse, ImpactResponse, ExplainResponse, MemoryResponse, RecallResponse, ForgetResponse, ErrorResponse
  - Models used internally for validation; `.model_dump()` for serialization
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
- [x] ADR-013: Pydantic Schemas
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
- [x] GitHub release v0.1.0
- [x] awesome-mcp-servers PR submitted (punkpeye/awesome-mcp-servers #3152)
- [x] Launch posts drafted — HN, Reddit (x3), Twitter/X, Dev.to, LinkedIn
- [x] Website/resume copy prepared
- [x] pyproject.toml keywords expanded (15 keywords)
- [x] SEO/GEO strategy documented (docs-pre/SEO_GEO_STRATEGY.md)

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
| 2026-03-12 | Pydantic v2 schemas | Strict I/O validation, FastMCP-compatible | [ADR-013](docs/adr/ADR-013-pydantic-schemas.md) |
| 2026-03-12 | Token bucket rate limiting | Per-tool rates, off by default for stdio | [ADR-014](docs/adr/ADR-014-rate-limiting.md) |
