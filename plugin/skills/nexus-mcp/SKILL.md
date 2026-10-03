---
name: nexus-mcp
description: Answer questions about how a codebase works with the nexus-mcp tools instead of reading files one by one. Use when asked where something is, how it works, who calls a function, what breaks if it changes, how the project is laid out, which code is complex or dead, or to keep and recall a project decision across sessions. Start with the status tool.
---

# Using nexus-mcp

Tool names below are bare. Your client may add a prefix.

## Start

1. Call `status`. If `indexed` is false, call `index` once with the absolute project path.
2. After that the index keeps itself fresh. A `stale` flag or a `warning` field means a
   background reindex is running; the next call or two may lag the latest edit.

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
  appear, and a name shared by several definitions gets no edge. Treat `graph` results as a
  lower bound and use `search` for the call sites it cannot see. No result does not mean unused.
- `analyze` complexity is approximate, and dead-code entries only mean "no static caller".
