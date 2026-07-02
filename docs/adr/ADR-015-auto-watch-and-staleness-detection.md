# ADR-015: Auto-Watch and Staleness Detection

## Status: Accepted
## Date: 2026-07-02

## Context

Nexus-MCP's index only ever changed when a client explicitly called `index()` again.
In practice this meant search results silently went stale the moment a user edited a
file or switched git branches — nothing in `status()` or `search()` indicated that the
underlying code had moved on. A `DebouncedFileWatcher` (`parsing/file_watcher.py`) had
already been built to solve exactly this, but was never instantiated anywhere in the
server.

An internal product audit (2026-07) flagged this as the top daily-driver blocker:
"a user who indexes, edits files, and searches again is searching stale code without
realizing it." This ADR covers the fix.

## Decision

### Detection: mtime-diffing, no dedicated git plumbing

`IndexingPipeline.check_staleness(codebase_paths)` reuses the same mtime-diff logic
`incremental_index()` already relies on (`_diff_mtimes`, `_discover_mtimes`) — it's
read-only and never mutates engines or metadata. Branch switches are covered for free:
`git checkout` touches file mtimes, so a mtime diff against the last indexed state
already detects a branch switch as "changed files," without tracking git HEAD
separately.

### Auto-watch: on by default

`NEXUS_AUTO_WATCH` defaults to `true`. After a successful `index()` call, the server
starts one `DebouncedFileWatcher` per indexed root (`state._file_watchers`). On a
debounced change event, the watcher triggers a background incremental reindex rather
than blocking. This was a deliberate default-on choice, weighed against the resource
cost of a background watchdog thread for the life of the session — daily-driver
usability was judged more important than avoiding that fixed cost, and the debounce
(2s default) keeps reindex churn bounded during active editing.

### Staleness surfacing: warn + background reindex, never block

When `status()` or `search()` detect staleness, they:
1. Return their result immediately (`stale`/`staleness_warning` on `status()`,
   `warning` on `search()`) — no added latency on the call that discovers staleness.
2. Fire `_trigger_background_reindex()` so the *next* call is fresh.

Two alternatives were rejected: warn-only (staleness would persist until the user
manually re-indexed, defeating the point of auto-watch existing) and
block-and-reindex-synchronously (adds unpredictable multi-second-to-minute latency to
whichever call happens to trip it — unacceptable for a tool whose north star is
performance).

### Throttling: the staleness check itself has a cost

`check_staleness()` still does a full `discover_files()` + per-file `stat()` walk —
O(files). Running that on every single `search()` call would regress token/latency
efficiency on large repos, which is precisely the thing this whole effort is trying to
protect. `state._staleness_cache` + `state._staleness_checked_at` throttle real
recomputation to once per `NEXUS_STALENESS_CHECK_INTERVAL` (default 15s). This is a
safety net for cross-session drift and for when auto-watch is disabled — when the
watcher is running, staleness rarely trips in the first place, so the throttle window
is rarely on the hot path at all.

### Concurrency: one shared lock, non-blocking on the background path

A foreground `index()` call and a background reindex (watcher- or staleness-triggered)
both mutate the same pipeline's vector/graph engines. Rather than add a second lock,
`index()` now holds the existing `_pipeline_lock` for its *entire* duration (previously
only during lazy pipeline creation), and `_trigger_background_reindex()` uses
`_pipeline_lock.acquire(blocking=False)` — if a foreground index is already running (or
another background reindex already is), the trigger is skipped and logged, not queued
or blocked. This also gives "one in-flight background reindex at a time" for free: a
burst of debounced file-save events can't spawn overlapping reindex threads, since the
first one holds the lock for its whole run.

The watcher's callback itself must stay non-blocking: it runs as an `asyncio.Task` on
the server's event loop (`DebouncedFileWatcher._debounced_callback`), so it only
*triggers* the background thread and returns immediately — it never calls
`pipeline.incremental_index()` directly, which would freeze the entire stdio server
for the duration of the reindex.

### Progress reporting requires `index()` to go async

Making `index()` report live progress (see companion work in the same change) requires
`ctx: Context` injection, which requires `async def`. Wiring the watcher's
`await watcher.start()` piggybacks on that same async conversion rather than
introducing a second async entry point.

## Consequences

- Search results carry an explicit signal when they might be stale, instead of
  silently drifting.
- A background watchdog thread runs for the life of any session with an indexed
  codebase (opt-out via `NEXUS_AUTO_WATCH=false`).
- `index()` now holds `_pipeline_lock` for its full duration rather than just at
  creation — tightens a latent race that existed even before this change (nothing
  previously prevented two concurrent `index()` calls from mutating the same pipeline).
- Shutdown (`state.shutdown()`) must stop watchers before persisting graph state; since
  it runs after `server.run()` has already returned, from a context with no running
  event loop, it uses `asyncio.run()` to drive the async `stop()`.

## Alternatives Considered

- **Dedicated git-HEAD tracking**: Would add a new state field and a git subprocess
  call on every check. Rejected — mtime-diffing already catches branch switches as a
  side effect of the same mechanism `incremental_index()` uses.
- **Polling instead of a watcher (stat all files every N seconds)**: Simpler, but
  either wastes CPU polling frequently or has poor latency polling infrequently. A
  filesystem watcher (`watchdog`, already a dependency) gets near-instant notification
  for free.
- **Block search() until reindexed**: Rejected for unpredictable latency (see above).
