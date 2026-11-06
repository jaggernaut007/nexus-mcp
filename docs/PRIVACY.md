# Privacy and data

Nexus-MCP processes your code on your machine. The server makes no network calls. It has no
telemetry and needs no API key.

## What crosses the network

Only the flows below cross the network. Each one is named in the CLI output before it starts.

| Flow | Direction | When | What | How to stop it |
|---|---|---|---|---|
| Package install | in | `pip install` or `uvx`, once | the wheel from PyPI | install from a local wheel |
| Embedding model | in | the first index, once | `BAAI/bge-small-en-v1.5` from Hugging Face (~130 MB) | set `NEXUS_OFFLINE=1` after the first download, or copy the model cache |
| Repo URL source | in | `eval <url>` only | `git clone --depth 1` of that public repo | use a local path |
| Agent tier | **out** | `eval --agent` only, after you answer yes | the prompts and the code that the agent reads, sent through **your own** `claude` or `codex` CLI to Anthropic or OpenAI | do not use `--agent`; the quick tier sends nothing |
| Share | **out** | `eval share`, after you answer yes to the exact payload | allow-listed numbers only: no paths, no code, no question text | do not run `share` |

Defaults that protect you:

- `HF_HUB_DISABLE_TELEMETRY=1` is set unless you set it yourself.
- `NEXUS_OFFLINE=1` also sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. The model load then
  fails clearly if the model is not cached.
- A test (`tests/test_evaluate_quick.py::test_quick_tier_makes_no_network_connection`) blocks
  every socket connection and runs the quick tier. It must pass.

## What is stored, and where

| Data | Default location | Holds your code? | Change it with |
|---|---|---|---|
| Project index: code chunks, vectors, BM25 (LanceDB `chunks`) | `<project>/.nexus/lancedb/` | **yes**, code snippets | `NEXUS_STORAGE_DIR` |
| Memories (LanceDB `memories`) | `<project>/.nexus/lancedb/` | only what the agent stores | `NEXUS_STORAGE_DIR` |
| Code graph (SQLite) | `<project>/.nexus/graph.db` | symbol names and paths | `NEXUS_STORAGE_DIR` |
| Embedding model | `~/.cache/huggingface/hub/` | no | `HF_HOME` |
| Audit log | stderr (your MCP client log); a file only if you set one | tool arguments, with secrets redacted | `NEXUS_AUDIT_LOG_FILE`, `NEXUS_AUDIT_ENABLED` |
| Eval results | `~/.nexus-mcp/evals/` | no: numbers only; question text stays local | `NEXUS_EVAL_HOME` |
| Eval index | the OS temp folder, deleted at exit | yes, while the eval runs | `--keep-index` writes it to `<project>/.nexus/` |
| Eval clones | `~/.cache/nexus-mcp/eval/repos/` | the public repo | `NEXUS_EVAL_CACHE` |
| `uvx` package cache | the `uv` cache folder | no | `uv cache clean nexus-mcp-ci` |

The index holds code. Add `.nexus/` to `.gitignore` (or to `.git/info/exclude`, which is never
committed). `nexus-mcp-ci data where` warns when `.nexus/` is not ignored.

## See and remove your data

```bash
nexus-mcp-ci data where                     # every location, with its size
nexus-mcp-ci data export-memories           # write memories to nexus-memories.json first
nexus-mcp-ci data clear --memories          # dry run: lists what it would delete
nexus-mcp-ci data clear --memories --yes    # delete only the memories table
nexus-mcp-ci data clear --all --yes         # index, memories, graph, eval results, clones, model
```

- `clear` is a dry run unless you add `--yes`.
- `clear` refuses a folder that does not look like a Nexus-MCP store.
- Stop your MCP client first: `clear` cannot see a server that runs in another process.
- Targets: `--index`, `--memories`, `--graph`, `--evals`, `--clones`, `--models`, `--all`.

Shell commands that do the same, if you prefer not to run our code to remove our data:

```bash
rm -rf .nexus
rm -rf ~/.nexus-mcp ~/.cache/nexus-mcp
rm -rf ~/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5
uv cache clean nexus-mcp-ci
```

## Uninstall

1. Remove the server from your client: `nexus-mcp-ci setup --uninstall --yes`,
   `claude mcp remove nexus-mcp`, or `/plugin uninstall nexus-mcp@nexus-mcp`.2. Remove the package: `pip uninstall nexus-mcp-ci`.
3. Remove the data: `nexus-mcp-ci data clear --all --yes` before step 2, or the shell commands
   above after it.
