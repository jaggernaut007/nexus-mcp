# ADR-018: Token-Efficiency Benchmark Harness

## Status: Accepted
## Date: 2026-07-03

## Context

`docs/ROADMAP-2026.md` P2 item 9 calls for a published, ContextBench-style
token-efficiency benchmark: the 2026 consensus is that code indexes win on **token
efficiency**, not raw search quality (Turbopuffer's ContextBench: baseline Claude Code
wastes 1-in-3 file reads, grep 1-in-5, grep+semantic 1-in-8). codebase-memory-mcp went
0→24k stars in four months largely on one honest benchmark plus a one-line install. If
nexus-mcp is going to make an efficiency claim, it needs its own reproducible numbers —
and they have to be credible enough to survive scrutiny, which means publishing the
methodology and raw data, not just a headline table.

This ADR records the design decisions behind the `benchmarks/` harness. The harness
code (parsers, scorers, runner, report generator, task suites) is complete and unit
tested; the *live run* against real repos is intentionally a separate, human-gated step
(real API spend + a headless-permission CLI flag).

## Decision

### Drive the real `claude` CLI, don't simulate

Each benchmark run shells out to `claude -p --output-format stream-json` in a target
repo and parses the emitted event stream. Measuring the *actual* agent doing *actual*
tool calls is the only honest way to count wasted reads and tokens-to-answer — a
simulated harness would measure our assumptions, not Claude's behavior. The tradeoff is
real cost (~$2 smoke, ~$20–50 full run) and dependence on the CLI's stream-json shape.

### Separate pure logic from subprocess execution

`conditions.py` (argv/env builders), `transcript.py` (event-stream parser), and
`scoring.py` (metrics) are **pure functions** with zero subprocess or network calls.
Only `runner.py` spawns `claude`. This is what makes the harness unit-testable at all:
79 tests exercise the parsing/scoring/aggregation logic against checked-in fixture
event streams (`tests/fixtures/bench/`) with no live CLI calls, per the project's
"no live runs in the test suite" convention (`slow` marker for anything else).

### Parse stdout stream-json, not the on-disk transcript

`claude --output-format stream-json --verbose` emits every assistant/user/result event
on stdout. The alternative — reading the session JSONL under
`~/.claude/projects/<slug>/` — would require slug-guessing and would break under
`--no-session-persistence`. Parsing stdout is self-contained and works with persistence
off. The parser is deliberately defensive: unknown event types are ignored (not an
error), and malformed lines are counted in `parse_errors` rather than raising, because
the stream-json schema is not a versioned public contract and a single bad line must
not lose a whole run's data.

### Wasted-read ratio: count files *surfaced*, not just files *Read*

For `baseline`, "files touched" = distinct paths opened via the `Read` tool. For
`nexus`, it additionally includes every file named in a nexus tool-result payload
(`filepath`/`absolute_path`) — because a search result that surfaces a file's snippet
costs context tokens whether or not a follow-up `Read` happens. Counting only `Read`
calls would flatter nexus by hiding the context cost of its own results. `Grep`/`Glob`
are counted separately as *searches*, not reads (matching ContextBench's framing that a
grep is a query, not a file-open). Only one path is taken per result dict (filepath
preferred over absolute_path) so the same file isn't double-counted.

### Correctness: mechanical by default, LLM-judge optional

`mechanical_score = 0.5·file_recall + 0.5·fact_score`, pass at ≥ 0.75. Ground truth is
hand-authored per task against a pinned commit SHA: `must_mention_files` (the strict
file-recall target, falling back to `relevant_files` when empty so architecture-style
tasks aren't over-constrained) and `facts` (groups of acceptable phrasings, ≥1 must
appear, case-insensitive). An optional LLM-judge pass (`judge_prompt` /
`parse_judge_output`) is available but off by default — it adds a call per task and
mechanical scoring is deterministic and free.

### Deterministic skill injection over relying on skill routing

The `nexus` condition injects the SKILL.md body via `--append-system-prompt` (frontmatter
stripped) rather than trusting Claude to auto-discover the skill. This isolates "does
nexus-mcp help when used" from "does Claude reliably route to the skill" — two separate
questions. An optional `nexus-plugin` condition (`--plugin-dir plugin/`) measures the
shipped auto-routing behavior for those who want that number too. The ~500-token
SKILL.md is counted as part of nexus's input tokens — a real cost of the tool, not
hidden.

### Config isolation: `--bare` when possible, documented fallback otherwise

Every run sets `CLAUDE_CONFIG_DIR=benchmarks/.claude-bench` plus `--strict-mcp-config`
and disallows mutating tools (tasks are read-only). With `ANTHROPIC_API_KEY` present,
runs add `--bare` for full isolation (no hooks/plugins/CLAUDE.md/auto-memory). Without
an API key, runs fall back to `--setting-sources ""` — weaker, and every JSONL record
stamps which `isolation_mode` was actually used so published numbers can be judged
accordingly.

### Aggregate on medians, publish the spread

Metrics aggregate as the median across reps per (task, condition), then a macro-average
of per-task medians per condition, with IQR reported for wasted-read ratio. Model
nondeterminism is real; N≥3 reps + medians + IQR is the honest way to report it. The
report also breaks results down by category (conceptual / impact / architecture /
needle) — showing the categories where baseline *ties* is what makes the win in
impact/graph categories credible.

### No `--max-turns` — cap with budget + subprocess timeout

The installed CLI (v2.1.185) has no `--max-turns` flag. Runaway sessions are bounded by
`--max-budget-usd` plus the runner's own per-task subprocess timeout (`timeout_s`,
default 600s). Budget-capped runs surface as `result_subtype == "error_max_budget"` and
are marked in the record rather than silently dropped.

## Consequences

- **Reproducible, auditable claims.** Every published number links back to raw
  `results/runs-*.jsonl`, the exact `claude --version`, model ID, repo SHA, and
  isolation mode — all stamped per record. Anyone can re-run `setup_repos.sh` + the
  runner and check.
- **The harness ships without numbers.** The live run is human-gated (spend +
  `--permission-mode bypassPermissions`/`--dangerously-skip-permissions` for headless
  execution), so this ADR documents a *validated harness*, not results. Publishing the
  table is a follow-up once a run is authorized.
- **Fragile to CLI stream-json changes.** A CLI update could rename event fields;
  mitigated by the defensive parser, fixture-based tests catching breakage, and keeping
  raw stdout for recompute. Not eliminated.
- **Contamination is partially unaddressed.** django is likely partly memorized by the
  model; home-assistant/core is pinned post-cutoff to compensate, and wasted-read ratio
  is contamination-resistant by construction (it measures reads, not recalled
  knowledge). Needle tasks asking for exact line numbers resist memorization. Documented
  honestly in `benchmarks/README.md` rather than papered over.

## Alternatives Considered

- **Simulated / replay harness** (feed canned tool results to a scorer) — rejected: it
  measures our assumptions about agent behavior, not the agent. The whole point is to
  observe what Claude actually reads.
- **Extend chunks-table schema to log runtime, then measure** — out of scope; this is a
  benchmark, not a feature.
- **Read the on-disk session transcript** instead of stdout stream-json — rejected:
  brittle slug resolution, breaks under `--no-session-persistence`.
- **Count only `Read` calls for wasted-read ratio** — rejected as dishonest: it hides
  the context-token cost of nexus's own surfaced results and would inflate nexus's
  numbers.
- **LLM-judge as the default correctness signal** — rejected as default: nondeterministic
  and costs a call per task. Kept as an opt-in cross-check.
