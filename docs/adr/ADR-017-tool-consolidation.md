# ADR-017: Tool Consolidation (15 → 10)

## Status: Accepted
## Date: 2026-07-02

## Context

MCP Tool Search shipped as the default discovery mechanism in Claude Code in early
2026: tool schemas above a size threshold are deferred, and the agent discovers them
on demand by matching name + description. This changes the economics of tool design —
a server's tool *count* and *description quality* now directly determine
discoverability and token overhead, and current guidance favors few, outcome-oriented
tools over many thin ones that each do one narrow thing.

Nexus-MCP's 15-tool surface had three internally-inconsistent clusters that were
really one capability each, split across multiple tool names for historical reasons:

- `find_callers`, `find_callees`, `impact` — all graph traversal from a symbol,
  differing only in direction and whether the closure is transitive.
- `overview`, `architecture` — both project-level structural summaries at different
  granularities, both read from the same graph/analyzer.
- `remember`, `recall`, `forget` — all operations on the same memory store,
  differing only in the CRUD verb.

A companion internal audit (2026-07) confirmed this: each cluster's tools shared
nearly all of their helper calls (`_resolve_symbol`, `_require_indexed`,
`CodeAnalyzer`, `_get_memory_store`) and differed only in a handful of lines.

## Decision

Merge each cluster into one tool with a discriminator parameter:

- **`graph(symbol_name, direction="callers"|"callees", transitive=False, max_depth=10)`**
  replaces `find_callers`/`find_callees`/`impact`. `transitive=True` requires
  `direction="callers"` (the graph engine only implements
  `get_transitive_callers`, not `get_transitive_callees` — asking for
  `direction="callees", transitive=True` returns a clear error rather than silently
  doing the wrong thing).
- **`map(detail="summary"|"architecture"|"full")`** replaces `overview`/`architecture`.
  `"summary"` and `"architecture"` return byte-identical shapes to the old
  `overview()`/`architecture()` responses; `"full"` is a flat merge of both (verified
  no key collisions between the two response shapes).
- **`memory(action="store"|"search"|"delete", ...)`** replaces
  `remember`/`recall`/`forget`.

Final surface: `index, status, health, search, find_symbol, analyze, explain, graph,
map, memory` — 10 tools. `find_symbol`, `explain`, and `analyze` were not merge
candidates: `explain` is a synthesized combined view (graph + vector + analysis) via
`ResponseBuilder`, a different abstraction level than a thin dispatch, and merging it
into `graph`/`map` would conflate the two.

Every old tool body was moved into a private helper function (`_graph_immediate`,
`_graph_transitive_impact`, `_build_overview`, `_build_architecture`,
`_memory_store_action`, `_memory_search_action`, `_memory_delete_action`) with the
logic unchanged — the merge is a dispatch layer, not a rewrite, to minimize the risk
of behavioral drift.

### Clean break, no deprecated aliases

Confirmed with the maintainer: the 8 removed tool names are not kept as aliases.
Nexus-MCP is a published PyPI package, so this is a genuine breaking change (v2.0.0,
not a minor bump) — but keeping both surfaces indefinitely would defeat the actual
goal (fewer tools for Tool Search to route). See `CHANGELOG.md` for the old→new
mapping shipped to users.

### `graph()`'s permission category: READ, not the old MUTATE

