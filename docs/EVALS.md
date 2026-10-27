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
arguments. "Nexus first" means that the first counted call is a nexus tool; set-up calls are skipped, so
`status` then `search` counts as nexus first. For the no-tool negative prompt every call counts,
including `status`. The report also gives the
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

**Not yet measured (2026-10-05 run):** Tool Search off, the `nexus` condition (with the plugin
skill), more than one run per prompt, and Codex. The 2026-10-26 run below has 3 runs per prompt.

### Results (2026-10-26): the 8-tool surface

The routing eval was rerun after `explain` and `health` left the tool list, the schemas lost their
unused parameters and the server instructions were shortened. Commit `3fa8810`, `mcp-only`
condition, Tool Search on, model `sonnet`, **3 runs for each of the 25 prompts (75 runs)**, 0 runs
left out, 0 isolation problems, 0 denied calls.

| | 2026-10-05 (10 tools, 1 run each) | 2026-10-26 (8 tools, 3 runs each) |
|---|---|---|
| Pass | 88% (22 of 25) | **97% (73 of 75)** |
| Nexus tool called first | 86% | 92% |
| Right tool within 3 counted calls | 86% | 97% |
| Right arguments | 86% | 97% |
| Negatives (grep or no tool is right) | 100% | 100% |

By category on 2026-10-26: search 100% (15 runs), symbol 100% (12), graph 100% (12), map 100% (9),
analyze 100% (9), negative 100% (9), memory 78% (9).

- The only failing prompt is `memory-store-decision`: 2 of 3 runs failed. In both, Claude called
  `Read` and `Glob` and then `ToolSearch`, and never reached `memory` within three counted calls;
  in the third run it called `ToolSearch` and then `memory`. Claude Code has its own memory
  convention, and this prompt also failed on 2026-10-05.
- `analyze-quality` failed on 2026-10-05. Now it passes in all 3 runs, but nexus is not called
  first: Claude calls `Glob`, then `analyze`, then `Read`.
- `symbol-reserve` ("Where is reserve_stock defined?") now gets `find_symbol` first in all 3
  runs. On 2026-10-05 Claude used Grep for it.
- "Nexus first" is below 100% for the negative prompts (a built-in tool is right there), for
  `analyze-quality` (3 runs) and for `memory-store-decision` (2 runs).
- The two runs differ in the tool list, the schemas, the instructions and the number of runs (1
  against 3), so the gain is not attributed to one change. What the run shows is that removing
  two tools and their unused parameters did **not** lower routing.
- Not measured: Tool Search off, the `nexus` condition (with the plugin skill), and Codex.

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
and counts wasted reads and tokens to answer. See `benchmarks/README.md` for the commands.
The raw traces are in `benchmarks/results/` (git-ignored: the project is private).

### Results (2026-10-08): jobscout, 12 questions, 3 runs each

Conditions `baseline` (Grep, Read, Glob) and `nexus@bge-small-en`. Model `sonnet`.
`python -m benchmarks.runner --tasks benchmarks/tasks/private/jobscout.yaml --conditions
baseline,nexus --embedding-models bge-small-en --reps 3 --warm-up --save-raw`. Commit `91a338e`
(module chunks, exact names, `find_symbol` with code, 8 tools, caps). `--warm-up` sends one
unrecorded call for a condition when its prompt cache is cold, so no run pays the cache write
of the prompt. Medians of the 3 runs of a question; the last row adds the medians.

| Task | Turns (Grep and Read / nexus) | Cost $ | Correct |
|---|---:|---:|---:|
| needle: caches | 4 / 2 | 0.028 / 0.024 | 3/3 and 3/3 |
| needle: budget | 3 / 2 | 0.033 / 0.022 | 3/3 and 3/3 |
| needle: salary floor | 2 / 2 | 0.034 / 0.024 | 3/3 and 3/3 |
| concept: CV variant | 5 / 3 | 0.047 / 0.041 | 3/3 and 3/3 |
| concept: ledger lease | 3 / 2 | 0.045 / 0.024 | 3/3 and 3/3 |
| concept: secret redaction | 4 / 3 | 0.036 / 0.036 | 3/3 and 3/3 |
| architecture: gateway flow | 8 / 5 | 0.125 / 0.089 | 3/3 and 3/3 |
| architecture: discovery graph | 3 / 2 | 0.030 / 0.030 | 3/3 and 3/3 |
| architecture: mail triage | 4 / 3 | 0.087 / 0.054 | 3/3 and 3/3 |
| impact: ensure_budget | 3 / 3 | 0.022 / 0.027 | 3/3 and 3/3 |
| impact: score_job | 13 / 5 | 0.063 / 0.067 | 3/3 and 3/3 |
| impact: wrap_untrusted | 3 / 2 | 0.022 / 0.022 | 3/3 and 3/3 |
| **Sum of the medians** | **55 / 34** | **0.571 / 0.459** | **36/36 and 36/36** |

- Nexus is cheaper by 19.7% ($0.459 against $0.571), with 38% fewer turns (34 against 55) and
  25% fewer output tokens. Correct answers: 36 of 36 in both conditions.
