# Nexus-MCP Research Notes

Consolidated research findings from project planning. Use as reference during implementation.

---

## 1. LanceDB

### What it is
Serverless, embedded vector database with native full-text search. Rust core, Python SDK. Disk-backed via mmap — vectors stay on disk, only accessed pages loaded into RAM.

### API Reference (replacing ChromaDB)

| Operation | ChromaDB (old) | LanceDB (new) |
|-----------|----------------|---------------|
| Connect | `chromadb.PersistentClient(path)` | `lancedb.connect(path)` |
| Create table | `client.get_or_create_collection(name)` | `db.create_table(name, schema)` / `db.open_table(name)` |
| Add rows | `collection.add(ids, embeddings, docs, metas)` | `table.add([{id, vector, text, ...}])` |
| Search | `collection.query(query_embeddings, n)` | `table.search(vector).limit(n).to_list()` |
| Filter | `where={"lang": "python"}` | `.where("language = 'python'")` (SQL-like) |
| Delete | `collection.delete(where={...})` | `table.delete("filepath = '...'")` |
| Upsert | `collection.upsert(...)` | `table.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(data)` |
| Count | `collection.count()` | `table.count_rows()` |
| FTS | N/A | `table.search("query text", query_type="fts").limit(n).to_list()` |

### Full-Text Search (FTS)
- Two implementations: **Tantivy-based** (older) and **native Rust** (newer, recommended)
- Tantivy limitations: Python async only, filesystem-only, no incremental indexing
- Native FTS: Tested on 41M Wikipedia docs, production-ready
- Create FTS index: `table.create_fts_index("text", replace=True)`

### Known Gotchas
- IVF index creation can hang on 100K+ vectors — use flat search for small codebases
- TypeScript SDK has schema-related bugs (Python SDK is more stable)
- Async API docs are incomplete
- Call `gc.collect()` after closing connections to prevent memory leaks
- Version history can cause storage bloat — compact periodically

### Memory Profile
- Disk-backed with mmap: ~20-50MB RAM for typical codebases
- Peak memory scales with rows being processed, NOT total dataset size
- Production example: 700M vectors on 128GB RAM system

