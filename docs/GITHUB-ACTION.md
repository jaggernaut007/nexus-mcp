# GitHub Action

The repository root has an `action.yml`. It runs `nexus-mcp-ci` in a workflow. The default
runs the free, offline `eval` on the checked-out repository and writes the result to the job
summary.

```yaml
name: nexus-eval
on: [pull_request]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jaggernaut007/Nexus-MCP@v2
        with:
          args: eval . --questions 30
```

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `args` | `eval .` | Arguments for `nexus-mcp-ci`. Any subcommand works. |
| `version` | latest | Version of `nexus-mcp-ci` to run, for example `2.3.0`. |
| `python-version` | `3.12` | Python for `uvx`. Supported: 3.10, 3.11, 3.12. |

## Notes

- The action installs `uv` with `astral-sh/setup-uv`, then runs `uvx`. The first run downloads
  the packages and the embedding model (about 130 MB). Cache `~/.cache/huggingface` to avoid
  the model download on later runs.
- The inputs reach the shell as environment variables, not as text in the script.
