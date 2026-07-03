# Token-efficiency benchmark

Implements ROADMAP-2026 P2 item 9: a ContextBench-style benchmark measuring
**wasted-read ratio** and **tokens-to-answer** for nexus-mcp vs. baseline
Claude Code (built-in `Read`/`Grep`/`Glob` only) on 1,000+ file repositories.

This is dev tooling, not part of the shipped `nexus-mcp-ci` package. Install
its one extra dependency with `pip install -e ".[bench]"`.

## Why this exists

The 2026 competitive landscape settled on: agentic grep is the default
baseline, but indexes win on **token efficiency**, not raw search quality —
Turbopuffer's ContextBench found baseline Claude Code wastes 1-in-3 file
reads, grep 1-in-5, grep+semantic 1-in-8. This harness measures where
nexus-mcp actually lands on that scale, honestly, with raw data published
alongside the summary.

## Quick start

```bash
pip install -e ".[bench]"
bash benchmarks/setup_repos.sh          # clone + pin repos, pre-build nexus index (one-time, minutes)
python -m benchmarks.runner --tasks benchmarks/tasks/django.yaml --smoke   # 2 tasks x 1 rep, <$2
python -m benchmarks.report benchmarks/results/runs-*.jsonl
```

Default model is `claude-sonnet-5`; override with `--model <id>` on `runner.py`.

Full run (all tasks, both conditions, 3 reps — expect **$20-50** in API spend
and tens of minutes of wall time):

```bash
python -m benchmarks.runner --tasks benchmarks/tasks/django.yaml
python -m benchmarks.runner --tasks benchmarks/tasks/home-assistant.yaml
python -m benchmarks.report benchmarks/results/runs-*.jsonl --out benchmarks/results/report.md --csv benchmarks/results/report.csv
```

## Methodology

**Conditions:**
- `baseline` — `claude -p --tools Read,Grep,Glob` (no MCP servers, no skill).
- `nexus` — same built-in tools, plus the `nexus-mcp` MCP server
  (`benchmarks/mcp-configs/nexus.json`) and the shipped
  `plugin/skills/nexus-mcp/SKILL.md` body injected via `--append-system-prompt`.
  Skill injection is deterministic (always present) rather than relying on
  Claude's own skill-routing — this isolates "does nexus-mcp help when used"
  from "does Claude reliably discover the skill," which is a separate
  question. An optional `nexus-plugin` condition (`--plugin-dir plugin/`)
  measures the shipped routing behavior instead, if you want that number too.

**Isolation:** each run gets `CLAUDE_CONFIG_DIR` pointed at
`benchmarks/.claude-bench` (gitignored) plus `--strict-mcp-config` and
`--disallowedTools Edit,Write,NotebookEdit,WebFetch,WebSearch,Task` (tasks are
read-only by design). With `ANTHROPIC_API_KEY` set, runs additionally use
`--bare` for full isolation (no hooks/plugins/CLAUDE.md/auto-memory). Without
an API key, runs fall back to `--setting-sources ""` — weaker isolation, and
every run record's `isolation_mode` field says which mode was actually used.
Judge your published numbers' credibility accordingly.