### Sources
- [Scaling LanceDB: 700M vectors in production](https://sprytnyk.dev/posts/running-lancedb-in-production/)
- [LanceDB WikiSearch: Native FTS on 41M docs](https://lancedb.com/blog/feature-full-text-search/)
- [LanceDB Python docs](https://lancedb.github.io/lancedb/)

---

## 2. Embedding Models

### Model Comparison

| Model | Params | Size | Dims | Quality | Code-specific? |
|-------|--------|------|------|---------|---------------|
| `jinaai/jina-embeddings-v2-base-code` **(our default)** | - | ~500MB | 768 | Best for code | Yes |
| `BAAI/bge-small-en-v1.5` | 33.4M | ~50MB (FP16: ~25MB) | 384 | Good | No (general text) |
| `all-MiniLM-L6-v2` (not supported) | 22M | ~80MB | 384 | Good | No |
| `CodeSage-Small` (not supported) | 130M | ~200MB | 1024 | Very good | Yes (9 languages) |

### Why jina-code as default
- Code-specific model with 768-dim embeddings for high-quality code search
- ONNX-compatible for efficient inference
- Best search quality among supported models for code-specific queries
- Users can switch via `NEXUS_EMBEDDING_MODEL=bge-small-en` (smaller, 384d)
- Only registered model names are accepted; custom names raise `ConfigurationError`

### ONNX Runtime Strategy
- Replaces PyTorch (~300-500MB RAM → ~50MB)
- 2.5x faster CPU inference, 7x more requests/sec
- Export: `model.save_pretrained("onnx_model", export=True)`
- INT8 quantization possible: 75% memory reduction, <1% accuracy loss
- BGE-M3 example: 2,272MB → 571MB with ONNX INT8

### Memory Optimization
- FP16: 50% memory reduction with negligible quality loss
- `torch.inference_mode()`: eliminates autograd buffers
- Batch size 32: prevents accumulating all chunks in memory
- Lazy loading: model only in RAM during `index` calls
- Unloading: `del model; gc.collect()` after indexing

### Sources
- [BAAI/bge-small-en-v1.5 - HuggingFace](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [6 Best Code Embedding Models Compared - Modal](https://modal.com/blog/6-best-code-embedding-models-compared)
- [Scaling PyTorch with ONNX Runtime](https://opensource.microsoft.com/blog/2022/04/19/scaling-up-pytorch-inference-serving-billions-of-daily-nlp-inferences-with-onnx-runtime/)

---

## 3. rustworkx (Code Graph)

### What it is
Rust-backed Python graph library. Drop-in replacement for NetworkX, 10-100x faster. Used for storing code relationships (calls, imports, inheritance).

### Why NOT a knowledge graph (Neo4j)
- Code graphs store simple relationships: A calls B, X inherits Y, M imports N
- Neo4j adds a database server dependency — overkill for in-memory code analysis
- rustworkx handles PageRank at 4.9M nodes/sec, betweenness centrality at 104K nodes/sec
- code-graph-mcp already has a production-ready implementation we're porting

### Memory Profile
- Max capacity: 2^32 - 1 nodes and edges (4.3B each)
- 3-10x less memory than NetworkX for same graph
- Estimated 50K nodes: ~50MB with lightweight payloads
- Pre-allocate with `node_count_hint` / `edge_count_hint` to avoid reallocation

### What we're porting (from code-graph-mcp)
- `RustworkxCodeGraph` class with thread-safe RLock
- `PyDiGraph` for directed relationships
- Node types: MODULE, CLASS, FUNCTION, VARIABLE, PARAMETER, etc. (15 types)
- Relationship types: CONTAINS, INHERITS, CALLS, IMPORTS, etc. (10 types)
- Algorithms: PageRank, betweenness centrality, SCC, cycle detection
- Performance indexes by type and language

### Sources
- [rustworkx benchmarks](https://www.rustworkx.org/benchmarks.html)
- [rustworkx paper](https://arxiv.org/pdf/2110.15221)

---

## 4. Memory Optimization for MCP Servers

### Previous Problem
CodeGrok + code-graph-mcp running as two separate MCPs consumed 1-2GB+ RAM:
- PyTorch runtime: ~300-500MB
- CodeRankEmbed model (removed): ~500MB loaded
- ChromaDB in-memory index: variable
- Two Python processes overhead: ~200-400MB

### Target: <350MB single process

| Component | Strategy | Budget |
|-----------|----------|--------|
| Embedding model | jina-code via ONNX (default), GPU/MPS auto | ~67MB |
| LanceDB | mmap (disk-backed) | ~20-50MB |
| rustworkx graph | Lightweight payloads | ~50MB |
| Python + FastMCP | Runtime overhead | ~100MB |
| **Total** | | **~250-350MB** |

### Strategies
1. ONNX Runtime replaces PyTorch (-300-500MB)
2. jina-code default with ONNX inference; bge-small-en as alternative
3. LanceDB mmap — vectors on disk, not in RAM
4. Lazy model loading — only during `index`, not at startup
5. Model unloading — `del model; gc.collect()` after indexing
6. Lightweight graph payloads — `{id, name, type, file, line}` only
7. Batch processing — embed in batches of 32
8. Connection cleanup — close LanceDB after operations
9. Periodic GC — `gc.collect()` every 100 tool calls
10. Memory monitoring — `status` tool reports RSS via `tracemalloc`

### MCP Best Practices
- Close file streams explicitly
- Use context managers for resource cleanup
- Monitor for unbounded cache growth
- Track retained objects preventing GC
- Use `tracemalloc.take_snapshot()` for profiling

### Sources
- [MCP Server Performance Benchmark](https://www.tmdevlab.com/mcp-server-performance-benchmark.html)
- [MCP Server Memory Management](https://fast.io/resources/mcp-server-memory-management/)
- [PyTorch Performance Tuning Guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html)

---

## 5. Dual Parser Strategy

### Why two parsers?
Each parser excels at a different task:

| Parser | Backend | Purpose | Feeds |
|--------|---------|---------|-------|
| **tree-sitter** | C library, grammar packs | Extract code **symbols** with snippets, docstrings, signatures | Vector engine (embeddings) |
| **ast-grep** | Rust library | Build **structural relationships** (calls, imports, inheritance) | Graph engine (rustworkx) |

### tree-sitter (from CodeGrok)
- 9 languages, 28 extensions
- Extracts: name, type, signature, docstring, line ranges, code_snippet
- ThreadLocalParserFactory for parallel parsing
- Max snippet: 4000 chars (~1000-1300 tokens)

### ast-grep (from code-graph-mcp)
- 25+ languages via `ast_grep_py.SgRoot`
- Extracts: function/class definitions, call sites, imports, inheritance
- Builds UniversalNode + UniversalRelationship objects
- Cyclomatic complexity calculation built in

### During indexing, both run:
1. tree-sitter → Symbol objects → chunker → embeddings → LanceDB vector table
2. ast-grep → UniversalNode objects → graph_engine → rustworkx PyDiGraph

---

## 6. code-graph-mcp Analysis (Source Repo)

### Repository
- GitHub: https://github.com/entrepeneur4lyf/code-graph-mcp
- Version: 1.2.3 (Jan 2025)
- Python 3.12+, MIT license
- Already cloned at `code-graph-mcp/` in project

### 9 MCP Tools
1. `get_usage_guide` — usage guidance
2. `analyze_codebase` — full project analysis (10-60s, must run first)
3. `find_definition` — symbol definition lookup (<3s)
4. `find_references` — cross-file reference tracking (1-3s)
5. `find_callers` — call graph analysis (1-2s)
6. `find_callees` — function call detection (1-2s)
7. `complexity_analysis` — cyclomatic complexity + code smells (5-15s)
8. `dependency_analysis` — circular dependency detection (3-10s)
9. `project_statistics` — health metrics (1-3s)

### Key Modules to Port
| Module | Lines | What it does |
|--------|-------|-------------|
| `universal_graph.py` | 318 | Data models: UniversalNode, UniversalRelationship, NodeType (15), RelationshipType (10) |
| `rustworkx_graph.py` | 1500+ | Thread-safe graph: PyDiGraph, PageRank, centrality, SCC, cycle detection |
| `universal_parser.py` | 1000+ | ast-grep parser: 25+ languages, LanguageRegistry, LanguageConfig |
| `universal_ast.py` | 608 | Code analyzer: smells, complexity, dead code, quality metrics |
| `file_watcher.py` | 264 | Debounced watchdog watcher with async support |
| `server.py` | 1000+ | MCP server with UniversalAnalysisEngine |

---

## 7. CodeGrok Analysis (Source Repo)

### Repository
- GitHub: https://github.com/jaggernaut007/CodeGrok_mcp
- Version: 0.2.0 (beta)
- Python 3.10+, MIT license
- Already cloned at `CodeGrok_mcp/` in project

### 8 MCP Tools
1. `learn` — index codebase (auto/full/load_only modes)
2. `get_sources` — semantic search with relevance scoring
3. `get_stats` — indexing statistics
4. `list_supported_languages` — language support info
5. `remember` — store semantic memory with TTL
6. `recall` — retrieve memories via semantic search
7. `forget` — delete memories
8. `memory_stats` — memory statistics

### Key Modules to Port
| Module | Lines | What it does |
|--------|-------|-------------|
| `core/models.py` | 503 | Symbol (frozen), ParsedFile, CodebaseIndex, Memory, MemoryType |
| `parsers/treesitter_parser.py` | 500+ | tree-sitter multi-language parser, 9 languages, 28 extensions |
| `parsers/language_configs.py` | 200+ | Extension→language mapping, AST node types |
| `indexing/embedding_service.py` | 488 | SentenceTransformers singleton, batch processing, GPU detection, LRU cache |
| `indexing/source_retriever.py` | 980+ | ChromaDB vector store, chunking, incremental reindex, file discovery |
| `indexing/memory_retriever.py` | 500 | Semantic memory CRUD, TTL, tag filtering |
| `indexing/parallel_indexer.py` | 200+ | ThreadPoolExecutor, progress tracking |
| `mcp/state.py` | 95 | Session state singleton, IndexingStatus |
| `mcp/server.py` | 774 | FastMCP server, 8 tools, background indexing |

---

## 8. Market Context

### Competitive Landscape (from MARKET_RESEARCH_codegrpk.md)
- 5,000+ MCP servers in ecosystem
- No competitor matches all features: hybrid search + code graph + memory + local-only
- Biggest threat: Continue.dev (open-source, MCP-compatible, larger community)
- Distribution: published on PyPI (`pip install nexus-mcp-ci`), submitted to awesome-mcp-servers

### Unique Selling Points
1. Fully local/private — zero cloud dependency
2. MCP-native — purpose-built for MCP protocol
3. Hybrid search — BM25 + vector + graph (unique combination)
4. Memory layer — persistent semantic memory across sessions
5. No API keys required
6. Explain tool — #1 developer request
7. Change impact analysis — addresses top refactoring pain point
