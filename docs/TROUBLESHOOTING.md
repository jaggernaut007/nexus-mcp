# Troubleshooting

## Install and start

**`uvx nexus-mcp-ci` fails to resolve or build.** The package needs Python 3.10 to 3.12. Run
`uvx --python 3.12 nexus-mcp-ci eval`. With pip, use a 3.12 virtual environment.

**The MCP client shows the server as failed.** Run `nexus-mcp-ci` in a terminal: it must wait
on stdin with no error. A virtualenv install needs the full path to the binary in the client
config (`which nexus-mcp-ci`).

**The server is disconnected after a long break.** The server stops after 3 hours with no tool
call (`NEXUS_IDLE_TIMEOUT`), and when the client process that started it is gone. Reconnect it
in the client (`/mcp` in Claude Code); the stored index attaches again. Set
`NEXUS_IDLE_TIMEOUT=0` to keep it running.

**The server does not stop.** Each stop (stdin close, SIGTERM, SIGINT, SIGHUP, idle, orphan)
ends the process within `NEXUS_SHUTDOWN_TIMEOUT` (10 s) plus 5 s. A second Ctrl-C ends it at
once. A running index can delay the stop; the graph is then not saved, and the next start
finds the index out of date.

## The embedding model

**The first index is slow or fails with a network error.** The first run downloads
`BAAI/bge-small-en-v1.5` (about 130 MB) from Hugging Face. Check the network, or copy
`~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5` from another machine.

**I must work offline.** After one download, set `NEXUS_OFFLINE=1`. If the model is not cached,
the load fails with a clear error instead of a network call. `nexus-mcp-ci eval doctor` shows
whether the model is cached.

## The index

**Results look old.** The watcher reindexes a few seconds after an edit, and `status` and
`search` check for stale files every 15 seconds (`NEXUS_STALENESS_CHECK_INTERVAL`). A `warning`
field means a background reindex is running.

**The index is corrupt or from another model.** It is rebuilt on the next `index` call. To start
again: `nexus-mcp-ci data clear --index --graph --yes`, then call `index`.

**Memory use is high.** About 90 MB idle and 460 MB with the model loaded
([MEMORY.md](MEMORY.md)). The model unloads after indexing.

**`.nexus/` shows in `git status`.** Add `.nexus/` to `.gitignore`. The index holds code.
`nexus-mcp-ci data where` warns about this.

## The eval

**"Weak fit" on my project.** That is a valid result. On a small or very well named project,
Grep and Read are cheap. Try a larger project, or the agent tier for real cost.

**"No source code was indexed."** The folder has no files in a supported language, or they are
all ignored. Point `eval` at the source folder.

**`eval --agent` refuses to start.** In a script or CI there is no terminal for the y/N question:
add `--yes`. Check `claude --version` (or `codex --version`) and that the CLI is logged in.
`nexus-mcp-ci eval doctor` checks both.

**Codex runs fail.** Codex support is experimental and depends on the `codex exec` flags of
your version. Report the error line in an issue.

## Windows

Use forward slashes or quote paths with spaces. The eval baseline uses `grep` when `rg` is not
installed; install ripgrep for the same ranking as on other systems.
