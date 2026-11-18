# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added (linked repos, phase 2.3)

- **Routes and HTTP client calls in the graph** (`parsing/http_extractor.py`). A route is
  `metadata["routes"]` on its handler function, a client call is `metadata["http_calls"]` on the
  function that makes it (the module node when there is no function node). Python: FastAPI and
  Starlette-style decorators, Flask, `requests`, `httpx`, `client.<verb>`, `session.<verb>`.
  JavaScript and TypeScript: Express-style routes, NestJS controllers, Next.js route files,
  `fetch`, `axios`, `ky`, `$fetch`. Paths are normalised (`/orders/{}`). No new tool and no new
  parameter: nothing reads the facts until phase 2.4, which connects the repos.
- **`IndexingPipeline.rebuild_graph()`** parses the indexed files again for the graph only, with
  no embedding model. `EXTRACTOR_VERSION` is 2, so `link` and `link --refresh` use it for a
  store of version 1. An incremental reindex keeps the stored version.
- Not covered: Django `path()`, Go, Java, C#, PHP, gRPC, a prefix set in another file.
- **Audit fixes.** A route needs a known router receiver (`api.post("/orders", body)` is a client
  call). `await axios.get<T>(...)` is read. A path of parameters only, a method that is not a
  literal and a NestJS path that is not a literal are left out. `rebuild_graph` refuses a
  read-only pipeline. An incremental reindex does not stamp a store that has no version.
- **North-star gate, no link (2026-11-18, `python -m evals.retrieval.run`, `bge-small-en`).**
  Hybrid hit@1 on the 2.3 checkout: `nexus_mcp` 0.667 (24 queries; 0.708 on 2.2), `shop_repo`
  0.750 (8 queries; 0.750 on 2.2). The `nexus_mcp` suite indexes `src/nexus_mcp`, which now has
  one more file (`http_extractor.py`), so the corpus changed. Control: the 2.3 code on the corpus
  of the 2.2 checkout gives `nexus_mcp` hybrid 0.708, the same as 2.2. So the drop of one query
  comes from the new file in the corpus, not from the code. `shop_repo` hybrid moves between
  0.625 and 0.750 with the checkout path (chunk ids use absolute paths). Not run: the live
  benchmark (needs a login and API money), the flask, language and jobscout suites (clones and a
  private file). Cost of the extractor: one regex test for each file, and one walk of the tree
  for a file with a candidate; the reviewer measured about 14% more ast-grep time on 2,000
  library files before the stricter text test. Tokens with no link: no tool output changes, since
  nothing reads `routes` and `http_calls` before phase 2.4.

### Added (linked repos, phase 2.2)

- **`search` and `find_symbol` look in the linked repos.** The query is embedded one time.
  Each linked store gives vector and BM25 results, which merge by score before the same RRF as
  before. A result of a linked repo has the repo name first in its path (`backend/src/app.py`).
  `find_symbol` looks in the graph of each repo, the primary project first; `detail="full"`
  adds a `repo` field. A stale linked repo gives `changed_since_index` and a warning that names
  `nexus-mcp-ci link --refresh`. No new tool and no new parameter.
- **The BM25 engine of a linked store is read-only** (`IndexingPipeline(read_only=True)`). It
  never creates the full-text index, so a search never writes to a linked store.
- **Audit fixes.** A folder that two stores both hold (a link to a sub-folder of the project)
  gives one result, not two. One failing linked graph does not stop `find_symbol`. The stale
  code warning of `find_symbol` names `nexus-mcp-ci link --refresh` for a linked repo.
- **North-star gate, no link (2026-11-18, `python -m evals.retrieval.run`, `bge-small-en`).**
  The 2.2 code gives the same hit@1 as the 2.1 code in all four modes, on the same paths:
  `nexus_mcp` hybrid 0.708 before and 0.708 after (24 queries), `shop_repo` hybrid 0.625 and
  0.625 (8 queries). Two checkouts at different paths gave `shop_repo` hybrid 0.625 and 0.750:
  chunk ids come from absolute paths, so ties order differently. This is not a code effect.
  Tokens with no link: the code path is the same (no `query_vector`, one list to the merge),
  and the tests show the same output as before. **Not run:** the live benchmark (it needs a
  login and costs API money), the flask suite and the language suites (they need clones of
  `benchmarks/setup_repos.sh`), the private jobscout suite. **Not measured:** a project with
  links. BM25 scores are scaled in each store, so the stores interleave; the result for a
  project with links needs its own eval.

### Added (linked repos, phase 2.1)

- **Repo identity.** Each index stores `repo_id` (made one time, kept through reindexes),
  `repo_name` and `extractor_version` in `index_metadata.json`. An index made before this
  version is built again one time when it is linked.
- **`nexus-mcp-ci link <path> [--name]`, `unlink <name>`, `links` and `link --refresh`.** Link
  other repos to a project. Each repo keeps its own index. The links are in a user-level
  file, `~/.nexus-mcp/links.json` (`NEXUS_LINKS_FILE`), outside every repo. `data where` and
  `data clear --links` know the file. See [ADR-027](docs/adr/ADR-027-linked-repos.md).
- **The server attaches linked stores read-only** at restore and after `index`, and `status`
  lists each linked repo with its file count, stale flag and skip reason. No new tool and no
  new parameter. With no link nothing changes, so the benchmark tokens and hit@1 do not move.
  Search and graph across repos come in phases 2.2 to 2.4.

### Breaking (3.0.0)

- **The default embedding model runs on the CPU with ONNX Runtime.** The default model `bge-small-en` is now the int8
  ONNX file of `Xenova/bge-small-en-v1.5` (32 MB, pinned to a commit). A native encoder
  (`tokenizers` and `onnxruntime`) runs it. `torch`, `sentence-transformers`, `optimum` and
  the CUDA packages are no longer dependencies. See [ADR-026](docs/adr/ADR-026-cpu-only-onnx-embeddings.md).
- **PyTorch is the optional GPU model.** Install `nexus-mcp-ci[torch]` and set
  `NEXUS_EMBEDDING_MODEL=bge-small-en-torch` (fp32 weights). It uses a GPU when one is present
  (CUDA, then Apple MPS) and the CPU if not. On Apple Silicon the MPS GPU is about 2 times
  faster than the default model. Switching between the two models rebuilds the index.
- **The ONNX model is CPU only.** `NEXUS_EMBEDDING_DEVICE` defaults to `auto`. It applies to
  `bge-small-en-torch` only. For `bge-small-en` a GPU value is ignored with a warning. The
  `gpu` extra is removed: `nexus-mcp-ci[torch]` is the GPU install.
- **`jina-code` is removed**, with `trust_remote_code`, `NEXUS_TRUST_REMOTE_CODE`, the `onnx`
  extra and the code that only they used. `NEXUS_EMBEDDING_MODEL=jina-code` now gives an
  error that tells you to use `bge-small-en`.
- **Every existing index rebuilds one time** (chunk format 9). The int8 vectors differ a little
  from the fp32 vectors of 2.x, and a chunk table must not hold both. Stored memories are not
  re-embedded (the memory store checks the vector width only); the difference is small (cosine
  0.993 at the lowest).
- **Offline and air-gapped setups: upgrade note.** The default model comes from another
  Hugging Face repo (`Xenova/bge-small-en-v1.5`, 32 MB) than in 2.x (`BAAI/bge-small-en-v1.5`).
  With `NEXUS_OFFLINE=1`, allow the download once, or copy `~/.cache/huggingface` from a machine
  that has it. The error now names the model and the way out. The 2.x cache is still used by
  `bge-small-en-torch`.
- The ONNX model runs one chunk in each call. With 32 the peak memory of an index was above
  1.3 GB, and the int8 vector of a chunk changed with the other chunks in its batch (cosine
  0.9933). The speed on a CPU does not change.

Before and after (north-star gate; Apple Silicon laptop, CPU, 2026-11-17):

