# ADR-027: Linked Repos: One Index per Repo, a User-Level Registry, Service Calls as the Connection

## Status: Draft (Part 2, built in phases 2.1 to 2.5; the PRs are drafts)
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
  uses the same RRF, test-file order and exact-name rule as before. A store divides its BM25
  scores by its own best score, so the first version gave the best hit of every store the
  score 1 and a weak linked match outranked the second result of the project; phase 2.5
  scales the BM25 lists of all stores by the best raw score of all (`_merge_bm25`). A result of a linked repo has the
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
  `EXTRACTOR_VERSION` is 3 (2 added routes and calls; 3, in phase 2.4, marks a call to a host outside the project). An index of version 1 has no
  routes: `link` and `link --refresh` build its graph again with `IndexingPipeline.rebuild_graph`
  (no embedding model runs, the vectors stay). An incremental reindex keeps the stored version,
  because it parses only the changed files; a full index writes the current one. The primary
  project is not rebuilt by a call: its routes appear after the next full index.
- **Service calls are the connection** (phases 2.3 and 2.4): routes and HTTP client calls are
  extracted per repo, and a resolver makes bridge edges between graphs, with precision first
  (ADR-019). Each repo keeps its own graph, because `resolve_calls` rebuilds the CALLS edges
  of one graph and its unique-name tier must not see the names of another repo. Bridge edges
  live in memory and are not persisted.
- **The service resolver and `graph` across repos** (phase 2.4, `indexing/service_resolver.py`).
  `resolve_services` matches the client calls and the routes of all graphs of a session: the
  primary project, each attached linked repo, and inside one graph (a monorepo with a front end
  and a back end folder). An edge is a `RelationshipType.HTTP_CALLS` relationship from the client
  node to the route handler, with `via` (`GET /orders/{}`), `resolution` and `strength`. Precision
  first: an edge exists only when exactly one route fits at the first tier that has a candidate.
  Tier `exact` (strength 1.0): method and full path are equal; an `ANY` route fits every method;
  two fitting routes give no edge and the next tier is not tried. Tier `suffix` (0.8): a route
  path equals the end of the client path at a segment border (`/api/v1/orders` and `/orders`),
  and the part that is cut off has literal segments only (`/users/{}/orders` is not matched to
  `/orders`); a route with no literal segment never fits this way. A call whose URL starts with
  a literal host that has a dot (`https://api.stripe.com/v1/charges`) has `external: true`, set by
  the extractor, and gets no edge: `localhost`, a host with no dot (a container service name) and
  a URL that starts with a variable are not external. If the only exact fit is the client itself,
  the suffix tier does not run. A function never gets an edge to itself.
  Calls and routes in test files are left out; the path is taken inside the repo root, so a
  repo below a folder named `tests` is not left out. The edges live in a `ServiceBridge` in
  `state.bridge`. No graph holds them, `resolve_calls` does not see them and the saved graph
  has none. The bridge is built again in `_attach_linked` (restore and `index`) and after a
  background reindex. A failure leaves `state.bridge` empty and `graph` answers from the
  CALLS edges. `graph` looks for the symbol in the primary project and in each linked repo, as
  `find_symbol` does, and follows CALLS and HTTP_CALLS: `callers`, `callees` and
  `transitive=true` all cross repos. A hop over an HTTP edge ends with `via METHOD /path` in
  the compact answer; `detail="full"` adds `via`, `resolution` and, for a linked repo, `repo`.
  The paths of a linked repo start with its name. Overrides and text references are those of
  the primary project, so a symbol that only a linked repo holds has none. With no linked repo
  and no HTTP edge the answer is the same as before 2.4. Not done in 2.4: `find_symbol` does
  not list the HTTP hops, and the server instructions are not changed (a line would add tokens
  for every project, also with no link). Decision of 2.5: they stay as they are; the plugin skill
  and `gemini-context.md` carry the two lines, and a line in the instructions waits for a
  measurement of the tokens with the live benchmark.
- **Evals and the BM25 merge** (phase 2.5). The fixture `tests/fixtures/linked` (a FastAPI
  backend, a TypeScript frontend, decoys) and the labels in `evals/linked/labels.yaml` are the
  edge eval: `python -m evals.linked.run` gives precision and recall of the HTTP edges against a
  list that a person wrote by hand. The gate is precision >= 0.9, no edge missing that is not a
  `known_miss`, and at least one edge when labels exist. On the fixture: precision 1.0, recall
  0.9 (the one miss is a path in a table, `axios.get(ENDPOINTS.orders)`). A public full-stack
  project is not labelled yet. `python -m evals.linked.with_link` measures `search` with a link:
  it found that the per-store BM25 scaling lowered hit@1 of `shop_repo` from 0.75 to 0.625 (2
  runs of 4, 3 of 6 in a second check) when a bookshop backend was linked. The BM25 lists are
  now scaled together: hit@1 stays at 0.75 in all 6 runs. The cost is on the other side: four
  queries about the backend found a file of the linked repo first in 0.79 of the cases before
  and 0.54 after (mean of 6 runs). Two of the four ("cancel an order", "charge a card") are
  ambiguous with the shop's own code, and now go to the project. The raw BM25 score is not
  equal for one text in two stores: it grows with the log of the number of chunks, so a large
  linked repo next to a small project can outrank it. A correction by `ln(1 + chunks)` was
  tried and kept the linked side but lost the project side in one run of six, so it was not
  adopted. The decision is the owner's: a rule that prefers the project in ambiguous cases was
  chosen because the north-star gate is the accuracy for the project.

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
