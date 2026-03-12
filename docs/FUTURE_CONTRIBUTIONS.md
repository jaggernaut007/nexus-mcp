# Future Contributions

> Features and improvements that would make Nexus-MCP faster, smarter, and more capable. Contributions welcome!

## How to Contribute

1. Pick a feature from the list below
2. Open an issue to discuss your approach
3. Fork the repo, implement, and submit a PR
4. Follow the [Developer Guide](DEVELOPER_GUIDE.md) for setup and test standards

---

## 1. Pluggable Embedding Backends

**Priority: High** | **Difficulty: Medium**

Currently Nexus-MCP only supports local ONNX models (bge-small-en, CodeRankEmbed). Add support for external embedding providers so users can trade latency for quality.

**Targets:**
- OpenAI `text-embedding-3-small` / `text-embedding-3-large`
- Anthropic/Voyage `voyage-code-3`
- Ollama (local LLM-hosted embeddings)
- Google Gemini embedding API
- Generic OpenAI-compatible endpoints

**Implementation notes:**
- Add an `EmbeddingProvider` interface in `indexing/embedding_service.py`
- Config via `NEXUS_EMBEDDING_PROVIDER` (local/openai/voyage/ollama)
- Handle API rate limits, retries, and cost tracking
- Batch API calls to minimize round trips
- Fallback to local ONNX if API is unreachable

**Competitors doing this:** Claude Context (Zilliz), Hindsight MCP

---

## 2. Enhanced Tool Dexterity

**Priority: High** | **Difficulty: Medium–Hard**

Make existing tools more flexible and composable.

**Features:**
- **Chained queries**: Let `search` accept follow-up refinement queries (iterative deep search, similar to Sourcegraph Deep Search)
- **Tool composition**: Allow tools to pipe output into each other (e.g., `search` → `impact` → `analyze`)
- **Configurable output formats**: JSON, Markdown, plain text, or token-budgeted summaries per tool
- **Streaming results**: Return partial results as they're found instead of waiting for all engines
- **Wildcard symbol matching**: Support glob patterns in `find_symbol` (e.g., `test_*`, `*Handler`)
- **Batch operations**: Accept arrays of symbols for `find_callers`, `impact`, `explain`
- **Natural language filters**: Let users say "Python functions modified this week" instead of manual filter params

---

## 3. Better Storage Strategies

**Priority: High** | **Difficulty: Medium**

Optimize how indexes are stored, cached, and scaled.

**Features:**
- **Vector quantization**: int8/float16 quantization in LanceDB to cut RAM by 2–4x on large codebases
- **ANN indexes**: IVF-PQ approximate nearest neighbor for codebases with 100k+ chunks
- **Query result caching**: LRU cache for repeated vector/BM25/graph queries with TTL invalidation
- **Tiered indexing**: Shallow mode (file list + symbols only) vs deep mode (full embeddings + graph) — let users choose speed vs depth
- **Merkle-tree change detection**: Content-hash-based incremental indexing instead of mtime-based (more reliable across git operations)
- **Shard splitting**: Auto-split LanceDB tables when they exceed a configurable size threshold
- **Cloud vector DB option**: Optional Zilliz/Pinecone backend for enterprise-scale codebases

**Competitors doing this:** Claude Context (Merkle trees), Zoekt (shard splitting), Zilliz (cloud vectors)

---

## 4. Multi-Repository & Cross-Repo Support

**Priority: High** | **Difficulty: Hard**

Index and search across multiple codebases simultaneously.

**Features:**
- Unified search across all indexed repos
- Cross-repo symbol resolution (e.g., a function in repo A calls a library in repo B)
- Monorepo package-aware navigation
- Per-repo configuration and index isolation
- Shared embedding cache across repos

**Competitors doing this:** Sourcegraph (cross-repo go-to-definition), GitHub MCP Server, Repo Lens MCP

---

## 5. Cross-Service API Linking

**Priority: Medium** | **Difficulty: Hard**

Automatically discover and link REST/gRPC endpoints across microservices.

**Features:**
- Detect route definitions (Flask, FastAPI, Express, Spring, etc.)
- Match HTTP call sites to route handlers with confidence scoring
- Visualize service dependency graph
- Detect breaking API changes across services

**Competitors doing this:** Codebase Memory MCP (DeusData)

---

## 6. Dataflow & Control Flow Analysis

**Priority: Medium** | **Difficulty: Hard**

Go beyond call graphs to understand how data moves through code.

**Features:**
- Source-to-sink taint tracking (security-critical)
- Variable assignment tracing across functions
- Control flow graph construction per function
- Dead code detection via reachability analysis
- Interface-to-implementation resolution for OOP codebases

**Competitors doing this:** Code Pathfinder, CodeQL, Semgrep

---

## 7. Trigram-Accelerated Regex Search

**Priority: Medium** | **Difficulty: Medium**

Add a trigram positional index for sub-50ms regex searches on large codebases.

**Features:**
- Build trigram index alongside existing BM25/vector indexes
- Use trigrams as a pre-filter before running full regex
- Support regex search as a new `mode` in the `search` tool
- Memory-mapped trigram shards for minimal RAM overhead