| Measure | 2.4.6 (torch fp32) | 3.0.0 (int8 ONNX) |
|---|---|---|
| Download size of the wheels, macOS arm64 | 336 MB | 157 MB |
| Download size of the wheels, Linux x86_64 | 1,054 MB | 185 MB |
| Model download | 133 MB | 32 MB |
| Peak RSS, `search` (`docs/MEMORY.md`) | 483 MB | 270 MB |
| Peak RSS, index of `src/nexus_mcp` (`docs/MEMORY.md`) | 801 MB | 311 MB |
| Eval run of `src/nexus_mcp`, process peak RSS | 820 MB | 289 MB |
| Eval run of `src/nexus_mcp`, index time | 92.8 s | 71.1 s |
| hit@1 `nexus_mcp`, hybrid (24 queries) | 0.79 | 0.79 |
| hit@1 `shop_repo`, hybrid (8 queries) | 0.62 | 0.62 |
| hit@1 vector only, `nexus_mcp` / `shop_repo` | 0.58 / 0.75 | 0.54 / 0.75 |
| hit@5 hybrid, `nexus_mcp` / `shop_repo` | 0.88 / 1.00 | 0.92 / 1.00 |
| Vector agreement, cosine min / mean | 1 | 0.9934 / 0.9966 |

The `flask` and `jobscout` suites are not run yet, so the accuracy gate is open for them. The
speed of `torch` on Apple Silicon is higher than ONNX Runtime for the same chunks (24.9 against
19.8 chunks/s for int8 in the final run; over several runs 18.8 to 26.7 against 16.5 to 22.3, on
a shared machine). Linux speed is not measured.

### Benchmarks

- **Head to head with four other code tools on Django and Home Assistant.** Nexus-MCP,
  GitNexus 1.6.12, codebase-memory-mcp 0.12.1, Serena 2.1.0 and Graphify 0.9.104. Claude Code
  2.1.319, `claude-sonnet-5-5`, medium effort, 3 runs per task. Published under
  `comparisons` in `benchmarks/published/results.json` (benchmarks page and `llms.txt`); run
  records in `benchmarks/runs/competitors-2026-11-09/`. Medians per task, tokens and cost:

  | Tool | Django (Nexus-MCP 2.4.6, 12 tasks) | Home Assistant (Nexus-MCP 2.4.1, 6 tasks) |
  |---|---|---|
  | Nexus-MCP | 21,660, $0.037, called 32 of 36 | 18,826, $0.020, called 18 of 18 |
  | GitNexus | 59,224, $0.029, called 8 of 36 | 64,503, $0.030, called 7 of 18 |
  | codebase-memory-mcp | 46,220, $0.033, called 2 of 36 | 44,532, $0.019, called 0 of 18 |
  | Serena | 33,552, $0.028, called 9 of 36 | 39,482, $0.021, called 0 of 18 |
  | Graphify | 24,168, $0.026, called 3 of 36 | 20,101, $0.018, called 0 of 18 |

  - All five answered every run correctly.
  - Nexus-MCP used the fewest tokens on 8 of 12 Django tasks and 4 of 6 Home Assistant
    tasks. It cost the least on 1 and 0 tasks: it sends more uncached tokens per run.
  - The agent rarely called the other four tools, so their rows mostly measure Read, Grep
    and Glob.
  - Grep and Read in the same session cost less than every tool (median $0.025 on Django,
    $0.018 on Home Assistant). This is not a north-star win.
  - The Grep and Read side is far cheaper than in the 2.4.1 runs of the same tasks (Home
    Assistant: 96,145 tokens and $0.071 per task then; 16,113 and $0.017 now, means). The
    agent and model changed in between, so the per-repo table needs a rerun.
- `benchmarks/conditions.py`: an `ext:<name>` condition runs another MCP tool from
  `benchmarks/tools/private/<name>/` (`mcp.json`, optional `instructions.md`, optional
  `corpus` for a tool that writes into the repo).
- `scripts/publish_results.py`: a comparison can record `tool_called` per condition, shown as
  a "Tool called" column. `benchmarks/latest_published.py` skips `competitors-*` run folders.
- **Removed the Chromium `base/` head-to-head with GitNexus (2026-11-04) from the published
  data.** It is gone from the benchmarks page, `site/benchmarks.json` and `llms.txt`. The Django
  and Home Assistant comparison replaces it. The Chromium repo row, its offline check and
  `benchmarks/tasks/chromium-base.yaml` stay, and so do the old `CHANGELOG` entries.

## [2.4.6] - 2026-11-08

A docs and listing release. The MCP tools and their behaviour do not change. The PyPI page,
the MCP Registry entry and every channel manifest get the new tagline: "Your coding agent reads
less code to find the right file."

### Fixed

- Site: the hero tabs of C, C# and C++ had the same element id, so they showed no panel or the
  wrong repo. The ids now come from the repo name.
- README: the downloads badge rounded 5,513 to "6k". It now reads the site's count, rounded
  down to one decimal ("5.5k").
- `scripts/publish_results.py` no longer crashes with no live repos or a zero divisor, and
  marks offline cells as win, even or loss. `benchmarks/latest_published.py` stops when two
  live sides from different runs have another setup, or when a run folder is not in `ORDER`.

### Docs

- The README (830 to about 200 lines) and the site lead with one story: the agent reaches the
  right file with fewer tokens. The site has a new design and a hero trace of tokens to the
  right file for each language. The benchmarks page uses the same styles.
- Published numbers: one row per benchmark repo, the latest run of each tier, from
  `benchmarks/runs/` (`python -m benchmarks.latest_published`). 12 repos in 10 languages, up
  from 4. Search time (median ms per lookup) and turns per task are new columns. Each row
  names its build. C and Rust, where Nexus-MCP used more tokens on the 2.2.2 build, are shown.
- `results.json` schema 3: `repos` replaces `languages`. `benchmarks/to_published.py` is
  removed. The 2.1.0 Django run and the 2026-11-03 Chromium `eval` row are replaced by newer
  runs of the same repos.
- New: `docs/COMPARISON.md`, `docs/research/OSS-ADOPTION.md` (adoption research and a growth
  checklist). The technology table moved to `docs/ARCHITECTURE.md`.
- North-star gate: not applicable. No code of the server changed, so token use and hit@1 do
  not change.
- One tagline everywhere: "Your coding agent reads less code to find the right file." It is in
  the PyPI description, `server.json`, `glama.json`, `context7.json`, the Gemini, Claude
  Desktop, Claude Code and Cursor manifests, `CITATION.cff`, `llms.txt` and
  `docs/SUBMISSIONS.md`. The old "27% to 49% fewer tokens" claim (a sum of 18 runs) is
  replaced by the published medians. New `site/og.png` and `site/social-preview.png`.
  Registry and PyPI texts change at the next release.

## [2.4.5] - 2026-11-08

Fixes from the third Cline install test. The 2.4.4 test passed after a manual fix of the
config. The cause of the two earlier failures: this Cline (the Cline CLI, and the Cline that
Antigravity runs) reads `~/.cline/data/settings/cline_mcp_settings.json`. `setup --client cline`
wrote only the file in the VS Code folder, so the entry that it wrote was never read, and the
old `nexus` entry (a local build) kept running. Every command passed, and the server was still
the old one.

### Fixed

- **`setup --client cline` wrote only the VS Code file.** It now also writes
  `~/.cline/data/settings/cline_mcp_settings.json` when that folder exists. That file nests
  the launch under `transport`, and `setup` keeps the keys of the user (`disabled`, `timeout`,
  `autoApprove`), sets `disabled` to `false`, and sets a 300 s timeout (the first search loads
  the embedding model). It writes each Cline install that is there, and `--uninstall` removes
  each entry. `detect_clients` finds Cline by either folder.
- **`setup` printed `keep` when the launch was current but the entry had another shape.** It
  now prints `update ... (same launch, other fields)`.

### Added

- **`llms-install.md`:**
  - how to find the process of a server (`ps`) and the config file that a client reads
    (`lsof`);
  - a rule for success: the server name and the `index` version must be those that were
    installed, even when every command passed;
  - the entries that `setup` writes, in both forms;
  - a warning that `--version` hangs on a build older than 2.4.3;
  - `git check-ignore .nexus`;
  - troubleshooting for an old index read by a newer or older build (`text_idx has version 2`);
  - the tool prefix is the server name (`nexus-mcp__index` in Cline).

### North star

- Tokens: no change. No tool result and no server instruction changes.
- Accuracy: no change to the parser, the index or the search. The live benchmark was not run
  again.

