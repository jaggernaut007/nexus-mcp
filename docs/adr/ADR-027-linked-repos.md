# ADR-027: Linked Repos: One Index per Repo, a User-Level Registry, Service Calls as the Connection

## Status: Draft (Part 2, phases 2.1 to 2.3 of 5)
## Date: 2026-11-17

## Context

One store holds one set of roots today. A different set of roots makes a full rebuild. A
repo has no identity, and nothing connects a client call in one repo to the route that it
calls in another. Agents that work on a front end and a back end need both sides.

The owner decided on 2026-11-17:

- The connection between repos is the service call (HTTP, API), not a package import.
- Each repo has one index. Indexes are linked, not merged.
- A person links repos with a CLI command that writes a registry file.

## Decision

- **One index per repo.** Each repo keeps its store in `<repo>/.nexus`. The store gets a
  `repo_id` (uuid4, made one time and kept through every reindex, also after a rebuild of a
  corrupt index), a `repo_name` and an `extractor_version` in `index_metadata.json`.
- **A user-level registry, outside each repo.** `~/.nexus-mcp/links.json` (`NEXUS_LINKS_FILE`)
  maps the root of a project to its links: `{name, path, repo_id}`. It is not in `.nexus`
  because `core_api._stored_roots` does not trust the contents of `.nexus`: a clone or an
  archive can supply it. A link in a `.nexus` folder would let a repo choose what the server
  opens. The registry is written only by `nexus-mcp-ci link`, `unlink` and `link --refresh`,
  in one atomic step (a temporary file, then a rename).
- **The server attaches linked stores read-only.** The saved graph is opened with SQLite
  `mode=ro&immutable=1`: no pragma, no table creation, no `-wal` or `-shm` file. A write that
  runs at the same time is not seen, and a half-written file skips the link. The primary project stays in the fields of
  `SessionState`. Linked repos go in a new `state.linked` list of `LinkedRepo`. The attach
  runs at restore and after `index`. The server checks each linked store before it opens it,
  and trusts nothing in it: the roots in its metadata must be the folder of the registry, and
  its `repo_id` must be the one of the registry.
- **A link that cannot attach is skipped, with a reason**, and stays in the list so that
  `status` can show it: the folder is gone, there is no index, the metadata or the store is
  corrupt, the embedding model differs, the repo id changed, the index was built for another
  folder, or the saved graph is missing or older than the metadata. A skipped link never
  stops the primary project.
- **`search` and `find_symbol` look in the linked repos** (phase 2.2). `search` embeds the
  query one time (`embed_query`, then `query_vector`) and runs the vector and BM25 search in
  each attached store. It merges the vector lists by score and the BM25 lists by score, then
  uses the same RRF, test-file order and exact-name rule as before. The BM25 score is divided
  by the best score of its store, so the best keyword result of each store has the score 1:
  the stores are interleaved, not ranked against each other. A result of a linked repo has the
  repo name first in its path (`backend/src/app.py`). `find_symbol` resolves a name in the
  graph of each repo, the primary project first, and applies the same sort and the same cap
  (`MAX_MATCHES_PER_NAME`) to all matches. With `detail="full"` an entry has a `repo` field.
  The live-grep fallback of `search` still reads the primary roots only. `graph` does not
  cross repos until phase 2.4.
- **The BM25 engine of a linked store is read-only.** `IndexingPipeline(read_only=True)`
  gives `LanceDBBM25Engine(read_only=True)`: it never calls `create_fts_index`. It uses the
  full-text index that `link` made at index time. A store with no such index gives no keyword
  results, and the vector results remain.
- **A stale linked repo is reported, not fixed.** A `search` result in a changed file of a
  linked repo has no snippet and `changed_since_index`, and the `warning` names the repo and
  says `nexus-mcp-ci link --refresh`. A search never starts a reindex of a linked repo.
- **No new MCP tool and no new tool parameter.** `status` lists each linked repo (name, path,
  file count, stale flag, skip reason). With no link, every result is the same as before.
- **`nexus-mcp-ci link <path>` indexes the target** when it has no index, or an index with no
  or an older `extractor_version`, or an index that the current model cannot use. It refuses
  to replace an index that was built for other folders. `link --refresh` indexes the changed
  files of every linked repo again and saves the graph after the metadata.
