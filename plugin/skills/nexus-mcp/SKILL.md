---
name: nexus-mcp
description: Answer questions about how a codebase works with the nexus-mcp tools instead of reading files one by one. Use when asked where something is, how it works, who calls a function, what breaks if it changes, or how the project is laid out. No setup call is needed.
---

# Using nexus-mcp

Tool names are bare; your client may add a prefix. The server instructions say which tool
answers which question. This page adds only what they do not.

- No setup call is needed. If a tool answers "No codebase indexed", call `index` once
  with the absolute project path. A `warning` field means a background reindex runs.
- Put all the names in one `find_symbol` call, not a grep or read for each.
- A long class comes back as an outline. Ask for `Class.method` to get a method's code.
- Do not use it when you know the exact file or string, for a project of a few thousand
  lines, or for files outside the indexed path.
- Call edges are static and name-based: callbacks, reflection and dynamic dispatch do not
  appear, and a name shared by several definitions gets no edge. Treat callers as a lower
  bound; `references` in the same result shows every line with the name.