Before this change, `find_callers`/`find_callees` were `ToolCategory.READ` but
`impact` was `ToolCategory.MUTATE`, grouped in `security/permissions.py` alongside
`index`/`analyze` under a comment reading "triggers computation, disk writes." None of
the three graph-traversal tools ever mutate persisted state — `impact`'s MUTATE tag
reflected computational cost (a transitive graph walk), not data safety. A single
merged tool needs one category; **READ is correct**, not a loosening, since nothing in
`graph()` — transitive or not — writes anything. This is disclosed here rather than
silently changed: under `NEXUS_PERMISSION_LEVEL=read`, transitive impact analysis is
now allowed where it previously wasn't. Rate limiting (not permissions) is the correct
lever for the cost concern — `TOOL_RATE_OVERRIDES["graph"] = (5.0, 10)` (vs. the
lighter-weight tools' 10/s) covers the heavier transitive path.

### `memory()`'s permission category: action-aware, not a single static value

This cluster could *not* use the same reasoning as `graph()`. Before this change:
`remember`=WRITE, `forget`=WRITE, `recall`=READ — and unlike `impact`, these
categories reflect real semantics: `remember`/`forget` genuinely mutate the memory
store; `recall` genuinely doesn't. A single static category for `memory` would either:

- silently **over-permit** writes under a `read`-only policy (category=READ), or
- silently **over-restrict** search under a `read`-only policy (category=WRITE).

Both are real regressions for anyone running `NEXUS_PERMISSION_LEVEL=read`. The fix:
extend the permission-check plumbing to accept a per-call category override —

```python
check_permission(tool_name, policy, category_override=None)  # security/permissions.py
_check_tool_permission(tool_name, category_override=None)    # server.py
_guard(tool_name, category_override=None)                     # server.py
```

`category_override` wins over the static `TOOL_PERMISSIONS` registry lookup when
provided; every other call site passes no override and is byte-identical to the old
behavior. `memory()` computes the override from its `action` parameter before calling
`_guard`:

```python
{"store": ToolCategory.WRITE, "search": ToolCategory.READ, "delete": ToolCategory.WRITE}
```

`TOOL_PERMISSIONS["memory"] = ToolCategory.WRITE` remains as the static fallback (the
more restrictive of the two categories), reached only if the action dispatch is ever
bypassed. Verified with a direct test: `memory(action="search")` is allowed and
`memory(action="store")` is denied under `NEXUS_PERMISSION_LEVEL=read`, matching the
pre-consolidation `recall`/`remember` behavior exactly.

### `map` shadows the Python builtin

Naming the tool `map` (matching the roadmap's original design) shadows Python's
builtin `map()` within `create_server()`'s scope for any code defined after it —
confirmed no builtin `map()` calls exist later in the file, so this isn't a live bug,
but it's a real footgun for future maintainers. Fixed by defining the function as
`_map_impl`, renaming `_map_impl.__name__ = "map"`, and applying
`mcp.tool(name="map")(_audited(_map_impl))` as plain calls instead of `@` decorator
syntax (the rename must happen between the two decorators, which `@`-stacking can't
express). This also keeps the audit log's `tool_name` field correct — `_audited`
derives it from `fn.__name__`, so without the rename it would have logged
`"map_tool"`/`"_map_impl"` while every permission/rate-limit check said `"map"`.

## Consequences

- Tool count drops from 15 to 10; each remaining tool's description was rewritten as
  a Tool-Search routing rule (front-loaded when-to-use guidance).
- Breaking change for any existing integration or `CLAUDE.md` that hardcodes the 8
  removed tool names — mitigated by the CHANGELOG mapping and a version bump to
  2.0.0, not silently shipped in a patch release.
- The permission model gained its first per-call category override, a small,
  targeted, backward-compatible extension rather than a rewrite — precedent for any
  future tool whose real category depends on a parameter rather than its name alone.
- `graph(transitive=True)` (impact analysis) is reachable under `read`-only policy
  where it previously wasn't — a disclosed, deliberate simplification, not an
  oversight.

## Alternatives Considered

- **Keep old tool names as deprecated aliases delegating to the new tools**: Rejected
  per the maintainer's explicit choice — see "Clean break" above. Revisit only if
  real-world breakage from existing integrations turns out to be worse than expected.
- **Static WRITE category for `memory`, always**: Simpler, but silently blocks
  `action="search"` under `read`-only policy — a real functional regression for the
  most common memory operation. Rejected.
- **Static READ category for `memory`, always**: Simpler, but silently allows
  `action="store"/"delete"` under `read`-only policy — a real security-relevant
  loosening (actual persisted-state writes, unlike `graph()`'s case). Rejected.
- **Nest `map(detail="full")`'s response under `overview`/`architecture` keys**:
  Considered for clarity, but the two response shapes have zero key collisions, so a
  flat merge preserves exact backward-compatible field access for anyone who already
  parses `overview()`'s or `architecture()`'s shape and switches to `detail="full"`.
