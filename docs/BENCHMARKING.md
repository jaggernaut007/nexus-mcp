# Benchmarking by language

This page tells you how to run the per-language benchmark again for a new release.
One command runs every tier. Published numbers come from
`benchmarks/published/results.json` (see [ADR-021](adr/ADR-021-results-publishing-and-sharing.md)).

## Languages and corpora

`benchmarks/corpora.yaml` pins one open-source project for each language. Each pin is a
commit. Each checkout is shallow and sparse.

| Language | Project | Folder |
|---|---|---|
| Python | pallets/flask, django/django | whole repo |
| TypeScript | vitejs/vite | `packages/vite/src` |
| JavaScript | webpack/webpack | `lib` |
| Java | apache/commons-lang | `src/main` |
| C# | JamesNK/Newtonsoft.Json | `Src/Newtonsoft.Json` |
| PHP | laravel/framework | `src/Illuminate` |
| C++ | Chromium | `base` |
| C | redis/redis | `src` |
| Go | prometheus/prometheus | `tsdb`, `promql` |
| Rust | tokio-rs/tokio | `tokio/src` |

The languages follow GitHub Octoverse 2025 (TypeScript, Python and JavaScript lead by
contributors) and the languages of large open-source infrastructure (Go, C, C++, Java).

## Tiers

| Tier | What it measures | Cost |
|---|---|---|
| `quick` | `nexus-mcp-ci eval`: Grep against Nexus on generated questions | free |
| `retrieval` | hit@1 on 15 hand-checked queries (`evals/retrieval/queries.languages.yaml`) | free |
| `graph` | symbols, graph nodes and CALLS edges per language (`evals/graph_coverage.py`) | free |
| `live` | `claude -p` with Read, Grep and Glob, against the same plus Nexus-MCP; 6 tasks | paid |

The live tasks are in `benchmarks/tasks/<corpus>.yaml`. Each language has 2 conceptual,
2 impact, 1 architecture and 1 needle task. `python -m benchmarks.validate_tasks` checks
that every file and fact of a task exists in the pinned checkout.

## Run it for a release

```bash
python -m benchmarks.corpora setup
```

```bash
benchmarks/install_nexus.sh <git-ref> <label>
```

```bash
python -m benchmarks.index_corpora --nexus-venv benchmarks/.venv-<label>
```

```bash
python -m benchmarks.run_languages --nexus-venv benchmarks/.venv-<label> --label <label>
```

The last command writes `benchmarks/results/languages-<label>.json`. A finished tier is
not run again. If the usage limit stops a live run, the command exits with code 3. Run
the same command later to resume.

Use `--languages Rust,Go` and `--tiers quick,retrieval,graph` to run a part. The three
free tiers never start a paid run.

## Rules of a fair comparison

The runner enforces these rules and stores the values on each result row.

- Both conditions use the same model (`sonnet`) and the same `--effort` level (`medium`).
  The effort does not come from the user settings of the person who runs the benchmark.
- Both conditions get the same prompt, the same budget cap and the same built-in tools
  (`Read`, `Grep`, `Glob`). Nexus adds only its MCP tools and the skill text.
- Both conditions run isolated from user hooks, skills, memory and settings. The row
  records the isolation mode. Claude config files of a corpus are deleted at checkout.
- Each run is a new session. The runs alternate in the order A B, B A for each task and
  rep, so time drift and prompt-cache warmth fall on both sides.
- The index is built before the timed runs, with the pinned install, in
  `benchmarks/indexes/`, outside the corpus. Index time is reported on its own.
- `install_nexus.sh` installs one commit in its own venv. The row records the version and
  the commit. Uncommitted edits never reach a measured run.

## The north-star gate

A change is kept only when it lowers tokens and does not lower accuracy. Run the
`before` and `after` labels with the same settings. Record both numbers in `CHANGELOG.md`.
Do not tune a corpus or a task until Nexus-MCP wins. Report a weak result as it is.
