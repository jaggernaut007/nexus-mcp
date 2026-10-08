# Nexus-MCP Self-Test Demo

Verifies the Nexus-MCP installation by exercising **all 10 MCP tools** end-to-end, bypassing the MCP protocol layer to call tool functions directly.

## Quick Start

```bash
# From the project root (with nexus-mcp-ci installed)
python self_test/demo_mcp.py

# Or point it at an existing project
python self_test/demo_mcp.py /path/to/your/codebase
```

## What It Tests

| # | Tool | Description |
|---|------|-------------|
| 1 | `health`, `status` | Liveness probe and server status before indexing |
| 2 | `index` | Full codebase indexing (vector + graph + BM25) |
| 3 | `status` | Index stats after indexing |
| 4 | `search` | Hybrid search (vector + BM25 + graph fusion) |
| 5 | `search` | Vector-only and BM25-only modes |
| 6 | `find_symbol`, `graph` | Symbol lookup (exact + fuzzy); `graph` callers and callees |
| 7 | `analyze` | Complexity, dependencies, smells, quality |
| 8 | `map` | Architecture view (`detail="architecture"`) |
| 9 | `graph` | Transitive change-impact analysis (`transitive=True`) |
| 10 | `explain` | Combined symbol explanation (summary + detailed) |
| 11 | `memory` | `store`, `search` and `delete` actions |
| 12 | `index` | Incremental re-index after a file change |
| 13 | `health` | Final health check |

The demo checks that each call returns without an error. It also checks that `graph` finds known callers in the sample project, so an empty call graph fails the demo.

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
  Results: 27/27 checks passed
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