## [2.4.4] - 2026-11-08

Fixes from the second Cline install test (an upgrade from 2.4.2). The 2.4.3 storage fix
passed: a clean 2.4.3 server started in `/` indexed the project into `<project>/.nexus/`. The
test failed because the live tools came from another server, a local build of older source.

### Fixed

- **`setup` printed "add MCP server" when it replaced an older entry.** It now prints
  `replace ... (was <old launch>)`, or `keep ...` when the entry is already current.
- **`setup --client claude-code` with the `claude` CLI kept an older entry.** A second run did
  nothing, so the pinned version did not change. It now removes the old entry and adds the new
  one. A current entry is still left as it is.
- **`index` answered `total_files: 0` and `total_symbols: 0` when no file had changed.** That
  reads as an empty index. It now gives the size of the stored index and `unchanged: true`.

### Added

- **`version` in the `index` result.** `status` is hidden by default, so an agent had no way to
  see which build answers.
- **A log for the background index of `setup`:** `<project>/.nexus/setup-index.log`. Its last
  line says `DONE` or `FAILED`, with the result. `setup` prints the path.
- **`llms-install.md`:** an "Update an existing install" section; the tool prefix gives the
  server name, and a server with another name comes from another config or a local build;
  restart the server in the Cline MCP Servers panel; the `index` path selects the project;
  a troubleshooting section for `[Errno 30] Read-only file system: '/.nexus'`; the
  `.nexus-bak` copy is made only before the first change; Cline does not read
  `.vscode/mcp.json`.

### North star

- Tokens: the `index` result has 5 more tokens (`version`), and 4 more (`unchanged`) only
  when no file changed. `index` runs about once in each session. The server instructions and
  the other tool results do not change.
- Accuracy: no change to the parser, the index or the search. The live benchmark was not run
  again.

## [2.4.3] - 2026-11-08

### Fixed

- **`index` failed in Cline with `[Errno 30] Read-only file system: '/.nexus'`.** Cline starts
  MCP servers with `/` as the working folder, and the default storage folder `.nexus` resolved
  against the working folder. Found in a Cline install test.
  - A relative `NEXUS_STORAGE_DIR` now resolves against the indexed project when that project
    is outside the working folder, or when the working folder is a filesystem root. The index
    then goes to `<project>/.nexus/`, as the docs already said.
  - A project in the working folder keeps the old place, so a new session in Claude Code,
    Cursor and the other clients still attaches the index by itself. An absolute
    `NEXUS_STORAGE_DIR` (as in the Docker image) does not change.
  - When `index` moves to another project, the server opens that project's index.
  - In Cline a new session cannot attach the index by itself, because the server starts in
    `/`. The agent calls `index` once: with an existing index this is an incremental update
    (3 ms on the test project).
  - Tested with a real stdio server started in `/`: `index` and `search` work, and `/.nexus`
    is not made. Tests: `tests/test_storage_location.py`.
- **`setup --client cline` wrote no `"type": "stdio"`.** Cline writes it in its own entries.

### Added

- **`nexus-mcp-ci --version`** (and `-V`) prints the version and exits.
- **`llms-install.md`** fixes from the Cline test: check first whether the tools already work,
  and add no second entry for a server that runs from another config; most entries are
  user-level; why the entry pins the version; tool names can have a prefix (`nexus__index` in
  Cline); check the install with `search`; where the index is, and `NEXUS_STORAGE_DIR`.

### North star

- Tokens: no change. The server instructions and the tool results do not change. In Cline the
  agent no longer spends failed `index` calls and a repair.
- Accuracy: no change to the parser, the index or the search. The live benchmark was not run
  again.

## [2.4.2] - 2026-11-08

### Fixed

- **The install failed when the default Python was 3.13 or 3.14.** uvx uses the default
  Python for a tool and does not apply `requires-python`, and `tree-sitter-languages` has
  wheels only up to 3.12. Found in a Cline install test on a machine with Python 3.14.
  - `setup` writes `uvx --python 3.12 nexus-mcp-ci==X` for every client.
  - `plugin/.mcp.json`, `gemini-extension.json` and the VS Code and Cursor install links use
    the same launch. `server.json` gives registry clients `runtimeHint: uvx` with
    `--python 3.12`.
  - Every `uvx` command in the docs has the pin. `check_docs.py` fails when a launch has no
    pin, and `bump_version.py` keeps it.
- **The Claude Desktop manifest listed 8 tools.** It now lists the 5 that the server shows by
  default. A test compares the manifest with the server.

### Added

- **`NEXUS_WORKSPACE` for container images.** In a container the agent knows only host paths,
  so its first `index` call failed with "Not a directory". When `NEXUS_WORKSPACE` is set, the
  server instructions name the mounted folder, and the `index` path error says which path to
  use. `docker/Dockerfile.stdio` sets it to `/workspace`. Tested with Colima: the image builds
  from the repository, answers over stdio, indexes `/workspace` and reuses the index in a new
  container.
- **A draft Docker MCP Catalog entry** in `docker/mcp-registry/` (`server.yaml`, and a
  `tools.json` generated from the server).
- **`llms-install.md`** names the server (`nexus-mcp`), the file that `setup` changes for each
  client, the `.nexus-bak` copy, and asks the agent to check for an existing entry first.
- **A star prompt.** The README has a GitHub stars badge and one line that asks for a star. The
  site's GitHub button now says "Star", and the hero has a star link. `setup` prints one line
  with the repository link after a successful install only (not for `--dry-run` or
  `--uninstall`).

### North star

- Tokens: no change for the MCP tools. `status()` and every tool answer are the same. The star
  line is CLI output of `setup`, printed once per install: about 25 tokens when an agent runs it.
- The server instructions are the same unless `NEXUS_WORKSPACE` is set: 1,398 characters with
  the default tools, before and after. In the Docker image they grow by 136 characters (about
  35 tokens), and the agent no longer spends a failed `index` call.
- Accuracy: no change to the parser, the index or the search. The live benchmark was not run
  again.

## [2.4.1] - 2026-11-08

### Added

