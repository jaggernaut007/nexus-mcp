# ADR-022: Server Lifecycle — Idle Timeout, Orphan Exit and a Guaranteed Exit

## Status: Accepted
## Date: 2026-11-06

## Context

The stdio server ran until its client closed stdin or sent a signal. Three faults came from that:

- After SIGTERM, the server logged "Nexus-MCP shutdown complete." and stayed alive. Measured:
  the process was still running 20 s after the signal. The interpreter exit joins threads
  (the stdin reader, the index thread pool), and one of them did not end.
- When the client died and the stdin pipe stayed open (another process held it), the server
  ran with no client, about 460 MB with the model loaded.
- A server from a session that ended but was not stopped ran for days.

The shutdown also saved the graph while a background reindex changed it. The save reads
`graph_engine.nodes` while the index writes it, so the save can fail or write half a graph.

## Decision

- A new module, `lifecycle.py`, owns the exit. Every stop goes through `lifecycle.finish()`:
  stdin close, SIGTERM, SIGINT, SIGHUP, an idle timeout and an orphan check.
- `finish()` runs the cleanup on its own thread with a deadline (`NEXUS_SHUTDOWN_TIMEOUT`,
  default 10 s), flushes the logs and ends the process with `os._exit`. It does not use the
  normal interpreter exit, which can hang.
- A watchdog thread ends the process if `finish()` does not run in time. A second signal ends
  the process at once. A signal during the cleanup does not interrupt it.
- A monitor thread checks every `NEXUS_LIFECYCLE_CHECK_INTERVAL` seconds (default 5):
  - Idle: no tool call for `NEXUS_IDLE_TIMEOUT` seconds (default 10800, 3 hours; 0 is off).
    A tool call that runs, or an index that holds the pipeline lock, is not idle time. Only
    tool calls count: a ping or `tools/list` does not keep the server alive.
  - Orphan: the parent PID differs from the PID read at import (`NEXUS_EXIT_ON_PARENT_EXIT`).
    A first parent of PID 1 (init, launchd) turns this check off.
- The monitor stops the server with a SIGTERM to its own process, so the cleanup runs on the
  main thread after `server.run()` unwinds, as for a client's signal.
- `state.shutdown(wait_for_index_s)` waits for the pipeline lock before it saves the graph. If
  an index still runs at the deadline, the graph on disk stays as it was. The next restore
  finds the index out of date, as after a crash.
- Exit code 0 for a clean stop; 1 when the cleanup timed out, the watchdog fired or a second
  signal came.

## Consequences

- The process always ends, within `NEXUS_SHUTDOWN_TIMEOUT` plus 5 s after a stop.
- After the idle timeout, the client shows the server as disconnected. Reconnect it in the
  client (`/mcp` in Claude Code). The next start attaches the stored index (`NEXUS_AUTO_RESTORE`).
- `os._exit` skips `atexit` handlers. The server registers none; a new one must run in
  `state.shutdown()` instead.
- `server.main()` no longer returns. Tests replace `lifecycle._hard_exit` (in `conftest.py`).
- On Windows the orphan check does not work (the parent PID does not change), and the idle
  stop uses `_thread.interrupt_main`; the watchdog still ends the process.
- No tool output changes, so the token use of the benchmark does not change.
