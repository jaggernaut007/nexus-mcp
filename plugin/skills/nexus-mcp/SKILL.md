---
name: nexus-mcp
description: Answer questions about how a codebase works with the nexus-mcp tools instead of reading files one by one. Use when asked where something is, how it works, who calls a function, what breaks if it changes, how the project is laid out, which code is complex or dead, or to keep and recall a project decision across sessions. No setup call is needed.
---

# Using nexus-mcp

Tool names below are bare. Your client may add a prefix.

## Start

1. Go straight to the tool for the question. The index of an earlier session is attached
   when the server starts. Do not call `status` first.
2. Only if a tool answers "No codebase indexed": call `index` once with the absolute
   project path.
3. The index keeps itself fresh. A `warning` field means a background reindex is running;
   the next call or two may lag the latest edit.
4. Answer from the result. `search` returns the whole code of its top three results, and
   `graph` returns every text reference of the name. Read a file or grep only for what
   the result does not show.

## Pick a tool by question

| The question | The tool |
|---|---|
| Where is... / how does... / find the code that... | `search` |
| A function or class I can name | `find_symbol`, then `explain` for the full picture |
| Who calls X / what does X call | `graph` (`direction` callers or callees) |
| What breaks if I change X | `graph` with `transitive=true`, before the edit |
| Overview / where do I start | `map` (`detail` summary, architecture or full) |
| Most complex code / dead code / quality | `analyze` (optional `path`) |
| Keep or recall a decision | `memory` (`action` store, search or delete) |

## When not to use it

- You already know the exact file or string: use grep or read.
- The project is small (a few thousand lines): reading it directly is cheaper than indexing.
- Files outside the indexed path.

## Limits

- Call edges are static and name-based. Callbacks, reflection and dynamic dispatch do not
  appear, and a name shared by several definitions gets no edge. Treat the caller list as a
  lower bound. The `references` list in the same result shows every line with the name.
- `analyze` complexity is approximate, and dead-code entries only mean "no static caller".
