---
name: nexus-mcp
description: Answer questions about how a codebase works with the nexus-mcp tools instead of reading files one by one. Use when asked where something is, how it works, who calls a function, what breaks if it changes, how the project is laid out, which code is complex or dead, or to keep and recall a project decision across sessions. No setup call is needed.
---

# Using nexus-mcp

Tool names below are bare. Your client may add a prefix. The server instructions say which
tool answers which question; this page adds only what they do not.

- The index of an earlier session is attached when the server starts. Do not call `status`
  first. If a tool answers "No codebase indexed", call `index` once with the absolute
  project path. A `warning` field means a background reindex is running.
- Each call costs one more read of the whole conversation. Prefer one `find_symbol` call
  with all the names to a grep or a file read for each name.
- A long class comes back as an outline. Ask for `Class.method` to get the code of a method.

## When not to use it

- You already know the exact file or string: use grep or read.
- The project is small (a few thousand lines): reading it directly is cheaper than indexing.
- Files outside the indexed path.

## Limits

- Call edges are static and name-based. Callbacks, reflection and dynamic dispatch do not
  appear, and a name shared by several definitions gets no edge. Treat the caller list as a
  lower bound. The `references` list in the same result shows every line with the name.
- `analyze` complexity is approximate, and dead-code entries only mean "no static caller".
