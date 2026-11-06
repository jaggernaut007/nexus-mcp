# Benchmark run logs

Raw logs of each language benchmark run. Use them to check a result or to give evidence in
an issue. They are not published numbers: the README, the site and `llms.txt` take their
numbers only from `benchmarks/published/results.json`.

| Folder | Run |
|---|---|
| `languages-before` | Nexus-MCP 2.2.2 (`0430f01`): every tier, paid live A/B on 8 corpora |
| `languages-after` | The parser fixes (`613a650`): free tiers only |
| `languages-final` | The token cuts (`11cfccc`): free tiers, and a live A/B stopped after 5 of 8 corpora |
| `live-opt2` | Shorter tool text (`e5a9d9a`): the Nexus side of the live tier on 4 corpora |
| `languages-v2.3.0` | The 2.4.0 code before it was renumbered (`0de5088`): free tiers for Python, TypeScript and JavaScript; stopped before the live tier |
| `languages-v2.4.0` | 2.4.0: Python, TypeScript and JavaScript only |
| `setup` | One file per pinned install: version, commit and install time |

Each folder has:

- `<corpus>-live.jsonl`: one record per task, condition and rep (tokens, cost, turns, tool
  calls, the answer and its score).
- `<corpus>-quick.json`, `-retrieval.json`, `-graph.json`: the free tiers.
- `<run>.json`: one summary row per corpus.
- `console.log`: the console output of the run.

Add a run with `python scripts/archive_runs.py --log <run>=<console log>`. The script refuses
a file that holds a credential-like string.
