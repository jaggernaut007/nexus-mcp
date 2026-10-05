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

**Prompts.** 25 prompts in `evals/routing/prompts.yaml`: search, symbol, graph, map, analyze and
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

### Results (2026-10-05)

`mcp-only` condition, Tool Search on, model `sonnet` (Claude Code 2.1.280), one run for each of the
25 prompts. **Before** is the source at tag `eval-baseline-descriptions` (old descriptions, no server
instructions, no enums or annotations, no session restore). **After** is commit `7517bec`.

| | Before | After |
|---|---|---|
| Pass | 56% (14 of 25) | **88% (22 of 25)** |
| Nexus tool called first | 27% | **86%** |
| Right tool within 3 counted calls | 50% | 86% |
| Right arguments | 50% | 86% |
| Negatives (grep or no tool is right) | 100% | 100% |
| Median wall time per run | 10 s | 17 s |

| Category (prompts) | Before | After |
|---|---|---|
| search (5) | 40% | 100% |
| symbol (4) | 25% | 75% |
| graph (4) | 75% | 100% |
| map (3) | 67% | 100% |
| analyze (3) | 67% | 67% |
| memory (3) | 33% | 67% |
| negative (3) | 100% | 100% |

Eight prompts went from fail to pass and none went from pass to fail. Before, Claude usually reached
for Grep, Glob or Read first, and used a nexus tool only after those. After, it loads the tool
through Tool Search and calls it first. No run had an isolation problem or a denied call.

The three prompts that still fail:

- `symbol-reserve` ("Where is reserve_stock defined?"): Claude used Grep. The descriptions say that
  built-in grep is as good for a name you already know, so this answer is defensible. The prompt
  stays in the set; it is a prompt on which the eval and the guidance disagree.
- `analyze-quality` ("Review the code quality of the shop/payments directory"): Claude globbed and
  read the files.
- `memory-store-decision` ("Remember that we decided..."): Claude read and wrote a file. Claude
  Code has its own memory convention, and the `memory` description does not beat it here.

**What this does and does not show.** It is one model, one small repository, one run per prompt, and
prompts that the maintainer wrote, so a change of one or two prompts is noise. The gain is large and
consistent across categories, so it is unlikely to be noise, but it is not a measure of real-world
use. The "before" run differs from "after" in more than the descriptions: it also lacks the server
instructions, the enums and annotations, and the session restore (it had to call `index` first). To
attribute the gain to each part, run each change alone. The cost is time: a run is 7 seconds longer
at the median, because Claude now loads and calls nexus tools where it used to grep.

**Not yet measured:** Tool Search off, the `nexus` condition (with the plugin skill), more than one
run per prompt, and Codex.

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
