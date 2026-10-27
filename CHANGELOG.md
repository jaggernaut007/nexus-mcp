# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- **Live benchmark on django, 2026-10-27, and the routing eval for 8 tools.** On django (12
  questions, 3 runs each) nexus costs **8.9% more** than Grep and Read ($0.973 against $0.894)
  with 16% fewer turns (78 against 93) and level accuracy (35 of 36 against 34 of 36). So the
  saving of the jobscout run is not general: nexus wins on questions that need 18 to 24 turns of
  grep and read and loses on short lookups. The cause is not analysed yet (see the backlog).
  One django ground truth was corrected after the run (a question asked for a value, and the
  scorer also required a file name); the runs were rescored from their stored answers. The
  routing eval, 25 prompts with 3 runs each, passes in 97% of runs (88% on 2026-10-05 with 10
  tools). See `docs/EVALS.md`.
- **Live benchmark, 2026-10-08.** 12 jobscout questions, 72 runs, Claude Sonnet, warm prompt
  cache: nexus costs 19.7% less than Grep and Read ($0.459 against $0.571), with 38% fewer turns
  (34 against 55) and 25% fewer output tokens, and 36 of 36 answers correct in both conditions.
  The same questions on 2026-10-07 gave nexus 6% more cost. The change comes from fewer turns:
  exact names in `search`, the code in `find_symbol`, 8 tools instead of 10, module chunks, and
  the warm-up call that takes the prompt cache write out of the first run. See `docs/EVALS.md`.
- **Fewer turns for an agent.** Each turn of an agent reads the whole prompt again, so a
  follow-up grep or file read costs more than its result. Three changes remove such calls:
  - `find_symbol` returns the `code` of each symbol (the whole symbol, without its common
    indent; 6,000 characters at most for one symbol and 12,000 for one call). It takes
    several names in one call (`"should_keep, AUTO_APPLY_CATEGORIES"`), finds constants
    and other names that have no graph node, and lists names with no match in `not_found`.
  - A `search` query can hold exact names (`snake_case` or `CamelCase`). Their
    definitions come first; a file that defines more of the names comes before the
    others. hit@1: `flask` 0.59 to 0.64, the other three suites unchanged. Other fusion
    weights, another RRF constant and the reranker each lowered a suite, so they stay.
  - A module result below the top 3 shows its `Defines:` line.
- **Caps.** `graph` with `detail="full"` shows 25 symbols with all fields and says how
  many there are (one call returned 20,000 characters). `explain` no longer carries the
  complexity report of the whole project, raw relationship records or raw search rows; it
  gives the code of the symbol, its callers and callees by name and related code by name.
- `python -m benchmarks.runner --warm-up` sends one unrecorded call for a condition when
  its prompt cache is cold. Before, the first task of a batch paid the cache write of the
  prompt (about $0.06) and the cost of a task depended on its place in the batch.
- **Module chunks.** A file that starts with a docstring (Python) or a comment block gets
  one more chunk: that text and a `Defines:` line with its top-level classes and functions.
  `search` returns it with `symbol_type: "module"`. Before, a file that only its top
  docstring describes was not found by a search by concept. hit@1 in production mode:
  `jobscout` 0.70 to 0.81, `nexus_mcp` 0.72 to 0.80, `shop_repo` 0.38 to 0.50, `flask`
  0.59 (no change). A licence header and a text of less than 20 characters give no chunk.
  The index records `chunk_format`; an index of an older version is rebuilt on the next
  `index` call.
- Benchmark scoring accepts a short path (`gateway/gateway.py` or `gateway.py`) as naming a
  file, and a task can list more than one valid answer. `python -m benchmarks.rescore` rescores
  recorded runs from their stored answers, with no Claude call. The first jobscout run went
  from 33/36 to 36/36 correct: all three misses were scoring or ground-truth problems.
- **`CALLS` edges.** `graph()` (callers, callees, `transitive=True`) and the
  `callers`/`callees` fields of `explain()` now return real results for Python,
  JavaScript, TypeScript, Go, Java and Rust. Edges are static and name-based, so
  results are a lower bound. See ADR-019.