- Output tokens for each turn: 261 with nexus and 217 for Grep and Read (sums of medians
  divided by the sum of the turns). Output follows the number of turns, so the saving comes
  from turns.
- Prompt on the first turn: 16,300 tokens with nexus against 12,700. The 2026-10-07 run had
  16,900 and 12,300. The smaller tool list took about 600 tokens out of the nexus prompt; the
  rest of the gap is the tool definitions that Claude Code sends and the skill text.
- The two dearer questions are the impact questions. `ensure_budget`: the agent called `graph`
  5 times in 3 runs. `score_job`: the agent used `find_symbol`, Grep and Read together.

Reference, the same questions on 2026-10-07 (1 run each, cold cache in the first task):
nexus $0.587, Grep and Read $0.553 with the cold-cache write taken out, 47 and 42 turns.
Before that, the three questions that cost most with nexus were `needle-caches`,
`secret-redaction` and `mail-triage`; in this run they cost $0.024, $0.036 and $0.054.

### Results (2026-10-27): django, 12 questions, 3 runs each (a public repository)

Same conditions, model and flags as the jobscout run (`--tasks benchmarks/tasks/django.yaml`),
commit `3fa8810` (after the security and code review fixes), index `chunk_format` 3 (46,052
chunks, built in 67 minutes). Pin `318a316a`. Medians of the 3 runs of a question.

| Task | Turns (Grep and Read / nexus) | Cost $ | Correct |
|---|---:|---:|---:|
| concept: signal dispatch | 3 / 3 | 0.056 / 0.052 | 3/3 and 3/3 |
| concept: field validation | 2 / 2 | 0.013 / 0.024 | 2/3 and 2/3 |
| concept: middleware chain | 3 / 3 | 0.051 / 0.053 | 3/3 and 3/3 |
| needle: CharField class | 2 / 2 | 0.013 / 0.023 | 3/3 and 3/3 |
| needle: max_length validator code | 2 / 2 | 0.012 / 0.015 | 3/3 and 3/3 (see below) |
| needle: send_robust | 3 / 4 | 0.020 / 0.039 | 3/3 and 3/3 |
| architecture: ORM layers | 7 / 4 | 0.059 / 0.078 | 3/3 and 3/3 |
| architecture: request to query | 9 / 6 | 0.063 / 0.096 | 2/3 and 3/3 |
| architecture: validators and fields | 8 / 8 | 0.070 / 0.102 | 3/3 and 3/3 |
| impact: SQLCompiler.as_sql | 24 / 21 | 0.313 / 0.247 | 3/3 and 3/3 |
| impact: URLResolver.resolve | 18 / 10 | 0.146 / 0.131 | 3/3 and 3/3 |
| impact: Field.validate | 12 / 13 | 0.077 / 0.113 | 3/3 and 3/3 |
| **Sum of the medians** | **93 / 78** | **0.894 / 0.973** | **34/36 and 35/36** |

- **On django nexus costs 8.9% more than Grep and Read** ($0.973 against $0.894), with 16% fewer
  turns (78 against 93) and 15% fewer output tokens (27,513 against 32,291). Accuracy is level:
  34 of 36 and 35 of 36 correct. **The saving of the jobscout run (-19.7%) does not hold here.**
- Nexus is cheaper on 3 questions (signal dispatch -7%, `SQLCompiler.as_sql` -21%,
  `URLResolver.resolve` -10%), about equal on 1 (middleware chain, +4%) and dearer on 8.
- The pattern in these numbers: nexus wins on the questions where Grep and Read need the most
  turns (24 and 18 turns) and loses on short lookups that take 2 or 3 turns (+$0.002 to +$0.019 per
  question). **The cause is not analysed yet.** The prompt of nexus is about 3,600 tokens larger on
  every turn, and a result such as `find_symbol` with code adds text that is written to the prompt
  cache; the split of the cost for these runs has not been done, so this is a hypothesis.
- `Field.validate` (impact): in 3 nexus runs the agent made 23 `Grep` and 9 `Read` calls besides
  `find_symbol` and `graph`: nexus did not replace the text search here.
- Django is a repository that the model knows from training, so part of what this measures is the
  model and not the tool.

**Run problems, and one change of ground truth (both after seeing the results).**
5 of the 72 runs were `API Error: Can't reach the API server (ENOTFOUND)` after about 80 minutes
each (the computer lost its network): 3 nexus runs of `orm-layers` and 2 Grep and Read runs of
`needle-charfield-class`. The runner re-ran exactly those 5; the table has no failed run. The task
`dj-needle-max-length-validator-code` asks for a string value and not for a file, but its ground
truth required the file name, so six exact answers (`"max_length"`) scored 0.5 and failed in both
conditions. The requirement is removed from `benchmarks/tasks/django.yaml` and the runs were
rescored from their stored answers (`python -m benchmarks.rescore`, no Claude call; 6 verdicts
changed, all from false to true, 3 in each condition). Three answers of the first jobscout run
were scored wrong for the same kind of reason and were rescored in the same way. Before the rescore the correct counts were 31 of 36 and 32 of 36; the cost
and turn columns do not change.