- **A linked store is read-only for the server.** Staleness is reported, not fixed. The
  person runs `link --refresh`.
- **Routes and client calls are extracted per file** (phase 2.3, `parsing/http_extractor.py`).
  A route is `metadata["routes"] = [{method, path}]` on its handler function node. A client call
  is `metadata["http_calls"] = [{method, path}]` on the innermost function that holds it. A route
  or a call with no function node (an arrow function, a call at module level) goes on the module
  node of the file. A path is normalised: no scheme, host or base-URL variable, each parameter
  `{}`, no query string, no trailing slash. A path that is not a literal, or does not start with
  `/`, is left out (precision first, ADR-019). Covered: Python (FastAPI and Starlette-style
  decorators, Flask, `requests`, `httpx`, `client.<verb>`, `session.<verb>`; a prefix set in the
  same file by `APIRouter(prefix=)`, `Blueprint(url_prefix=)`, `include_router` or
  `register_blueprint`), JavaScript and TypeScript (Express-style `app.get("/x", handler)` with
  `use("/p", router)` in the same file, NestJS controllers, Next.js route files, `fetch`,
  `axios`, `ky`, `$fetch`). Not covered: Django `path()`, Go, Java, C#, PHP, gRPC, a prefix set
  in another file (the route path stays relative, and the resolver of 2.4 matches the end of the
  path), a path built by a function, `requests.request("GET", url)`, a `url=` keyword, a prefix
  or a NestJS path that is not a literal. A route needs a receiver that a known factory makes in
  the file (`express()`, `Router()`, `new Hono()`, `APIRouter()`, `Flask()`, ...) or one named
  `app`, `router`, `bp` or `blueprint`; `api.post("/orders", body)` is a client call, not a
  route. A path with no literal segment (`/{}`) is left out, because it would match every route
  of that depth. Known limits, left for later: a router that two files mount keeps one prefix;
  the Next.js path comes from the folders after the last `app` folder of the absolute path, so a
  repo below a folder named `app` can get extra segments; client calls in test files
  (`TestClient`) are not marked, and the resolver of 2.4 must leave test files out.
  `EXTRACTOR_VERSION` is 2. An index of version 1 has no
  routes: `link` and `link --refresh` build its graph again with `IndexingPipeline.rebuild_graph`
  (no embedding model runs, the vectors stay). An incremental reindex keeps the stored version,
  because it parses only the changed files; a full index writes the current one. The primary
  project is not rebuilt by a call: its routes appear after the next full index.
- **Service calls are the connection** (phases 2.3 and 2.4): routes and HTTP client calls are
  extracted per repo, and a resolver makes bridge edges between graphs, with precision first
  (ADR-019). Each repo keeps its own graph, because `resolve_calls` rebuilds the CALLS edges
  of one graph and its unique-name tier must not see the names of another repo. Bridge edges
  live in memory and are not persisted.

## Consequences

- Linking an index made before this version builds it again one time: it has no
  `extractor_version` and no `repo_id`.
- A repo can have one index only. A repo that a person indexes with several roots cannot be
  linked until its `.nexus` folder is removed.
- Two processes can write to the registry at the same time. The write is atomic, but the
  second writer can overwrite the first one's change. A lock is not in scope.
- The BM25 engine of a normal store creates its full-text index on the first search of a
  process (`ensure_fts_index(replace=True)`). That is a write. A linked store uses a read-only
  engine (phase 2.2), so a search never changes it. A linked store without a full-text index
  gives vector results only, until `link --refresh` runs.
- A link name is the first part of a result path. A name equal to a top-level folder of the
  primary project makes two paths look the same. Use `--name` to choose another one.
- Each search of a project with links reads every attached store, so a search costs a little
  more time for each link. With no link the code path is the same as before.
- The run of `link --refresh` while a server reads the same store is not locked. LanceDB
  readers see a consistent version, but the graph file is replaced. A lock between processes
  is in the "later" list of the plan.

## Alternatives considered

- **One merged index for all repos.** A rebuild for each change of the set, no stable
  identity, and the call resolver would see the names of every repo.
- **A link file inside `.nexus`.** Rejected for the trust rule above.
- **Package-import edges.** Later. Service calls need no package manifest and fit the
  stack where repos are deployed apart.
