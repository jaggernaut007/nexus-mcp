# Nexus-MCP Self-Test Demo

Verifies the Nexus-MCP installation by exercising **all 15 MCP tools** end-to-end, bypassing the MCP protocol layer to call tool functions directly.

## Quick Start

```bash
# From the project root (with nexus-mcp installed)
python self_test/demo_mcp.py

# Or point it at an existing project
python self_test/demo_mcp.py /path/to/your/codebase
```

## What It Tests

| # | Tool | Description |
|---|------|-------------|
| 1 | `health` | Readiness / liveness probe |
| 2 | `status` | Server status & memory metrics |
| 3 | `index` | Full codebase indexing (vector + graph + BM25) |
| 4 | `search` | Hybrid search (vector + BM25 + graph fusion) |
| 5 | `search` | Vector-only and BM25-only modes |
| 6 | `find_symbol` | Symbol lookup (exact + fuzzy) |
| 7 | `find_callers` | Direct caller analysis |
| 8 | `find_callees` | Direct callee analysis |
| 9 | `analyze` | Complexity, dependencies, smells, quality |
| 10 | `impact` | Transitive change-impact analysis |
| 11 | `explain` | Combined symbol explanation (summary + detailed) |
| 12 | `remember` | Store semantic memories |
| 13 | `recall` | Search memories by similarity |
| 14 | `forget` | Delete memories by tag / type |
| 15 | `index` | Incremental re-index after file change |

## Sample Project

When no path is supplied, a temp project is created with three files:

- **main.py** — entry point with `greet()`, `run_calculations()`, `create_user()`
- **utils.py** — helpers: `calculate_sum()`, `calculate_product()`, `format_currency()`, `clamp()`
- **models.py** — dataclasses: `User`, `Config`

After the initial index, `new_feature()` is appended to `main.py` and an incremental re-index is triggered.

## Expected Output

The demo prints a pass/fail summary at the end:

```
  ✓ health()
  ✓ status()
  ✓ index(path)
  ✓ search("calculate sum of two numbers")
  ...
  ══════════════════════════════════════════
  Results: 26/26 checks passed
```

Install `rich` for colorized, table-formatted output:

```bash
pip install rich
```

## How It Works

The script imports `create_server()` from `nexus_mcp.server`, which returns a `FastMCP` instance. Each registered tool's underlying function is accessed via `tool.fn`, allowing direct invocation without starting the MCP transport.

```python
from nexus_mcp.server import create_server

mcp = create_server()
tools = {
    comp.name: comp.fn
    for key, comp in mcp._local_provider._components.items()
    if key.startswith("tool:")
}

# Call any tool directly
result = tools["search"]("find authentication logic", 10)
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: nexus_mcp` | Run `pip install -e ".[dev]"` from the project root |
| `ImportError: rich` | `pip install rich` (optional — runs without it) |
| Memory errors during indexing | Set `NEXUS_MAX_FILE_SIZE_MB=5` to skip large files |
| Slow on large codebases | Use `python self_test/demo_mcp.py` without args to test with the small sample project |