**Competitors doing this:** Zoekt (Google), Sourcegraph

---

## 8. HTTP/SSE & Streamable HTTP Transport

**Priority: Medium** | **Difficulty: Medium**

Currently stdio-only. Adding HTTP transport unlocks remote usage and team sharing.

**Features:**
- Streamable HTTP transport (the new MCP standard, replacing SSE)
- Remote server deployment (team shares one indexed codebase)
- OAuth 2.1 authentication (already reserved in config)
- WebSocket support for real-time updates
- Multi-client concurrent access

---

## 9. Real-Time File Watching

**Priority: Medium** | **Difficulty: Low**

The file watcher code exists (`parsing/file_watcher.py`) but is not integrated.

**Features:**
- Auto-trigger incremental reindex on file changes
- Debounced batching (don't reindex on every keystroke)
- Configurable watch patterns and ignore rules
- Status notifications when reindex completes

---

## 10. Token Efficiency Metrics & Optimization

**Priority: Medium** | **Difficulty: Low**

Measure and report how many tokens Nexus-MCP saves compared to sending full files.

**Features:**
- Track tokens-sent vs tokens-if-full-files per query
- Report cumulative savings in `status` tool
- Dynamic token budget allocation based on result relevance scores
- Configurable per-tool token limits

**Competitors doing this:** Codebase Memory MCP (99.2% reduction), Claude Context (40% reduction)

---

## 11. Expanded Language & File Type Support

**Priority: Low** | **Difficulty: Low–Medium**

Go beyond source code to index configuration and infrastructure files.

**Features:**
- SQL migrations, stored procedures
- Markdown/documentation files (semantic search over docs)
- JSON/YAML/TOML configuration files
- Dockerfiles, Terraform, CloudFormation (IaC)
- .env templates, CI/CD pipeline definitions
- Target: 50+ file types (currently 25+ languages)

**Competitors doing this:** Code Index MCP (50+ types), Codebase Memory MCP (64 languages)

---

## 12. Security Tool Integration

**Priority: Low** | **Difficulty: Medium**

Integrate with external SAST/SCA tools for security-aware code intelligence.

**Features:**
- SonarQube integration (import findings as graph annotations)
- Semgrep rule results linked to symbols
- Snyk vulnerability data overlaid on dependency graph
- Security hotspot highlighting in `analyze` results

**Competitors doing this:** SonarQube MCP, Snyk Code, Semgrep

---

## 13. CLI Mode (Non-MCP Usage)

**Priority: Low** | **Difficulty: Low**

Allow direct command-line usage without an MCP client.

**Features:**
- `nexus-mcp search "auth middleware" --mode hybrid`
- `nexus-mcp index /path/to/repo`
- `nexus-mcp impact MyClass.my_method`
- JSON or human-readable output
- Shell completion support

**Competitors doing this:** Codebase Memory MCP (single Go binary with CLI)

---

## 14. Background TTL Cleanup & Memory Management

**Priority: Low** | **Difficulty: Low**

Currently expired memories remain in the database until manually cleaned.

**Features:**
- Background thread for periodic TTL enforcement
- Configurable cleanup interval
- Memory usage alerts when approaching limits
- Auto-compaction of LanceDB tables after bulk deletes

---

## 15. Dependency-Aware Code Intelligence

**Priority: Medium** | **Difficulty: Medium–Hard**

Understand how your codebase uses its dependencies — not just what the library API is (that's what tools like Context7 do), but how your project interacts with it.

**Why not just use Context7?**
Context7 fetches up-to-date library documentation. That's valuable for *writing* code. But Nexus-MCP sits at a different layer — it understands *your* code. The opportunity is to bridge the gap: map the relationship between your codebase and its dependencies.

**Features:**
- **Dependency call-site mapping**: Index which functions/methods from external packages are used, and where. Answer "which files import `lancedb.connect`?" or "how many call sites use `fastmcp.tool`?"
- **Deprecated API detection**: Flag call sites using APIs marked deprecated in newer versions of a dependency. Cross-reference with package changelogs or metadata.
- **Upgrade blast radius**: Before bumping a dependency version, show every call site, pattern, and import that might break. Combine with `impact` for transitive analysis.
- **Usage pattern extraction**: Identify the idioms your project uses for a given library (e.g., "we always wrap `httpx.get` in a retry decorator") so AI tools can follow existing conventions.
- **Dependency graph visualization**: Map which modules depend on which external packages, detect heavy coupling to a single dependency, and find candidates for abstraction.
- **Version constraint analysis**: Parse `pyproject.toml` / `package.json` / `go.mod` and correlate pinned versions with known CVEs or available upgrades.
- **Transitive dependency awareness**: Understand indirect dependencies and flag risk (e.g., "module X depends on library Y which pulls in 14 transitive deps").

**Example queries this enables:**
- "Which functions in our codebase use deprecated LanceDB APIs?"
- "What's the blast radius if we upgrade `fastmcp` from v1 to v2?"
- "Show me all the patterns we use for tree-sitter parsing"
- "Which modules are most coupled to `rustworkx`? What would it take to swap it out?"

**Complements, not competes with Context7:** Use Context7 to learn *what* a library offers. Use Nexus-MCP to understand *how your project uses it* and *what breaks if it changes*.

---

## 16. Infrastructure-as-Code Awareness

**Priority: Medium** | **Difficulty: Medium**

Understand cloud infrastructure *as it appears in your code* — not as a cloud console replacement.

**Scope (what Nexus-MCP should do):**
- Index Terraform, Pulumi, CloudFormation, and CDK files as part of the code graph
- Map SDK call sites to cloud services (e.g., `boto3.client('s3')`, `storage.Client()`, `BlobServiceClient`)
- Link hardcoded resource ARNs, URLs, project IDs, and bucket names to the code that references them
- Impact analysis: "if I delete this DynamoDB table, which Lambda handlers break?"
- Detect infrastructure drift between IaC definitions and SDK usage (e.g., a Terraform resource exists but no code references it)
- Cross-reference environment variables (`NEXUS_STORAGE_DIR`, `AWS_REGION`) with where they're consumed

**Out of scope (use dedicated cloud MCP servers instead):**
- Listing live cloud resources, managing deployments, monitoring costs
- AWS MCP, GCP MCP, and Azure MCP already handle this well

**Example queries:**
- "What code touches the `user-uploads` S3 bucket?"
- "Which services depend on this Pub/Sub topic?"
- "Show me all GCP API calls in the payments module"
- "If I rename this Terraform resource, what config files and code references break?"

**Same principle as Context7:** Nexus-MCP understands your code's *relationship* to cloud services. Use cloud MCP servers for live resource management.

---

## 17. Smart Reindexing & Auto-Reindex

**Priority: High** | **Difficulty: Medium**

Currently reindexing is manual (`index` tool) with basic mtime-based change detection. Make it smarter and automatic.

**Automatic reindex triggers:**
- **File watcher integration**: Auto-trigger incremental reindex on file save (code exists in `parsing/file_watcher.py`, needs integration)
- **Git hook reindex**: Reindex after `git pull`, `git checkout`, `git merge`, `git rebase` — the moments when your codebase actually changes significantly
- **CI/CD webhook**: Trigger reindex when a PR merges or a deploy completes
- **Session-start freshness check**: On first tool call, compare index timestamp to latest file mtimes and auto-reindex if stale
- **Configurable auto-reindex policy**: `NEXUS_AUTO_REINDEX=off|on_change|on_session|on_git`

**Smarter change detection:**
- **Git-diff-based reindex**: Use `git diff --name-only` to find exactly which files changed instead of scanning all mtimes — faster and more reliable across branch switches
- **Merkle-tree content hashing**: Hash file contents, not timestamps, so renamed/moved files don't trigger unnecessary re-embedding
- **Dependency-aware reindex**: If `models.py` changes, also re-analyze files that import from it (their call graphs may have changed)
- **Priority reindexing**: Reindex frequently-queried files first, background-reindex the rest

**Reindex efficiency:**
- **Partial graph updates**: Update only affected nodes/edges in the graph engine instead of rebuilding the full graph
- **Embedding cache**: Skip re-embedding chunks whose content hash hasn't changed (even if the file was touched)
- **Debounced batching**: Collect file changes over a configurable window (e.g., 2 seconds) before triggering reindex, avoiding churn during rapid edits
- **Progress reporting**: Show reindex progress via `status` tool — files processed, estimated time remaining, last reindex timestamp

**Staleness indicators:**
- `status` tool reports index age and staleness score
- Warn when search results may be stale (index is >N minutes old and files have changed)
- Optional "reindex recommended" flag in tool responses

---

## Good First Issues

If you're new to the project, these are great starting points:

| Issue | Area | Difficulty |
|-------|------|------------|
| Add token savings tracking to `status` tool | Metrics | Easy |
| Integrate file watcher with indexing pipeline | Indexing | Easy |
| Add CLI mode with argparse | DX | Easy |
| Implement background TTL cleanup for memories | Memory | Easy |
| Add int8 vector quantization option | Storage | Medium |
| Support Ollama as embedding provider | Embeddings | Medium |
| Add regex search mode with trigram pre-filter | Search | Medium |
| Git-diff-based reindex (`git diff --name-only`) | Reindexing | Easy |
| Add staleness score and last-reindex timestamp to `status` | Reindexing | Easy |
| Index Terraform/CloudFormation files as graph nodes | IaC | Medium |
| Map `boto3`/`google-cloud` SDK calls to service names | IaC | Medium |

---

## Architecture Principles

When contributing, keep these principles in mind:

1. **Memory budget**: Stay under 350MB RAM for typical codebases
2. **Lazy loading**: Load models/resources only when needed, unload after
3. **Graceful degradation**: Optional features (reranker, etc.) should fail silently
4. **No breaking changes**: New features should be additive; existing tool signatures stay stable
5. **Test everything**: 441+ tests and counting. Every public function needs tests.
6. **ADRs for decisions**: Document significant architectural choices in `docs/adr/`

---

*Last updated: 2026-03-12*
