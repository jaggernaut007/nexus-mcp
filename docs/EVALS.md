# Evals

Three evals measure what nexus-mcp claims. Run them before you change a tool description,
the ranking or the embedding model.

| Eval | Question | Code | Cost |
|---|---|---|---|
| Routing | Does Claude call the right nexus tool without being told? | `evals/routing` | Claude usage (small) |
| Retrieval | Does a model rank the right files first? | `evals/retrieval` | CPU time only |
| Benchmark | Does nexus-mcp cut tokens on a large repository? | `benchmarks/` | Claude usage (large) |

## Routing eval

**Setup.** Each run starts `claude -p` headless in a copy of the `shop_repo` fixture
(`evals/fixtures/shop_repo`, 18 files, Python and TypeScript). The nexus server is connected from
this checkout's source, so a description change is measured as soon as you make it. The prompt
names no tool: "Who calls reserve_stock?". The runner stops the process after three tool calls that are not tool discovery
(`ToolSearch`) or session set-up (`status`, `index`, `health`), or after eight calls of any kind.

**Conditions.** `mcp-only` (the server, no skill) measures the descriptions and the server
instructions alone. `nexus` adds the plugin skill to the system prompt. Each condition runs with
Tool Search on (the Claude Code default; the model sees tool names and instructions first) and off
(all tools load up front).

**Prompts.** 26 prompts in `evals/routing/prompts.yaml`: search, symbol, graph, map, analyze and
memory questions, plus three negatives where the right move is a built-in tool or no tool.

**Metrics.** A run passes when one of the expected nexus tools appears within the first three
counted tool calls (`ToolSearch`, `status`, `index` and `health` do not count) with the right
arguments. The report also gives the
nexus-first rate, the right-tool rate, the argument rate and the negative pass rate.

**Isolation.** The run uses its own config directory and `--setting-sources ""`. It records the
`system/init` event and drops a run if a plugin or an extra MCP server leaked in. It does not
use `bypassPermissions`: it runs with `--permission-mode dontAsk` and an allowlist, and records
every denied call.

**Auth.** The isolated config directory has no login. Export `CLAUDE_CODE_OAUTH_TOKEN` (from
`claude setup-token`) or `ANTHROPIC_API_KEY` in the shell before you start.

```bash
python -m evals.routing.runner --smoke                 # 4 prompts, a quick check
python -m evals.routing.runner --label before          # full run, resumable
python -m evals.routing.report "evals/results/routing-*.jsonl"
```

A run that already has a record is skipped, so an interrupted run (for example by the usage
limit) resumes when you start it again with the same `--label`.

**Limits.** One model (`sonnet`), one repository, one repetition by default. The prompts are
written by the maintainer. Treat a difference of one or two prompts as noise.

### Results

Not run yet: the first run needs `CLAUDE_CODE_OAUTH_TOKEN`. The commit `eval-baseline-descriptions`
(tag) holds the descriptions from before the rewrite, so the baseline can still be measured from
a checkout of that tag.

## Retrieval eval

Compares embedding models on file-level search quality over two corpora (this repository's
source, and the fixture). It runs offline on CPU. See
[research/embedding-models-2026-10.md](research/embedding-models-2026-10.md) for the method and
the results.

```bash
python -m evals.retrieval.run --candidates bge-small-en --label baseline
```

## Benchmark

The token-efficiency benchmark (`benchmarks/`, ADR-018) drives `claude -p` on large repositories
and counts wasted reads and tokens to answer. The harness is built and tested. It has no
published numbers yet. See `benchmarks/README.md` for the commands.