**Tasks:** `benchmarks/tasks/*.yaml`, hand-authored against a pinned commit
SHA in each repo (`git show <sha>:<path>` to verify any fact yourself). Four
categories: conceptual search, callers/impact analysis, architecture
overview, needle-in-haystack symbol lookup. Each task specifies
`relevant_files` (the ground-truth minimal file set), `acceptable_extra_files`
(reasonable neighborhood reads that don't count as waste),
`must_mention_files` (the subset an answer must cite by name — used for
file-recall scoring, falls back to `relevant_files` when empty), and `facts`
(groups of acceptable phrasings, at least one of which must appear).

Full task-suite YAML schema:

```yaml
repo:
  name: django          # local clone dir under benchmarks/repos/<name>
  url: https://...       # git clone URL
  pin: <commit-sha>      # pinned SHA, checked out by setup_repos.sh
  pinned_date: "2026-..." # for the contamination check (see below)
  why: "..."              # why this repo, free text

defaults:                 # optional; merged under each task, task keys win
  max_budget_usd: 1.00
  timeout_s: 600

tasks:
  - id: some-unique-id
    category: conceptual   # conceptual | impact | architecture | needle
    prompt: "The question asked verbatim (a runner-added suffix forbids edits)."
    max_budget_usd: 1.00   # optional override of defaults
    timeout_s: 600         # optional override of defaults
    ground_truth:
      relevant_files: ["path/to/file.py"]
      acceptable_extra_files: []
      must_mention_files: ["path/to/file.py"]   # optional, see above
      facts:
        - any_of: ["phrase one", "phrase two"]  # at least one must appear
        - any_of: ["another required phrase"]
```

`facts` is a list of groups; each group's `any_of` is a list of acceptable
phrasings (case-insensitive substring match) — the group is satisfied if
**any one** of them appears in the final answer, and `fact_score` is the
fraction of groups satisfied.

**Metrics** (see `benchmarks/scoring.py` and `benchmarks/transcript.py` for
exact formulas):
- **Wasted-read ratio** = fraction of files surfaced to the model that are
  outside the ground-truth relevant set. For `baseline`, "surfaced" = files
  opened via `Read`. For `nexus`, it additionally includes every file named
  in a nexus tool's result payload (`filepath`/`absolute_path`) — a result
  that surfaces a file's content costs context tokens whether or not a
  follow-up `Read` happens. `Grep`/`Glob` calls are counted separately as
  searches, not reads.
- **Tokens-to-answer** = total usage (input + cache_creation + cache_read +
  output). Also reported: **fresh tokens** (excludes cache reads — a fairer
  cross-run comparison since cache hit rates vary) and **cost/task** in USD.
  The ~500-token nexus SKILL.md body is counted as part of nexus's input —
  it's a real cost of using the tool, not hidden.
- **Retrieval tokens** (`retrieval_tokens_est`, recorded per-run but not in
  the headline table) = an estimated token count for all tool-result
  payloads seen (`len(text) // 4`), diagnostic-only — it isolates how much of
  a run's tokens went to retrieval versus everything else (reasoning,
  conversation scaffolding).
- **Correctness** = `0.5 * file_recall + 0.5 * fact_score`, pass at ≥ 0.75.
  Mechanical only (substring/regex matching against the final answer) unless
  a judge pass is run — see `scoring.judge_prompt`/`parse_judge_output` for
  an optional LLM-judge rubric (not run by default; costs one extra call per
  task).
- Aggregation is the **median across reps** per (task, condition), then a
  macro-average of per-task medians per condition, with IQR reported for
  wasted-read ratio.

**Contamination:** both target repos are large enough (django: ~2,900 Python
files; home-assistant/core: ~26,000) that no single conversation could read
the whole thing, but the model may have memorized parts of django from
training. Mitigations: home-assistant/core is pinned to a commit dated at
model training-cutoff or later (check the `pinned_date` field in its task
YAML against the **default model's** — `claude-sonnet-5`, or whatever
`--model` you actually ran with — training cutoff before publishing);
wasted-read ratio is contamination-resistant by construction since it
measures *reads*, not recalled knowledge; needle tasks ask for exact line
numbers, which models don't reliably memorize even for famous code.

## File layout

- `tasks/*.yaml` — task suites (schema above)
- `mcp-configs/nexus.json` — MCP server config for the `nexus` condition;
  its `env` is intentionally empty so the server inherits nexus-mcp's default
  embedding model. Assumes the target repo was already pre-indexed by
  `setup_repos.sh` — pointing this at an un-indexed repo means the first
  nexus tool call has to index from scratch inside the measured run.
- `conditions.py` — pure argv/env builders (no subprocess calls)
- `transcript.py` — pure stream-json event parser -> `RunTrace`
- `scoring.py` — pure per-run metrics against ground truth
- `runner.py` — orchestrates real `claude` subprocess runs, writes JSONL
- `report.py` — aggregates JSONL into markdown + CSV
- `setup_repos.sh` / `_preindex_one.py` — one-time repo clone + pre-index
- `repos/`, `results/`, `.claude-bench/` — gitignored, generated locally

## Known gotchas

- The CLI's stream-json event schema is not a versioned public contract —
  `transcript.py` is defensive (unknown event types are ignored, malformed
  lines are counted in `parse_errors` rather than raising) but a CLI update
  could still shift field names. Raw stdout is not persisted by default;
  keep it if you need to debug a parsing gap on a specific run.
- No `--max-turns` flag exists in this CLI version — runaway sessions are
  bounded by `--max-budget-usd` plus the runner's own subprocess timeout
  (`timeout_s` per task, default 600s).
- Every published number should link back to the raw `results/runs-*.jsonl`
  it came from, plus the exact `claude --version` and model ID used for that
  run — both are recorded in every JSONL record.
