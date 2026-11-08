# Nexus-MCP plugin

Fewer tokens for Claude Code on large codebases. Nexus-MCP is a local MCP server with hybrid code
search, a static call graph for 10 languages and the code of any symbol, so the agent reads
fewer files.

## Results

Live benchmark of 2.4.1: Claude Code, Claude Sonnet at medium effort, 6 tasks × 3 runs per
project, against the built-in Grep and Read tools.

| Project | Tokens, sum of 18 runs | Cost | Correct answers |
|---|---|---|---|
| Home Assistant (about 26,000 Python files) | 49% fewer | 32% lower | 18 of 18 (Grep 17) |
| Django | 27% fewer | 27% lower | 17 of 18 (Grep 16) |

On Django it used fewer tokens on 2 of the 6 tasks; the large impact tasks give the win. Grep
totals vary from run to run. Method and all numbers:
[benchmarks](https://jaggernaut007.github.io/nexus-mcp/benchmarks.html).

## What you need

- [uv](https://docs.astral.sh/uv/). The plugin starts the server with
  `uvx nexus-mcp-ci==<version>`, so there is no `pip install`.
- The first index downloads the embedding model `BAAI/bge-small-en-v1.5` from Hugging Face
  (about 130 MB), once.

## What is in the plugin

| Part | What it does |
|---|---|
| MCP server `nexus-mcp` | 5 tools: `index`, `search`, `find_symbol`, `graph`, `map`. Set `NEXUS_EXTRA_TOOLS=all` to add `status`, `analyze` and `memory`. |
| Skill `nexus-mcp` | Tells Claude when to use the tools instead of reading files one by one. |
| Skill `evaluate` | Runs the free offline check `nexus-mcp-ci eval` on your project and explains the verdict. |
| Agent `nexus-evaluator` | Runs the same offline check and returns only the summary. |

## Data and privacy

The server runs on your machine. It makes no network call of its own, has no telemetry and
needs no API key. The index (code snippets and vectors) is stored in `<project>/.nexus/`.

Data leaves the machine only in these opt-in steps of the `evaluate` skill. Each one runs only
after you say yes:

| Step | Sent to | What |
|---|---|---|
| Agent tier (`eval --agent`) | Anthropic or OpenAI, through **your own** `claude` or `codex` CLI | the prompts and the code that the agent reads; it spends your own LLM API tokens |
| Share (`eval share`) | a GitHub issue on this repository | allow-listed numbers only: no paths, no code, no question text. You see the exact payload first. |

The plugin itself keeps no data: there is no Nexus-MCP service. Full details:
[PRIVACY.md](https://github.com/jaggernaut007/Nexus-MCP/blob/main/docs/PRIVACY.md).

## Links

- [Documentation](https://github.com/jaggernaut007/Nexus-MCP/blob/main/docs/README.md)
- [Issues and support](https://github.com/jaggernaut007/Nexus-MCP/issues)
- License: [MIT](https://github.com/jaggernaut007/Nexus-MCP/blob/main/LICENSE)