- **Parser gaps closed for C, C++, C#, PHP and Rust.** Each language had at least one of
  these gaps: no tree-sitter symbols, no graph nodes, no CALLS edges.
  - Tree-sitter symbols for Rust, PHP and C#. A Rust `impl` block gives its methods the
    type as parent. Doc comments (`///`, `/** */`, C# XML) fill the docstring.
  - ast-grep function and class nodes for C, C++, C# and PHP. C and C++ names come from the
    declarator, as in the tree-sitter parser; an out-of-line `Widget::Paint` is a method of
    `Widget`.
  - CALLS edges for C, C++, C# and PHP, precision first (ADR-019, amendment of 2026-11-05).
    Includes and `use` lines count as imports; `std::` calls and library method names
    (`push_back`, `Add`, `map`) get no edge.

- **A per-language results table, ready for data.** `results.json` is schema v2: it can hold
  an optional `languages` list, with a `before` run and an optional `after` run for each
  language. `publish_results.py` writes the table to the README, the site results rows,
  `llms.txt`, the benchmarks page and the CSV. A cell shows "before → after" when both runs
  exist. On a phone the table scrolls in its own box. Schema v1 files still work. The
  table holds TypeScript (Vite), JavaScript (webpack), Java (commons-lang) and C++
  (Chromium `base/`). The CSV has new columns for the language rows.
  North star: no change (output only).

- **`Class.method` names and subclass overrides in `graph` (issue #31).** On Django,
  `graph("Field.validate")` gave no callers and `find_symbol("Field.validate")` gave "not
  found", so the agent ran 5 to 8 greps on each impact task.
  - The parser records the base classes of each class in Python, JavaScript and TypeScript.
  - `find_symbol` and `graph` accept `Class.method` and `Class::method`.
  - For a method of one class, `graph` also lists the `overrides` in subclasses.
  - `references` gives only a count (`non_code`) for files that are not code. On Django the
    word `validate` had 100+ `.po` files, which used the line budget before the code.
  - Bases are names, so two classes with one name merge. Treat the overrides as a lower
    bound.

  Live benchmark on Django, `claude -p`, Sonnet, medium effort, reduced isolation, 6 tasks ×
  3 reps, 2.4.0 → this change (Grep and Read in brackets):
  - Median of the 18 runs: 52,410 (46,570) → 52,280 (46,523) tokens.
  - Sum of the 18 runs: 1,496,136 (1,283,308) → 1,459,415 (1,225,952) tokens; fresh tokens
    217,904 (164,843) → 149,809 (168,238); cost $1.357 ($1.101) → $1.043 ($1.106).
  - Correct answers: 15 (16) → 16 (16) of 18.
  - The impact task on `Field.validate`: median 102,845 → 33,242 tokens, 12 → 2 turns. It
    is the only Django task where Nexus-MCP uses fewer tokens than Grep.
  - The median of the 6 task medians (the summary table value) goes from 54,038 (47,846) to
    43,210 (52,202). This value moves with one task, so do not read it as a win.
  - Nexus-MCP costs less than Grep on Django and has 11% fewer fresh tokens. Its tokens are
    still 12% higher by median and 19% higher by sum: each turn adds about 5k tokens of
    fixed text, read again from the cache. The `SQLCompiler.as_sql` task still takes 22
    turns. Django is not a win.

  Accuracy gate: the quick tier, retrieval hit@1 (Flask 0.59, Vite 0.47, webpack 0.20) and
  the graph counts do not change on Flask, Vite and webpack. Run logs are in
  `benchmarks/runs/languages-py-fix`.

- **`graph` names the file of the symbol itself (`defined_in`).** On Django the impact
  task on `Field.validate` got the overrides in 2 turns, but in 2 of 6 runs the answer
  left out `fields/__init__.py`, where `Field.validate` is, and the scorer marked it
  wrong. A name with more than 5 definitions gets no `defined_in`.
- **Less repeated text on each turn.** The skill body now holds only what the server
  instructions and the tool descriptions do not say. The `index`, `search` and
  `find_symbol` descriptions lose sentences that another text already says. Fixed text
  (skill, instructions and 5 tool schemas, estimated as characters / 4): about 1,420 →
  1,240 tokens on each turn.
- **`graph` references of a method rank by its class.** For a method of one class, the
  files that name the class or a subclass come first. A `def` line of another method with
  the same name only adds to a `definitions` count. On Django `SQLCompiler.as_sql` listed
  `contrib/gis` first, and 91 of 283 lines were other `as_sql` definitions.

  Live benchmark on Django, same settings as above (Grep and Read in brackets). Grep-side
  numbers move a lot between runs, so compare each Nexus-MCP number with its own Grep run.
  - Reference ranking (`f1e9d56`): sum of the 18 runs 1,283,949 (1,589,581) tokens, median
    47,943 (46,699), fresh 145,769 (173,909), cost $0.972 ($1.215), correct 16 (17) of 18.
    Nexus-MCP uses fewer tokens on 2 of 6 tasks.
  - With `defined_in` and the shorter fixed text (`45aec40`): sum 855,938 (1,346,905)
    tokens, median 32,938 (46,642), fresh 129,072 (176,969), cost $0.789 ($1.178),
    correct 16 (17) of 18, median turns 2 (4.5). Nexus-MCP uses fewer tokens on 4 of 6
    tasks: 36% fewer by sum, 29% fewer by median, 33% lower cost.
  - The `SQLCompiler.as_sql` impact task: median 197,972 → 77,526 tokens (Grep 201,082).
  - Answer score (facts and files, 0 to 1): Nexus-MCP 0.819, Grep 0.833. The two Nexus-MCP
    answers marked wrong name the right classes and facts. One does not name
    `fields/__init__.py`, and one does not name `core/validators.py` as a path. A Grep
    answer on the second task has the same miss.

  Live benchmark on Home Assistant (about 26,000 Python files), `45aec40`, same settings:
  - Sum of the 18 runs 1,179,981 (1,821,765) tokens, median 50,500 (56,948), fresh 149,395
    (174,706), cost $0.934 ($1.241), median turns 3 (5.5). Nexus-MCP uses fewer tokens on 4
    of 6 tasks: 35% fewer by sum, 11% fewer by median, 25% lower cost.
  - The startup architecture task: median 97,495 (394,235) tokens.
  - Correct answers: 15 (18) of 18. Answer score 0.861 (0.931). The accuracy gate fails.
  - The 3 misses are the 3 Nexus-MCP runs of the `async_setup_component` impact task. Each
    answer names the right callers in `bootstrap.py`, but not `homeassistant/setup.py`,
    where the function is. The task also costs more: median 123,553 (58,012) tokens.
    `graph` gives 4,979 callers in 222,303 characters, about 55k tokens. Claude Code does
    not give the agent an MCP result above 25k tokens (its default limit) in full, so the
    agent greps again.
  - Run logs are in `benchmarks/runs/languages-refs-fix` and `languages-defs-fix`.

- **`graph` results have a size cap.** A symbol with thousands of callers gave a result
  above the 25k-token MCP limit of Claude Code.
  - `graph` lists at most 60 source files and 10 test files of callers (or of impacted
    files with `transitive=true`). `callers_not_listed` or `impacted_not_listed` gives the
    rest as counts: files, test files, symbols and the 10 largest folders.
  - `references` lists at most 20 files in `more_files` and in `test_files`.
    `more_files_not_listed` and `test_files_not_listed` count the rest (files and lines).
  - Measured on the Home Assistant index (characters of the JSON result, about 4 per
    token): `graph("async_setup_component")` 222,303 → 6,220; with `transitive=true`
    → 10,506. After the cap, `async_fire` gives 14,165 and `Entity.async_write_ha_state`
    gives 8,131.
    `defined_in` stays in each result.
  - Live A/B of `cf1bb7a` (`benchmarks/runs/languages-graph-cap`), Sonnet at medium effort,
    6 tasks × 3 reps, on the defs-fix indexes. The cap changes only the output.
    - Home Assistant, the `async_setup_component` task: the `graph` result fell to about
      1,555 tokens, and the median of the task fell from 123,553 to 72,656 tokens (Grep
      58,977). All 3 runs now have every fact (before: 0 of 3 correct). 1 of 3 is
      correct: 2 answers write `setup.py` with no folder, which the scorer does not
      accept. 1 Grep run has the same miss.
    - Home Assistant, all tasks, Nexus against Grep: sum 1,046,718 / 2,350,456; median
      51,774 / 48,726; fresh 168,565 / 210,805; cost $0.972 / $1.491; correct 15 / 17 of
      18; fact score 0.889 / 0.833; combined 0.861 / 0.880; tasks with fewer tokens 3 of 6.
      The gate still fails by 2 runs. Each Nexus miss has all the facts and leaves out a
      file path.
    - Django, Nexus against Grep: sum 884,896 / 1,276,331; median 32,924 / 46,538; fresh
      113,262 / 168,863; cost $0.728 / $1.134; correct 16 / 16 of 18; combined 0.847 /
      0.833; tasks with fewer tokens 4 of 6. The `SQLCompiler.as_sql` task fell from
      77,526 to 33,573 tokens (Grep 199,128).

### Fixed

- **Code review of this branch (triple-agent audit, 2026-11-08).**
  - `graph` references dropped the `function save` line of a subclass override for a
    method in PHP, Go, Java, C#, C, C++ or Rust. Only Python, JavaScript and TypeScript
    record base classes, so only those languages get an `overrides` list. The other
    languages now keep those lines.
  - The 60-file cap of `graph` kept the first files by name. For `transitive=True` a far
    caller in `a/` could push out a direct caller in `z/`. The cap now keeps the files in
    the order of the walk, nearest first.
  - The `truncated` note of a full `graph` result said that `detail="compact"` names all
    callers. The compact list has a cap, so the note now says so.
  - `scripts/archive_runs.py` accepted a run label that ends in a newline.
  - North star: not measured. The references fix does not touch Python, JavaScript or
    TypeScript. The cap change affects only a result with more than 60 source files. No
    paid rerun was made.

- **Security review of this branch.**
  - Without ripgrep, `graph` references used plain `grep`, which also read git-ignored and
    hidden files, such as a `local_settings.py`. In a git repo the fallback is now
    `git grep`, which skips ignored files. Plain `grep` drops hidden paths.
  - `scripts/archive_runs.py` refuses more credential shapes: GitHub, AWS and Hugging Face
    tokens, private keys, and `NAME=value` secret lines. It also refuses any file that
    holds the live token value from the environment or the Keychain. It writes the home
    folder as `~`. The run logs already committed were rewritten in the same way.
  - The archive script and `install_nexus.sh` refuse a run label with `/` or `..`.
  - A corpus checkout loses `CLAUDE.md`, `.claude` and `.mcp.json` at every depth, not
    only at the top.
- **Code review of this branch.**
  - The published C++ "calls per function" counted the Java, Python and Rust files of
    Chromium `base/`. A corpus now counts only the graph rows of its own language. The
    C++ entry (2.2.2) changes from 5,745 function nodes and 1.39 calls per function to 0
    and 0: 2.2.2 had no C++ graph nodes. TypeScript changes from 1,721 and 1.38 to 1,689
    and 1.40.
  - `references` lists templates, `.vue`, `.toml`, `.yaml` and other config files again,
    after the code. Only translations, prose, maps and lock files are a count.
  - `def` lines of other methods are left out only for a method of one class. The pattern
    also knows PHP, Rust and Go heads.
  - `overrides.subclasses` counted a class twice when a stub file (`.pyi`) defined the
    method too.
  - A C call to a function with the name of a struct gets its edge.
  - `search` drops a one-line result next to its body only in C and C++ files: a one-line
    JavaScript `init` is a definition of its own.
  - `publish_results.py` no longer stops on a language with no file count.
  - `install_nexus.sh` deletes the index folders of a label when it installs a new commit.
- **`run_languages` live tier on Home Assistant.** The task file names the repo
  `home-assistant-core`, and the clone folder is `home-assistant`, so the runner did not
  find the repo. The live tier now passes the clone folder of the corpus to the runner.
- **C# and Dart files stopped the whole index.** The ast-grep ids `c_sharp` and `dart` do not
  exist. ast-grep-py then raises a `BaseException` panic that `except Exception` does not
  catch. C# now uses `csharp`; Dart has no id and is skipped.
- **C++ keyword as a function name.** `FMT_BEGIN_NAMESPACE namespace detail {` gave a
  function named `namespace` (8 in fmt). Such a declarator now gives no symbol.
- `find_symbol` puts a constructor after the class of the same name.

### Changed

- **The server instructions ask for full file paths in an answer.** In the graph-cap live
  run on Home Assistant, 3 Nexus answers had every fact but named a file by its bare name
  (`setup.py` for `homeassistant/setup.py`) or not at all. A bare name can fit many files,
  and a reader cannot open it. The results already give the full path. The new rule tells
  the agent to copy that path for each file, function or class that it names. It applies
  in every project, and it adds about 60 tokens to the instructions.
  - Live A/B of `195090c` on Home Assistant and Django (`benchmarks/runs/languages-v2.4.1`),
    Sonnet at medium effort, 6 tasks × 3 reps, on copies of the defs-fix indexes.
    `195090c` differs from the 2.4.1 commit only in the version string, so the runs are
    filed as 2.4.1. Each record keeps `nexus_ref` 195090c and has a `nexus_version_note`.
  - Home Assistant, Nexus against Grep: sum 880,904 / 1,730,618; median 42,396 / 65,990;
    fresh 152,801 / 187,939; cost $0.870 / $1.273; correct 18 / 17 of 18; fact score
    0.917 / 0.889; combined 0.949 / 0.917; tasks with fewer tokens 5 of 6. The gate passes.
    Before (graph-cap run, `cf1bb7a`): Nexus correct 15 of 18, sum 1,046,718, cost $0.972.
    The `async_setup_component` task went from 1 to 3 of 3 correct, with a median of
    53,219 tokens (before 72,656; Grep 76,403).
  - Django, Nexus against Grep: sum 1,008,351 / 1,372,604; median 32,788 / 46,588; fresh
    131,646 / 170,208; cost $0.841 / $1.151; correct 17 / 16 of 18; combined 0.882 /
    0.819; tasks with fewer tokens 2 of 6. The gate passes. Before (graph-cap run): Nexus
    correct 16 of 18, sum 884,896, cost $0.728.
  - Open item: on Django the `SQLCompiler.as_sql` task rose from a median of 33,573 to
    133,787 tokens (Grep 209,738). All 3 runs made 2 to 7 `graph` calls and walked the
    transitive chain by hand, with longer answers. 1 of 3 runs before the rule did the same,
    so part of the rise is run-to-run noise. The rule can also push the agent to look up a
    path for each function it names.
  - Both repos together, Nexus: sum 1,931,614 → 1,889,255 tokens; correct 31 → 35 of 36.
    Grep's totals change much from run to run (Home Assistant sum 2,350,456 in the
    graph-cap run), so the token gaps are less certain than the accuracy results.

- **5 tools by default.** The server shows `index`, `search`, `find_symbol`, `graph` and
  `map`. Set `NEXUS_EXTRA_TOOLS` to `status`, `analyze`, `memory` (comma-separated) or `all`
  to show the others. Agents almost never called these three in the live benchmark, and
  their descriptions cost about 750 tokens on each turn. The server instructions and the
  skill no longer name them when they are hidden. **Upgrade note:** `memory` stays on
  when the storage folder already holds memories, so memories of 2.2.x stay reachable.
  The names in `NEXUS_EXTRA_TOOLS` are not case-sensitive.
- **The index format is 8.** An index of 2.2.x is rebuilt once at the first start. Without
  the rebuild, unchanged files keep no class `bases` (so `graph` shows no `overrides`) and
  no symbols or graph nodes for Rust, PHP, C# and C/C++.
- **Error, warning and exception classes rank after other source.** Their messages repeat
  the words of a feature, so on webpack `*Warning.js` files ranked above the plugin that
  does the work. A query that names an error keeps them in place. Retrieval hit@1, before
  → after: `shop_repo` 0.50 → 0.62; `nexus_mcp` 0.71, Flask 0.68, Vite 0.47 and webpack
  0.27 do not change.
- **Less fixed text on every turn.** The tool descriptions, the parameter descriptions, the
  server instructions and the skill body are shorter. They were 3.6k tokens above the Grep
  condition on each turn, and tool results were the same size in both conditions, so this
  text explained most of the token gap. No tool, parameter or enum changed.

  Token measure (north star), first-turn context of `claude -p` on Redis, Sonnet, medium
  effort, no tool call: Grep 12,480; Nexus-MCP 16,105 before and 15,322 after. The gap to
  Grep falls from 3.6k to 2.8k tokens on each turn.

  Live benchmark, Nexus-MCP only, 6 tasks × 3 reps, same model and effort as the 2.2.2 run
  (Grep numbers are from the 2.2.2 run). Median tokens per task, 2.2.2 → this branch (Grep):
  Vite 57,342 → 60,920 (77,004); commons-lang 34,602 → 32,848 (56,271); Redis 70,567 →
  48,020 (42,290); Prometheus 57,716 → 52,196 (52,662). Correct answers: 71 of 72 → 72 of
  72. The Redis change includes the new C function nodes and the Vite change is inside the
  run-to-run noise, so read the four rows as one result: the mean of the four medians falls
  from 55.1k to 48.5k (Grep 57.1k) and accuracy does not fall.

### Measured

`nexus-mcp-ci eval` quick tier, 30 questions, seed 7, default model, on pinned public repos.
Before is release 2.2.2 (`0430f01`); after is this branch. hit@1 / hit@3 of Nexus-MCP:

| Language | Repo (commit) | Before | After |
|---|---|---|---|
| C | redis/hiredis (5bb7f03) | 0.733 / 0.933 | 0.733 / 0.933 |
| C++ | fmtlib/fmt (4ed3535) | 0.933 / 1.000 | 0.933 / 1.000 |
| C# | jbogard/MediatR (62010ba) | crash (`c_sharp` panic), no index | 0.833 / 0.967 |
| PHP | Seldaek/monolog (3bed304) | "No source code was indexed" | 0.933 / 1.000 |
| Rust | BurntSushi/ripgrep (91f5d22) | 1.000 / 1.000 on 26 questions, Bash only (16 chunks) | 0.900 / 1.000 on 30 questions (3,417 chunks) |

The Rust "before" covered only the Bash scripts of the repo. The drop from 1.000 to 0.900 is
a harder question set (Rust symbols are now in it), not a loss on the same questions.
C and C++ do not change: both already had symbols; this work adds graph nodes and edges.

Retrieval gate (`python -m evals.retrieval.run --candidates bge-small-en`, hybrid hit@1;
only the `nexus_mcp` and `shop_repo` suites run here, the others need local clones):
`nexus_mcp` 0.62 → 0.71, `shop_repo` 0.50 → 0.62. It does not fall. The `nexus_mcp` source
is larger after this change, and the suites have 24 and 8 queries, so read the rise as noise
around "no loss". `pytest` (1366 passed) and `ruff check .` are green.

Token measure (north star): the change adds no tokens per query. Median tokens of the
quick tier did not change on hiredis (755.5) or fmt (868.0).

Live benchmark of 2.4.0 against 2.2.2 (`0430f01`), `claude -p`, Sonnet, medium effort,
reduced isolation, 6 tasks × 3 reps per project. Median tokens per task, Nexus-MCP / Grep
and Read, and correct answers:

| Project | 2.2.2 | 2.4.0 | Correct (Nexus-MCP / Grep), 2.2.2 → 2.4.0 |
|---|---|---|---|
| Django (Python) | 69,144 / 59,562 | 54,038 / 47,846 | 6 / 5 → 5 / 5 of 6 |
| Vite (TypeScript) | 57,268 / 71,166 | 52,411 / 69,790 | 6 / 6 → 6 / 6 of 6 |
| webpack (JavaScript) | no run | 74,638 / 100,320 | 6 / 6 of 6 |

Nexus-MCP uses 25% fewer tokens than Grep on Vite and 26% fewer on webpack. On Django it
uses 22% fewer tokens than 2.2.2, but 13% more than Grep. The Grep side of Django also fell
by 12k tokens between the two runs, so part of the change is run-to-run noise. The three
projects cost $7.24 in all. Run logs are in `benchmarks/runs/languages-v2.4.0`.

## [2.3.0] - 2026-11-06

### Added

- **`nexus-mcp-ci setup`: one command to install the server and its skill.** It finds Claude
  Code, Claude Desktop, Cursor, VS Code, Windsurf, Gemini CLI, Codex and Cline on the machine,
  shows each change, and asks before it writes. `--dry-run` writes nothing, `--yes` does not
  ask, `--client` picks one, `--uninstall` removes what it wrote. An agent can run it from
  `llms-install.md` ("Install Nexus-MCP from the repository URL"). It keeps the other servers
  in a config file and refuses a config that is not valid JSON.
- **More install channels.** A Claude Desktop extension (`mcpb/`, built by `publish.yml`; it is
  also a plain way to install it by hand), a Gemini CLI extension, VS Code and Cursor
  install links, a `docker/Dockerfile.stdio` image pushed to GHCR, and a composite GitHub
  Action (`action.yml`). `scripts/bump_version.py` sets every pinned version at once.
  [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) lists each channel and the owner steps.
- **Cursor plugin.** `.cursor-plugin/marketplace.json` and `plugin/.cursor-plugin/plugin.json`
  reuse the skill and the `uvx` launch. `bump_version.py` and `check_docs.py` cover the new file.
  [docs/SUBMISSIONS.md](docs/SUBMISSIONS.md) holds ready-to-paste text for each listing.

### Changed

- **Licence: PolyForm Noncommercial 1.0.0 → MIT** ([ADR-024](docs/adr/ADR-024-mit-licence.md)),
  to remove the barrier for companies and directories that accept only OSI licences. Every new
  release is MIT. Versions 2.0.1 to 2.2.3 keep the PolyForm terms for those who got them. A paid
  commercial tier (a Polar licence key, built and tested in the sandbox during this cycle) was
  dropped before release, and its code is not in this release. The server again makes no
  network call of its own.
- The Claude Code plugin starts the server with `uvx nexus-mcp-ci==X`, so it needs `uv` and no
  `pip install`. The README quickstart now starts with "ask your agent" and `uvx nexus-mcp-ci setup`.
- The skill has an install section for a client with no nexus tools. The always-loaded files
  (rules, `AGENTS.md` block, `gemini-context.md`) and the benchmark loaders cut it.

### Removed

- `smithery.yaml`. Smithery shut down its CLI, API and accounts on 31 October 2026 and stays
  only as a curated public directory, so there is nothing to publish there.

### North star

- Tokens: no change for any user. `status()` is the same 204 characters (51 tokens, 4
  characters per token). The skill text in the benchmark and the eval is unchanged: the loaders
  cut the new install section.
- Retrieval gate, hit@1 in `hybrid-exact` mode, before (commit 0430f01) and after, same
  command (`python -m evals.retrieval.run --single bge-small-en`), each in a clean checkout:
  `nexus_mcp` 0.67 and 0.67, `shop_repo` 0.50 and 0.62. hit@5: 0.92 and 0.92, 1.00 and 1.00.
  The gate holds: hit@1 did not fall. Only two of the four suites ran (`flask` and `jobscout`
  have no checkout here). No retrieval code changed. The `nexus_mcp` suite indexes this source,
  so it grew from 773 to 854 chunks. The `shop_repo` gain is not explained by the diff: its
  chunks are the same, BM25 and vector hit@1 are equal, and only the two fused modes moved
  (one query of eight). Two baseline runs gave the same 0.50, and two runs of this branch gave
  0.62, so it is not run-to-run noise. Treat it as unexplained, not as an improvement.

## [2.2.3] - 2026-11-06

### Added

- **The server stops by itself when the session is done.** A new module, `lifecycle.py`
  ([ADR-022](docs/adr/ADR-022-server-lifecycle-and-exit.md)), stops the server:
  - after 3 hours with no tool call (`NEXUS_IDLE_TIMEOUT`, in seconds; `0` turns it off). A
    tool call or an index that runs is not idle time;
  - when the client process that started it is gone, also when the stdin pipe stays open
    (`NEXUS_EXIT_ON_PARENT_EXIT`, default `true`);
  - on SIGHUP (the terminal closed), as on SIGTERM and SIGINT.
  After an idle stop, reconnect the server in the client (`/mcp` in Claude Code). The stored
  index attaches again.

### Fixed

- **The server did not exit after SIGTERM.** It logged "Nexus-MCP shutdown complete." and was
  still running 20 s later: the interpreter exit waited for a thread. Each stop now runs the
  cleanup with a deadline (`NEXUS_SHUTDOWN_TIMEOUT`, default 10 s) and ends the process with
  `os._exit`. Measured after the fix: exit 0 in 0.01 s after SIGTERM. A watchdog ends a
  cleanup that hangs, and a second signal ends the process at once (exit code 1).
- **The shutdown saved the graph while an index changed it.** The save now waits for the
  running index. If the index still runs at the deadline, the graph is not saved, and the next
  start finds the index out of date.
- North star: no tool output changes, so the tokens of the benchmark do not change. hit@1 is
  not affected (no change to parsing, indexing or search).

## [2.2.2] - 2026-11-05

### Added

- **A head-to-head with GitNexus on Chromium `base/`, published.** `results.json` has a new
  `comparisons` list. `publish_results.py` writes it to the README, the benchmarks page
  and `llms.txt`. 8 C++ tasks, 3 runs each, Claude Sonnet, 24 of 24 correct for each
  condition. Median tokens and cost per task: Grep and Read 62,450 and $0.057; Nexus-MCP
  (with the C++ fixes of 2.2.1) 82,900 and $0.062; GitNexus 1.6.12 164,192 and $0.085.
  North star: Nexus-MCP does not lower tokens against Grep and Read on this C++ code.
  Before the C++ fixes, the WeakPtr task cost $0.100 (106.8k tokens); after, $0.069
  (82.6k). The benchmarks page lists the rules that kept the comparison fair.
- **Fair comparisons, documented.** `benchmarks/README.md` has a "Fair comparisons" list:
  the same agent, prompt and built-in tools in each condition, each tool with only what its
  real user has loaded, warm caches, isolation, pinned versions, 3 runs and the same
  scoring. The benchmark runs on any repo (`--repo-path`, `--generate`). The GitNexus
  condition and its setup scripts were removed after the run; only the published numbers
  stay. `benchmarks/tasks/chromium-base.yaml` stays as a C++ task suite.

### Fixed

- **C++ attributes in a class head, and out-of-line nested classes.**
  - A class head with `[[...]]` attributes before, between or after its macros was not a
    class (`class [[nodiscard]] SCOPED_LOCKABLE BasicAutoLock {`,
    `class BASE_EXPORT [[maybe_unused, nodiscard]] TaskScope {`). Its constructors were
    top-level functions. The class-head mask now masks these attributes with the macros.
  - `class Outer::Inner {` was named `Outer::Inner`, so `find_symbol("Outer::Inner")` did
    not find it, and its methods had the parent `Outer::Inner`. It now has the name `Inner`
    and the parent `Outer`, as a class nested in the body of `Outer` has. A `::` in template
    arguments does not split the name (`std.pointer_traits<a::B<T>>`).
  - A parse of Chromium `base/` before and after renames 412 symbols from `::` to `.` and
    adds 28, in 174 files. No other name changes. `CHUNK_FORMAT` is 7. hit@1 does not
    change: the `nexus_mcp` and `shop_repo` suites have no C or C++ file (0.62 and 0.50).

## [2.2.1] - 2026-11-05

### Fixed

- **C++ symbol names.** A benchmark run on Chromium `base/` (commit d8ebdd25) found two
  defects. The agent then fell back to Glob and Read and used 97.7k tokens, against 50.0k
  for Grep and Read.
  - An out-of-line definition was named after its return type.
    `WeakReference WeakReferenceOwner::GetRef() const` was the function `WeakReference`.
    C and C++ functions now take their name from the declarator. `GetRef` is a method with
    the parent `WeakReferenceOwner`. Most `Owner::Method` definitions were not indexed at all.
    Constructors, destructors (`~Foo`), operators, templates and pointer returns keep their
    own names. Constructors and destructors are methods.
  - A `.h` file was parsed as C, so the classes in a C++ header were not found. A `.h`
    file with C++ syntax (`namespace`, `class X`, `template <`, `public:`, `::`) is now
    parsed as C++. A nested C++ class has its outer class as parent.
  - `find_symbol` takes `::` names: `WeakReference::Flag`, `AtExitManager::RegisterCallback`,
    `Flag::Invalidate`. A namespace prefix is dropped when the full name has no match
    (`base::WeakPtr` finds `WeakPtr`). The symbol with the whole name comes before a member
    with the same last part, so `WeakReference` gives the class before its constructor.
  - A forward declaration (`class Foo;`) no longer gives a class chunk.
  - `CHUNK_FORMAT` is 4: an older index is rebuilt on the next `index` call.
  - Retrieval gate, hit@1 in `hybrid-exact` mode, before and after: `nexus_mcp` 0.62 and
    0.62, `shop_repo` 0.50 and 0.50. hit@5 on `nexus_mcp` went from 0.92 to 0.88, because that
    suite indexes this source and the change edits it. The chunks of `shop_repo` are
    identical before and after. `flask` did not run (its checkout `benchmarks/repos/flask`
    is not present), and `jobscout` is a private local suite that is not present. No suite
    has C++ code, so the gate shows only that Python retrieval did not fall. A C++ suite
    and a new Chromium benchmark run are still to do.
  - **Export and annotation macros.** On a Chromium index built from the fix above, about
    640 top-level classes in `base/` headers were still wrong. Examples are
    `class BASE_EXPORT TimerBase {` and `class BASE_EXPORT TRIVIAL_ABI WeakReference {`.
    tree-sitter-cpp read these as function definitions, so `TimerBase` was a function and
    `DeadlineTimer` was the method `internal.TimerBase`. The methods in their bodies were lost.
    The parser now masks ALL_CAPS macros in a class head with spaces before the parse. This
    covers macros with arguments (`COMPONENT_EXPORT(X)`), `final`, base clauses and `struct`.
    It also masks annotation macros between a parameter list and a body
    (`void Acquire() EXCLUSIVE_LOCK_FUNCTION() {`). Before, such a method had the name of
    its macro. Offsets do not change, so names and code still come from the original
    text. An ALL_CAPS class name (`class URL_PARSER : public Parser {`) is not masked.
    `CHUNK_FORMAT` is 5. hit@1 in `hybrid-exact` mode, before and after, in one folder:
    `nexus_mcp` 0.62 and 0.62, `shop_repo` 0.50 and 0.50.
    The mask regex has a word boundary after each macro name. Without it, a run of macro
    lines after a `)` (`DEFINE_CHECK_OP_IMPL(EQ, ==)` in `base/check_op.h`) backtracked:
    four lines took 5.1 s, and 23 files of `base/` stopped the Chromium index for more
    than an hour. Now the 3,168 C and C++ files of `base/` have no file over 0.5 s.
  - **Annotation macro before a constructor initializer.** The tail-macro mask now also
    applies when a member initializer list follows the macro
    (`explicit AutoLock(Lock& l) EXCLUSIVE_LOCK_FUNCTION(l) : lock_(l) {`). Before, the
    Chromium index named 12 such constructors after the macro (`BasicAutoLock`,
    `TaggedMetricAutoLock`, the partition_alloc `ScopedGuard`). The macro also broke the parse
    of `basic_cstring_view`, so 48 of its methods had no class as parent. A parse of all of
    `base/` before and after changes only these names. `CHUNK_FORMAT` is 6. hit@1 does not
    change: the `nexus_mcp` and `shop_repo` suites have no C or C++ file, so their chunks
    are the same (0.62 and 0.50).
  - Eval note: the chunk text starts with the absolute file path, so the scores depend on
    where the checkout is. The same code gave `shop_repo` 0.50 in one folder and 0.62 in
    another. Compare a before and an after run in the same folder.

## [2.2.0] - 2026-11-03

### Added

- `nexus-mcp-ci eval [PATH|URL] [--example]`: a free, offline check that compares Nexus-MCP with
  Grep and Read on questions made from your own code. It calls no LLM and sends nothing. The
  result is a verdict, a table by kind of question, and a "Data in this run" block. Runs are saved
  under `~/.nexus-mcp/evals/` (`eval list`, `eval show <id>`). On this repo's `src/nexus_mcp`
  (53 files, 30 questions) the token proxy reads 1,446 tokens against 5,339 for Grep and Read.
  `--example` runs the same check on the installed Nexus-MCP source, offline. An 18-file toy
  project and a synthetic 119-file project both gave "Weak fit" (Grep and Read used fewer tokens),
  so the toy project is not shipped.
- `nexus-mcp-ci data where | clear | export-memories`: list every place Nexus-MCP stores data,
  delete it (a dry run unless `--yes`), and export memories first. `clear` refuses a folder that
  is not a Nexus-MCP store.
- `NEXUS_OFFLINE=1` blocks the embedding model download. Hugging Face telemetry is off by default.
- `python -m nexus_mcp` starts the server.
- A test that proves the quick tier opens no network connection.
- `nexus-mcp-ci eval --agent claude[:model][,codex[:model]]`: the opt-in paid tier. It runs
  Claude Code or Codex (experimental) on tasks made from your code, with `baseline` (Grep, Glob
  and Read), `mcp` (the server) and `nexus` (the server and its skill). One index is shared by
  every run. It shows a cost estimate and asks first, and it stops starting runs at `--max-usd`
  (default $5). Presets: `quick`, `matrix` and `efficient`. `efficient` names the cheapest model
  and setup whose accuracy is not below the baseline of the same model.
- `nexus-mcp-ci eval doctor`: checks ripgrep, the cached model, the `claude` and `codex` CLIs,
  and the results folder.

- `benchmarks/published/results.json` is the one source of every published benchmark number.
  `scripts/publish_results.py` writes the README "Results at a glance" block, the site results
  rows, the llms.txt "Benchmarks" section and `site/benchmarks.{html,json,csv}`. CI runs it with
  `--check` and fails when a generated block is out of date.
- `nexus-mcp-ci eval share <id>`: shows the allow-listed numbers of a saved run (no paths, no
  code, no question text, no local project name) and posts them as a GitHub issue only after a
  yes. Without a logged-in `gh` it prints the issue-form link. A maintainer label
  (`eval-accepted`) starts `community-results.yml`, which validates the payload and opens a PR
  that adds it to the community table on `benchmarks.html`. There is no telemetry.
- Evaluate first: the README, the site hero, llms.txt, PyPI and Glama now lead with
  `uvx nexus-mcp-ci eval` for a person who has not decided. New README sections "Evaluate on
  your project" and "Privacy and Data", a site `#try` section and FAQ entries.
- Plugin: `/nexus-mcp:evaluate` skill and the `nexus-evaluator` agent (it runs the quick tier
  and returns only the summary).
- `server.json` for the official MCP Registry, a registry publish job in `publish.yml`, and the
  `mcp-name` marker in the README. `context7.json`.
- Docs: `docs/README.md` (index), `EVALUATE.md`, `PRIVACY.md`, `TROUBLESHOOTING.md`, `CLI.md`
  (generated by `scripts/gen_cli_docs.py`, checked in CI), `SECURITY.md`, `CONTRIBUTING.md`,
  ADR-020 and ADR-021.
- Deep demo: `eval` on Chromium `base/` (commit `5997890`, 3,638 indexed files, 40 questions)
  gives "Strong fit": 950 median tokens to the right file against 17,998 for Grep and Read
  (95% fewer), right file first 70% against 40%, top 3 90% against 58%. Indexing takes about 25
  minutes on a laptop. Added to `benchmarks/published/results.json`.
- `eval https://github.com/pallets/click` (91 files, the first real run of the URL path): "Strong
  fit", 1,301 median tokens against 24,774 (95% fewer), right file first 97% against 27%. The
  homepage results table now shows each quick-tier run too.
- All quick-tier numbers above were re-measured after the question-generator fix (see below).
  Before it: Chromium 849 against 8,519 (right file first 75% against 38%), click 1,203 against
  21,765 (90% against 27%), the Nexus-MCP source 1,366 against 5,339. Every verdict stayed the
  same.
- `scripts/check_docs.py` (in CI): versions agree with `pyproject.toml`, every "N tools" claim
  matches `server.py`, no hand-written test counts, every docs page is in the index, and the
  generated pages are current. An offline lychee link check runs in CI. New pages:
  `docs/CONFIGURATION.md` (generated from `config.py` by `scripts/gen_config_docs.py`),
  `docs/FAQ.md`, `docs/RELEASING.md`. The stale "1,132 tests" counts are removed.
- PyPI metadata: new description, keywords, classifiers and project URLs. README links are
  absolute, so they work on PyPI and Glama.

- First paid agent-tier runs (`eval --example --agent claude:haiku --tasks 2`, 2026-11-03):
  baseline 2 of 2 right at $0.030 per task; nexus called `search` in every run, $0.022 to
  $0.034 per task. Two tasks is far too few to compare cost. The runs found three gaps, now
  fixed: the report showed no tool names (it now has a "Used Nexus" column and flags a nexus
  row that never called Nexus-MCP), a timed-out run lost all its output (it now keeps the
  partial tool calls), and answers were dropped (the local report now keeps them, capped at
  500 characters; `eval share` still never sends run records).
- Question generator fix: a method whose bare name is defined in two files (for example
  `Graph.get_relationships_to` and `Engine.get_relationships_to`) is no longer a question,
  because both files are right answers. Found by the paid run above, which "missed" such a
  question. Quick-tier numbers were re-measured with the fix.
- `benchmarks/runner.py --repo-path PATH` runs the benchmark on any local folder, and
  `--generate N` makes N tasks from its symbols (no YAML to write).
- `evals/retrieval/run.py --root PATH --generate N` runs the retrieval eval on any folder. On
  `src/nexus_mcp` (20 generated queries): hybrid hit@1 0.85, BM25 0.90.

### Changed

- `benchmarks/transcript.py` moved to `nexus_mcp.evaluate.transcript`, so the eval CLI ships it.
  The old module re-exports it.

## [2.1.0] - 2026-10-28

### Breaking Changes

- The `explain` and `health` tools are removed from the MCP tool list (10 tools to 8).
  `find_symbol` now returns the code of a symbol with its callers and callees. Both functions
  stay in `core_api`.
- Parameters removed from the MCP schemas (they stay in `core_api`): `search` `language`,
  `symbol_type`, `mode`, `rerank`, `live_grep`, `detail`; `find_symbol` `detail`; `graph`
  `max_depth`, `detail`; `memory` `memory_type`, `ttl`, `project`, `limit`. A client that
  sends one now gets a schema error.
- `find_symbol(name=)` is now `symbol_name`. An invalid enum value is a schema error.
- The FlashRank reranker is removed (`rerank`, the `reranker` extra, `setup.sh --reranker`,
  `NEXUS_RERANKER_MODEL`), and so is the graph list in fusion (`NEXUS_FUSION_WEIGHT_GRAPH`).
- An index of an older version is rebuilt in full on the first `index` call
  (`chunk_format` 3).

### Added

- **`find_symbol` shows an outline of a long class.** A class whose code is longer than 2,000
  characters gets `outline` in place of `code`: its head (class line, docstring, class
  attributes) and one line for each method with its lines (nested functions left out). The
  result gets a `hint` to ask for `Class.method`. A short class, a class with no methods in the
  graph, and every function keep their code. Reason: the django cost split (`docs/EVALS.md`)
  showed that a class body of up to 6,000 characters was larger than the read that it replaced.
  Result sizes on the django index: `CharField` 6,468 to 2,728 characters, `SQLCompiler` 6,675
  to 2,332, `URLResolver` 6,682 to 1,270, `Signal` 6,671 to 1,502. Live rerun of the nexus
  condition (36 runs for each project, against the stored Grep and Read runs): django $0.973 to
  **$0.863 (+8.9% to -3.4% against Grep and Read)**, 36 of 36 correct; jobscout $0.459 to
  **$0.446 (-19.7% to -22.0%)**, 35 of 36 correct (the wrong run did not call `find_symbol`).
  One jobscout question (gateway flow) went from 5 to 10 turns after two outlines. A later run
  (2026-10-28) showed that the same code varies by about 20% in cost over 3 runs, so these
  changes of cost are inside the noise; the drop in result size is real.

- **Tried and discarded: a smaller budget for `find_symbol` with several names** (2026-10-28).
  Places only for a bare name with more than 3 definitions, one code definition for each name
  in a call with several names, and a cap of 2,500 characters for an outline. `find_symbol`
  output on django fell by 25%, but the cost did not fall (django $0.863 to $0.902, jobscout
  $0.446 to $0.531, a run where the change did not fire). Not merged; the code is on the local
  branch `discarded-find-symbol-budget`. Details in `docs/EVALS.md`.

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

- **Security: the grep fallback of `search` passed the query as a flag.** A query that starts
  with `-` reached `rg` or `grep` as an option. With ripgrep, `--pre=<command>` runs a command
  on each file. The query now comes after `-e`.
- **A failed memory migration lost the memories on the next start.** The temp table holds the
  only copy with vectors when the refill of the new table fails. The next start dropped it as a
  leftover. Now the rows that only the temp table holds are copied back first.
- **A cancelled `index()` broke `_pipeline_lock`.** A cancel while the call waited for the lock
  leaked it, and every later `index()` hung. A cancel during indexing freed the lock while the
  work still ran. The lock and the work now run in one shielded worker thread.
- `find_symbol` gave a cut body, not an outline, for a long class that came after big symbols
  in one call. The decision now uses the source before the budget cut.
- `search` could fall back to BM25 with no error when a background reindex unloaded the model
  during the search. The search now keeps its own reference to the model.
- `search` no longer shows stored snippets of files that changed after indexing. After a
  restore, the stored index can come from the repository itself (a shipped `.nexus` folder).
  Such results have `changed_since_index` and no snippet.
- The grep fallback of `search` and the `references` of `graph` now search every root of a
  multi-root index, not only the first. With several roots, a path starts with its root folder.

- `find_symbol("Class.method", exact=False)` always returned "not found". A method is in the
  graph by its bare name, so a dotted name resolves only through the chunk table, and that
  lookup ran only with `exact=True`. It now runs in both modes. Found in the django traces: 5 of
  9 calls of `find_symbol("Field.validate", exact=False)` failed, and the agent went on with
  Grep and Read. On the django index the call now gives the same 1,699 characters as with
  `exact=True`. Not measured in a live run.

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