- `evals/routing`: a tool-routing eval that measures whether Claude Code calls the
  nexus tools unprompted, with a synthetic `shop_repo` fixture.
- `evals/retrieval`: compares embedding models on file-level search quality
  (docs/research/embedding-models-2026-10.md).
- Server `instructions` sent on connect (issue #5): which tool answers which question.
- Tool annotations (`readOnlyHint` and others) and a title on every tool, and enum
  schemas for `mode`, `direction`, `verbosity`, `detail`, `action` and `ttl`.
- The index records its embedding model and rebuilds when the model changes. An older
  index with a different vector width is also detected and rebuilt. Stored memories are
  re-embedded with the new model; a JSON backup is written first
  (`memories-before-model-change.json`).
- A new server process reattaches to the index on disk (`NEXUS_AUTO_RESTORE`, default on):
  `status` reports `indexed: true` at the start of a session, and the graph tools work, without
  calling `index` first. It is skipped when the stored index is missing, built by another model,
  or its project folder is gone, or the stored root is not the server's working directory or
  inside it (the storage folder is not trusted: it can come from a clone or an archive). The
  `status` tool is now async and starts the file watcher for a restored index.
- The saved graph is loaded on restart, so `graph()` works after a restart without a
  full reindex.
- Cyclomatic complexity and Python docstrings on graph nodes, so `analyze` is no longer
  all zeros.
- `docs/AGENT_ROUTING.md`, `docs/EVALS.md` and `docs/BACKLOG.md` (replaces `todo.md`).

### Changed

- **Fewer model turns in a session.** The benchmark traces showed that a nexus run took 5
  turns against 3 for grep and read, and that every turn costs the whole cached prompt again.
  Four changes remove the extra turns. They are measured offline; a live rerun is still to do.
  - The server `instructions`, the `status` description and the skill no longer ask for a
    `status` call first. Every benchmark run spent one turn on it.
  - Compact `search` gives the whole symbol for the top 3 results (up to 2,000 characters) and
    about 240 characters for the rest. A file read of the top hit followed 9 of 12 searches.
  - A `graph` callers result has a new `references` field: every file and line where the name
    appears as a whole word, with the code of each source line, and a line count for each test
    file. A test file with more than 3 callers shows 3 and `+N more` in the caller lists (a file
    of 40 test functions took 40 entries before) (ripgrep, or grep when ripgrep is missing). The agent ran its own
    grep after `graph` in 5 of 6 impact tasks, because call edges are a lower bound. On django,
    `MaxLengthValidator` has 1 call edge and 18 reference lines in 10 files. A name with no
    graph node now returns its references and not an error.
  - The server attaches the index and loads the embedding model in a background thread at
    start (`NEXUS_WARM_START`, default on). The first `search` of a session waited about 7 s
    for the model; later searches take about 0.1 s. The model memory is then in use from the
    start of each session.
- **Smaller results, same ranking.** Measured on a 460-file project:
  - `find_symbol` has a `detail` parameter. Compact (default) gives the callers and callees by
    name as `file -> ["name:line"]`, not raw relationship records with node ids: 8,400 to
    2,800 characters for a symbol with many callers.
  - Compact `graph` groups callers and callees by file (9,600 to 3,700 characters), and the
    transitive result names each impacted symbol once, in `impacted_files` (12,200 to 6,000).
  - A compact `search` snippet is the code only. The stored chunk text also holds a copy of
    the signature and the docstring and `Imports:` and `Calls:` lines; they are left out, with
    `parent` and `rerank_score`. The tail results get 160 characters, not 240.
  - A compact `search` result below the top 3 shows the signature and the first docstring
    line, with no `(truncated)` mark. Compact results have no `language` field (the file
    extension says it), and the response has only `total`, `results`, `hint` and, when the
    index is stale, `warning`. `detail="full"` keeps `query`, `search_mode`,
    `engines_used` and `language`. On 27 queries that an agent sent in the live benchmark
    the mean result went from 5,089 to 4,311 characters (15% less) with the same result
    order.
- **`search(rerank=...)` is now `False` by default.** With `flashrank` installed, both
  FlashRank models lowered hit@1 on two of four eval suites (`nexus_mcp` 0.72 to 0.44,
  `jobscout` 0.70 to 0.59) and added 0.1 to 3 s to a query. Pass `rerank=True` to use it.
- **`search` ranks source files before test files**, unless the query asks for tests. No
  result is removed. On a 45,744-chunk index of django (70% of the chunks are tests), hit@1 on
  the 12 benchmark task queries went from 6 to 8 and test files in the top 5 from 14 of 60 to 0.
- **The graph list is no longer part of hybrid search by default**
  (`NEXUS_FUSION_WEIGHT_GRAPH` is now `0`, was `0.2`). It raised hit@1 on none of the four
  eval suites and lowered it on three. Hybrid hit@1 without it: `flask` 0.50 to 0.59,
  `nexus_mcp` 0.60 to 0.72, `shop_repo` 0.25 to 0.38, `jobscout` 0.70 unchanged. Set the
  variable above 0 to turn it on again.
- **`search` and `graph` return a compact result by default** (new `detail` parameter,
  `"compact"` or `"full"`). The first live benchmark measured a median 16,000-character `search`
  result and a 46,000-character `graph` result. Compact `search` trims the snippets (see above), and leaves out `id`, `score`,
  `rrf_score`, `_fusion_sources`, `absolute_path` and the fields that repeat the snippet. Compact
  `graph` returns name, type, file and lines, and `transitive=True` lists at most 40 symbols
  (`truncated: true` when there are more; `impacted_files` always names all of them). On a
  460-file project the results are about 40% (`search`) and 20% (transitive `graph`) of the
  full size, with the same files in the same order. Pass `detail="full"` for the old shape.
- `fastmcp` is now `>=3.1,<5` (the new APIs were verified on 4.0.5). New optional extra
  `onnx` (`pip install nexus-mcp-ci[onnx]`) for the ONNX backend on optimum 2.x.
- Memory use is measured and documented (`docs/MEMORY.md`): about 90 MB idle, 460 MB with the
  embedding model loaded. The old "under 350 MB" claim is removed from the docs and the site.
- Calls that the tool schema rejects (for example an invalid enum value) now leave an audit
  record with the status `invalid_arguments`.
- The graph tools check for changed files (throttled) and start a background reindex, as
  `status` and `search` already did.
- Indexing project B over a storage folder that holds project A now rebuilds the index instead of
  diffing against A.
- Memory migration after a model change goes through a temp table, and an interrupted migration is
  finished on the next open.
- Graph relevance in hybrid search keeps a word index (no scan of every node), splits digits and
  joined words (`OAuth2Token` matches `oauth`), and scores at most 1,000 candidates.
- Graph relevance in hybrid search now matches whole identifier words and ignores stop words,
  so short query words no longer match every node whose name contains those letters.
- **Tool descriptions rewritten** (issues #5, #6, #8). In the routing eval (25 prompts, Claude Sonnet,
  Tool Search on) the pass rate went from 56% to 88% and a nexus tool was called first 86% of
  the time instead of 27%. Each description now opens with the question it
  answers, says what comes back and when not to use it. The "preferred over Grep" and "MUST"
  wording is gone. The `memory` action no longer mentions the old tool names.
- **Invalid enum values are now rejected by the tool schema** (a validation error from the
  server) instead of an `{"error": ...}` result. `core_api` still returns the error dict.
- One registration name, `nexus-mcp`, in every doc and on the site. The binary is still
  `nexus-mcp-ci`.
- `Dockerfile`, `smithery.yaml` and `glama.json` default to `bge-small-en` (issue #7).
- `analyze` dead-code entries now say "No static caller found" instead of
  "Never called", and are far fewer now that call edges exist.
- The benchmark harness no longer uses `--permission-mode bypassPermissions`. It
  runs with `dontAsk` and an explicit allowlist.

- `find_symbol`'s keyword parameter was renamed from `name` to `symbol_name`, to
  match `graph()` and `explain()`. Callers that pass `find_symbol(name=...)` must
  switch to `find_symbol(symbol_name=...)`.

### Fixed

- With `flashrank` installed, the default `search` raised `ValueError` (and the result was not
  JSON-serializable): the reranker returned numpy scores. The scores are plain floats now.

### Fixed

A security review and a code review of 2026-10-26 found these in code of this release; all are
fixed before the release, each with a test that fails on the old code.

- **`find_symbol` and `explain` could read a file outside the project.** They read the source
  of a symbol from the path in the index, and a repository can ship its own `.nexus` folder
  (attached by `NEXUS_AUTO_RESTORE`). A crafted path (`/etc/passwd`, a `..` path or a symlink)
  gave the content of that file. Now the real path must be a regular file inside an indexed
  folder and not larger than `NEXUS_MAX_FILE_SIZE_MB`, and at most 1,000 lines are read.
- `find_symbol` gave the whole file as the "code" of the node of a file that is named like
  the symbol (`main` and `main.py`), and used half of the code budget on it. Such a node now
  comes last and has no code.
- `find_symbol` gave the wrong code, with no warning, when a file changed after indexing. The
  code is left out and a `warning` is given when the stored lines do not hold the symbol name.
- Module chunks: a docstring after a licence or pragma comment (`# pylint:`, `# type:`,
  `# noqa`) was lost, and so was a Go or JavaScript package comment after a licence block.
  `chunk_format` is now 3. A fuzzy name with many matches is capped at 10 entries, and a
  leftover code budget below 200 characters gives no stub.
- The server instructions say that results come from the index: read the file before an edit.

### Removed

Rule for every item below: a tool, a parameter or an engine stays only if tasks that use it end
in fewer turns, or if grep cannot do the job. In 12 live tasks the output tokens for each turn
were equal with and without nexus (259 and 266), so reasoning follows turns; and nexus added
4,900 tokens to the prompt of each turn.

- **The `explain` and `health` tools** (10 tools to 8). No agent called either in 64 recorded
  runs. `find_symbol` returns the code of a symbol with its callers and callees. Both stay in the
  Python API (`core_api.explain`, `core_api.health`). Breaking change for MCP clients that call them.
- **Parameters that no agent used**, from the MCP schema (they stay in `core_api`): `search`
  `language`, `symbol_type`, `mode`, `rerank`, `live_grep`, `detail`; `find_symbol` `detail`;
  `graph` `max_depth`, `detail`; `memory` `memory_type`, `ttl`, `project`, `limit`. Tool
  definitions went from 9,818 to 6,682 characters and the skill text from 2,574 to 1,550.
- **The FlashRank reranker** (`rerank`, `engines/reranker.py`, the `reranker` extra, the
  `--reranker` option of `setup.sh`, `NEXUS_RERANKER_MODEL`). hit@1 fell on `nexus_mcp` (0.76
  to 0.52) and `jobscout` (0.81 to 0.78); a query took 2 to 7 s more.
- **The graph list in fusion** (`graph_relevance_search`, `NEXUS_FUSION_WEIGHT_GRAPH`, the word
  index of the graph engine). It never raised hit@1 and lowered it on three suites; it was off
  since 2026-10-06.
- README: the table of token savings was never measured. It is replaced by the 12-task
  measurement, in which nexus cost 6% more than Grep and Read.

### Deprecated

- **`jina-code` embedding model.** It still loads, with a warning, so an existing index keeps
  working. It is no longer offered in `smithery.yaml` or `glama.json` or recommended in the
  docs, and it will be removed in a future major release (it also needs `trust_remote_code`).
  Use `bge-small-en`, then re-index. See the amendment in ADR-004.

### Documentation

- Replaced pre-2.0.0 tool names (`find_callers`, `impact`, `remember`, ...) with
  `graph`, `map` and `memory` in `docs/USAGE_GUIDE.md`, `docs/PROJECT_INFO.md`,
  `llms.txt`, `llms-full.txt` and `self_test/README.md`.
- Corrected the default embedding model (`bge-small-en`), test counts, the
  `core_api.py` layer and lock location, and license wording across the docs.
- Documented that the graph holds no `CALLS` edges yet, so `graph()` and the
  `callers`/`callees` fields of `explain()` return empty results.
- `llms-full.txt` is now generated by `scripts/build_llms_full.py`.

## [2.0.3] - 2026-09-18

### Changed

- **License: All Rights Reserved → PolyForm Noncommercial 1.0.0.** Free to
  use, copy, modify, and distribute for any noncommercial purpose;
  commercial use still requires a separate license. The 2.0.1 license
  change removed the LICENSE file entirely, which broke GitHub's license
  detection and the SPDX `LicenseRef-*` convention it used (a `LicenseRef-`
  identifier is meant to reference accompanying license text that wasn't
  present). Distribution platforms that gate on a detectable license (e.g.
  Glama) could not deploy the server without a LICENSE file. Restored the
  file — now under PolyForm Noncommercial, a recognized SPDX identifier —
  and wired it into the package build via `license-files` in
  `pyproject.toml`. Versions before 2.0.1 remain available under their
  original MIT terms.

## [2.0.2] - 2026-09-18

### Fixed

- `serverInfo.version` reported FastMCP's own library version instead of
  nexus-mcp-ci's actual package version; now correctly reports the installed
  package version.
- Dockerfile: `pip install torch onnxruntime --index-url
  https://download.pytorch.org/whl/cpu` failed because PyTorch's package
  index never hosts `onnxruntime` and `--index-url` fully replaces the
  default PyPI index rather than adding to it. Split into separate installs.
- Restored the `requires-python <3.13` upper bound (dropped in a prior
  release), which lets `uv sync` self-select a compatible Python version
  even when the host environment defaults to a newer, unsupported one
  (`tree-sitter-languages` has no wheel beyond cp312).
- `CITATION.cff` claimed 15 tools; corrected to the current count of 10
  after the earlier tool consolidation.
- `analyze` tool description brought in line with the other tools' usage
  guidance (preferred-over, read-only, requires an index).
- Bumped fastmcp, mcp, cryptography, torch, and several transitive
  dependencies to versions with known CVEs fixed.

## [2.0.1] - 2026-09-18

### Changed

- **License: MIT → All Rights Reserved.** The LICENSE file has been removed; no
  license is granted to use, copy, modify, or distribute this software or any
  part of it without a separate commercial agreement. `pyproject.toml`,
  `CITATION.cff`, and `plugin/.claude-plugin/plugin.json` metadata updated to
  match. Versions published before this release remain available under their
  original MIT terms for anyone who already obtained them.
- Added cross-harness compatibility bridges (Cline, Codex, Antigravity).
- Corrected supported Python range in docs to 3.10-3.12.

## [2.0.0] - 2026-07-02

### Breaking Changes

Tool surface consolidated from 15 tools to 10, to reduce Tool-Search-era description
overhead and follow current MCP tool-design guidance (fewer, richer tools). No
deprecated aliases — this is a clean break. See
[ADR-017](docs/adr/ADR-017-tool-consolidation.md) for the full rationale.

| Old tool | New call |
|---|---|
| `find_callers(symbol_name)` | `graph(symbol_name, direction="callers")` |
| `find_callees(symbol_name)` | `graph(symbol_name, direction="callees")` |
| `impact(symbol_name, max_depth)` | `graph(symbol_name, transitive=True, max_depth=...)` |
| `overview()` | `map(detail="summary")` |
| `architecture()` | `map(detail="architecture")` |
| `remember(content, ...)` | `memory(action="store", content=..., ...)` |
| `recall(query, ...)` | `memory(action="search", query=..., ...)` |
| `forget(memory_id, tags, memory_type)` | `memory(action="delete", memory_id=..., tags=..., memory_type=...)` |

Upgrade: `pip install -U nexus-mcp-ci` (or update the plugin), then update any
`CLAUDE.md`/skill content that hardcodes the old tool names to the mapping above.
Response shapes are unchanged for every merged case — only the tool name and, for
`memory`, the addition of an `action` parameter, changed.

### Added

- **Auto-reindex + staleness detection.** A debounced file watcher
  (`NEXUS_AUTO_WATCH`, default on) keeps the index fresh after edits; `status()`/
  `search()` now surface `stale`/`staleness_warning`/`warning` fields and trigger a
  non-blocking background reindex instead of silently serving stale results.
  ([ADR-015](docs/adr/ADR-015-auto-watch-and-staleness-detection.md))
- **Live indexing progress.** `index()` is now async and streams real MCP progress
  notifications instead of blocking silently for minutes on large repos.
- **Claude Code plugin + skill.** Nexus-MCP can now be installed as a plugin
  (`/plugin marketplace add jaggernaut007/nexus-mcp`) bundling the MCP server
  registration with a routing skill, replacing the old always-loaded `CLAUDE.md`
  block.
- **Known-limitations documentation** for the call graph's static-analysis blind
  spots (dynamic dispatch, closures, reflection) in `CLAUDE.md`/`README.md`.

### Changed

- `multi_index()` is now incremental-aware: it mtime-diffs like `incremental_index()`
  when the indexed root set hasn't changed, instead of always doing a full rebuild.
- `graph()`'s permission category is READ (previously `impact` was categorized
  MUTATE, reflecting computational cost rather than actual data mutation — `graph()`
  never writes anything, so READ is the correct category for the merged tool).
- `memory()`'s permission category is action-aware: `action="search"` is READ,
  `action="store"`/`"delete"` are WRITE — preserving the exact access boundaries the
  three separate tools had before consolidation
  ([ADR-017](docs/adr/ADR-017-tool-consolidation.md)).

### Fixed

- `analyze()` now rejects path traversal (`path="../../../etc"`) with a clear error
  instead of silently returning empty results.
- Dead `schemas/` package (Pydantic models never wired into any tool at runtime)
  removed. ([ADR-016](docs/adr/ADR-016-remove-unused-pydantic-schemas.md))

## [1.0.1] - 2026-04-18

### Added
- **15 Unified MCP Tools**: Complete rollout of the consolidated toolset across search, graph analysis, and semantic memory.
- **Hybrid Search Flow**: Integrated Vector, BM25, and Graph-based relevance with Reciprocal Rank Fusion (RRF) and FlashRank re-ranking.
- **Live Grep Fallback**: New `LiveGrepEngine` providing 100% code coverage fallback using `rg` or standard `grep` for unindexed/new files.
- **Visual Graph Generation**: Ability to export code relationships as Mermaid-compatible diagrams (via `architecture` and `explain` tools).
- **Glama Registry Optimization**:
    - Added `Annotated` types to all tool parameters for rich discovery and high TDQS scores.
    - Implemented `glama.json` build specification for Python 3.12 compatibility.
    - Integrated `mcp-proxy` support for cloud-hosted registry inspection.
- **CPU-Only Docker Build**: Optimized Dockerfile with specialized pip index (`https://download.pytorch.org/whl/cpu`) to eliminate 500MB+ of unnecessary CUDA/GPU libraries.

### Changed
- **Parser Hardening**: Replaced silent exception handlers in `AstGrepParser` and `FileWatcher` with detailed debug logging to resolve Bandit B110/B112 findings.
- **Tech Stack Refresh**: Switched to `jina-code` as the default embedding model (768d) for better code-specific semantic performance.
- **Architecture Documentation**: Synchronized the full documentation suite (ARCHITECTURE.md, PROJECT_INFO.md) to reflect the 15-tool system and 14 ADRs.

### Fixed
- **Glama Build Failures**: Resolved Python version mismatch (forced 3.12) and `spawn ENOENT` errors by correctly configuring `uv run` and PATH injection.
- **Memory Management**: Enforced strict <350MB RAM budget by unloading models after indexing and using lazy-loading for heavy dependencies.

### Security
- **Bandit Audit**: Achieved 0 issues status across the entire codebase.
- **Input Validation**: Hardened all tool entry points with strict parameter validation via FastMCP/Pydantic.

---
*Note: This release marks the transition of Nexus-MCP from an experimental consolidation to an industrial-grade coding intelligence server.*
