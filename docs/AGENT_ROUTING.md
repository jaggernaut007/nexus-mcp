# Agent routing guide

How an agent should choose between the nexus-mcp tools and its built-in tools. Three
places carry this guidance, and they must agree:

| Where | Who reads it | Size limit | Source of truth |
|---|---|---|---|
| Server `instructions` (`SERVER_INSTRUCTIONS` in `server.py`) | Every MCP client, on connect | Claude Code cuts at 2,048 characters; Codex reads the first 512 first | Code |
| Tool descriptions (docstrings in `server.py`) | The model, when it picks a tool | 2,048 characters each in Claude Code | Code |
| Plugin skill (`plugin/skills/nexus-mcp/SKILL.md`) | Claude Code, when the skill loads | Keep the body under 500 tokens | This guide |

`tests/test_tool_contract.py` checks the limits, the enums and that the docs list exactly the
registered tools. `evals/routing` measures whether an agent really picks the right tool.

## Why the server sends instructions

With Claude Code's Tool Search, MCP tools are deferred. At the start of a session the model
sees tool names and the server instructions, not the descriptions. A name like `search` or
`map` is generic, so the instructions must say what the server is for and which tool answers
which question. Codex reads the instructions the same way.

## Rules for the text

1. Open with the task, not the tool: "where is...", "who calls...", "what breaks if...".
   Use the words that users say.
2. Say what comes back (for example "ranked snippets with file path and line range").
3. Say when not to use it. For an exact string or a known file, built-in grep or read is as good.
4. State limits plainly. Call edges are static, so graph results are a lower bound.
5. Name capabilities, not another harness's tool inventory.
6. Keep one source for each fact. The instructions route, the descriptions explain one tool,
   the skill adds the workflow and the scope rules.

## Question to tool

| The question | The tool |
|---|---|
| Where is... / how does... / find the code that... | `search` |
| A function or class that you can name | `find_symbol`, then `explain` |
| Who calls X / what does X call | `graph` |
| What breaks if I change X | `graph` with `transitive=true` |
| Overview / where do I start | `map` |
| Most complex code / dead code / quality | `analyze` |
| Keep or recall a decision | `memory` |

## Measure, then change

Change a description only with a before-and-after eval run:

```bash
python -m evals.routing.runner --label before
# edit server.py
python -m evals.routing.runner --label after
python -m evals.routing.report "evals/results/routing-*.jsonl"
```

See [docs/EVALS.md](EVALS.md) for the method and the results.
