# Nexus-MCP Installation Guide

[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/card.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)
[![jaggernaut007/Nexus-MCP MCP server](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP/badges/score.svg)](https://glama.ai/mcp/servers/jaggernaut007/Nexus-MCP)

## Prerequisites

- **Python 3.10, 3.11, or 3.12** (Python 3.13+ is not yet supported by tree-sitter-languages)
- **pip** (comes with Python)

Check your Python version:

```bash
python3 --version
```

If you have multiple Python versions, ensure you use 3.10 or later.

---

## Install from PyPI (recommended)

The simplest way to install Nexus-MCP:

```bash
pip install nexus-mcp-ci
```

With optional extras:

```bash
# With GPU (CUDA) support
pip install nexus-mcp-ci[gpu]

# With FlashRank reranker for better search quality
pip install nexus-mcp-ci[reranker]

# Both GPU and reranker
pip install nexus-mcp-ci[gpu,reranker]
```

After installing, the `nexus-mcp` command is available globally:

```bash
nexus-mcp
```

> **Virtual environment recommended:** While `pip install nexus-mcp-ci` works globally, using a virtual environment avoids dependency conflicts:
> ```bash
> python3 -m venv ~/.nexus-mcp-venv
> source ~/.nexus-mcp-venv/bin/activate
> pip install nexus-mcp-ci
> ```

---

## Install from Source (for development)

### Quick Start (Setup Script)

```bash
# Clone the repository
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP

# Run setup script (creates venv, installs deps, verifies)
./setup.sh
```

**Setup script options:**

| Flag | Description |
|------|-------------|
| `--clean` | Remove existing venv before creating new |
| `--prod` | Install production dependencies only (no dev) |
| `--reranker` | Include optional FlashRank reranker |
| `--no-verify` | Skip verification step |
| `--help` | Show help message |

Examples:

```bash
./setup.sh                       # Dev install (pytest, ruff, mypy)
./setup.sh --clean               # Remove old venv, fresh install
./setup.sh --prod                # Production-only (no dev tools)
./setup.sh --reranker            # Dev install + FlashRank reranker
./setup.sh --clean --prod        # Clean production install
```

After setup, activate the environment:

```bash
source .venv/bin/activate
```

### Manual Install from Source

```bash
git clone https://github.com/jaggernaut007/Nexus-MCP.git
cd Nexus-MCP

# Option 1: Production only
pip install -e .

# Option 2: With dev dependencies
pip install -e ".[dev]"

# Option 3: With dev + reranker
pip install -e ".[dev,reranker]"

# Option 4: With GPU (CUDA) support
pip install -e ".[gpu]"
```

---

## Verify Installation

```bash
# Check the module imports correctly
python3 -c "import nexus_mcp; print('OK')"

# Check the CLI is available
nexus-mcp --help

# Run the self-test demo (exercises all 15 tools)
python self_test/demo_mcp.py
```

---

## AI Tool Integrations

### Claude Code (CLI)

**If installed via pip (recommended):**

```bash
# If nexus-mcp is on your PATH (pip install nexus-mcp-ci)
claude mcp add nexus-mcp -- nexus-mcp-ci

# With environment variables
claude mcp add nexus-mcp -e NEXUS_EMBEDDING_MODEL=bge-small-en -- nexus-mcp-ci
```

**If installed in a virtual environment:**

```bash
# Use the full venv path so the MCP client finds the right Python
claude mcp add nexus-mcp -- /path/to/Nexus-MCP/.venv/bin/nexus-mcp

# If updating an existing registration, remove first
claude mcp remove nexus-mcp
claude mcp add nexus-mcp -- /path/to/Nexus-MCP/.venv/bin/nexus-mcp
```

> **Why use the full venv path?** MCP clients spawn the server as a subprocess. If you just use `nexus-mcp`, it resolves to whatever Python is on your system PATH — which may not have the required dependencies installed. Using the venv path ensures the server runs with the correct, isolated environment.

**Manual config** — add to your settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "command": "nexus-mcp-ci"
    }
  }
}
```

**After setup**, reload your VS Code window (Cmd+Shift+P → "Reload Window") or restart Claude Code for the MCP server to start.

**Usage in Claude Code:**
```
> index my codebase at ./my-project
> search for authentication logic
> find_symbol User
> explain Config
```

---

### Claude Desktop

Add to your Claude Desktop configuration:

| Platform | Config File Location |
|----------|---------------------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "command": "nexus-mcp-ci",
      "args": []
    }
  }
}
```

Restart Claude Desktop after saving.

---

### Cursor

Cursor supports MCP servers through its extension system:

1. **Open Settings** → Extensions → MCP
2. **Add Server Configuration**:

```json
{
  "nexus-mcp": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

Or add to `.cursor/mcp.json` in your project:

```json
{
  "servers": {
    "nexus-mcp": {
      "command": "nexus-mcp-ci"
    }
  }
}
```

---

### Windsurf (Codeium)

Windsurf supports MCP through Cascade:

1. Open **Cascade Settings**
2. Navigate to **MCP Servers**
3. Add configuration:

```json
{
  "nexus-mcp": {
    "command": "nexus-mcp-ci",
    "transport": "stdio"
  }
}
```

---

### Cline (VS Code)

Add to Cline's MCP settings in VS Code:

1. Open Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
2. Search "Cline: Open MCP Settings"
3. Add:

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "command": "nexus-mcp-ci"
    }
  }
}
```

---

### Zed Editor

Zed supports MCP through its assistant panel. Add to settings:

```json
{
  "assistant": {
    "mcp_servers": {
      "nexus-mcp": {
        "command": "nexus-mcp-ci"
      }
    }
  }
}
```

---

### Continue (VS Code / JetBrains)

Add to your Continue configuration (`~/.continue/config.json`):

```json
{
  "mcpServers": [
    {
      "name": "nexus-mcp",
      "command": "nexus-mcp-ci"
    }
  ]
}
```

---

### Generic MCP Client

For any MCP-compatible client, use stdio transport:

```bash
# Command to run
nexus-mcp

# Transport
stdio (stdin/stdout)

# Protocol
Model Context Protocol (MCP)
```

---

## Configuration

All settings use the `NEXUS_` environment variable prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_STORAGE_DIR` | `.nexus` | Storage directory for indexes and graph DB |
| `NEXUS_EMBEDDING_MODEL` | `jina-code` | Embedding model: `jina-code`, `bge-small-en` |
| `NEXUS_EMBEDDING_DEVICE` | `auto` | Device: `auto` (CUDA > MPS > CPU), `cuda`, `mps`, `cpu` |
| `NEXUS_MAX_FILE_SIZE_MB` | `10` | Skip files larger than this |
| `NEXUS_CHUNK_MAX_CHARS` | `4000` | Max characters per code chunk |
| `NEXUS_MAX_MEMORY_MB` | `350` | Memory budget in MB |
| `NEXUS_SEARCH_MODE` | `hybrid` | Search mode: `hybrid`, `vector`, or `bm25` |
| `NEXUS_LOG_LEVEL` | `INFO` | Logging level |
| `NEXUS_LOG_FORMAT` | `text` | Log format: `text` or `json` |
| `NEXUS_PERMISSION_LEVEL` | `full` | Permission level: `full` or `read` |
| `NEXUS_AUDIT_ENABLED` | `true` | Enable audit logging |
| `NEXUS_RATE_LIMIT_ENABLED` | `false` | Enable per-tool rate limiting |
| `NEXUS_TRUST_REMOTE_CODE` | `false` | Allow trust_remote_code in models |

Example:

```bash
NEXUS_LOG_LEVEL=DEBUG NEXUS_SEARCH_MODE=vector nexus-mcp
```

---

## Running Tests

```bash
# All tests (441)
pytest -v

# Skip slow performance benchmarks
pytest -m "not slow"

# Lint
ruff check .
```

---

## Troubleshooting

### ONNX Runtime / Optimum errors during indexing

If you see `Using the ONNX backend requires installing Optimum and ONNX Runtime`, install the required packages:

```bash
pip install "sentence-transformers[onnx]" "optimum[onnxruntime]>=1.19.0,<2.0"
```

**Version compatibility:** Ensure `optimum` and `transformers` versions are compatible. If you see `cannot import name 'FLAX_WEIGHTS_NAME'`, pin compatible versions:

```bash
pip install "optimum[onnxruntime]>=1.19.0,<2.0" "transformers>=4.46,<5.0"
```

**MCP server not picking up new packages:** If you installed packages but the MCP server still errors, the server process needs a restart. Reload your VS Code window, restart Claude Code, or restart Claude Desktop.

**Alternative: use a model that doesn't need ONNX:**

```bash
NEXUS_EMBEDDING_MODEL=bge-small-en nexus-mcp
```

### `ModuleNotFoundError: No module named 'nexus_mcp'`

Ensure you installed with `pip install -e .` from the project root and are using the correct Python version (3.10+). If using a venv, make sure it's activated: `source .venv/bin/activate`.

### `tree-sitter` FutureWarning

The warning `Language(path, name) is deprecated` is harmless and comes from the tree-sitter-languages compatibility layer. It does not affect functionality.

### High memory usage during indexing

The embedding model is loaded during indexing and unloaded after. Peak RSS may exceed the 350MB target briefly. Set `NEXUS_MAX_MEMORY_MB` to adjust the budget.

### `pip` resolves dependency conflicts

If you see dependency conflict warnings from other installed packages, these are unrelated to Nexus-MCP and can be safely ignored as long as `import nexus_mcp` succeeds.

### Demo fails at indexing step

Ensure `tree-sitter==0.21.3` and `tree-sitter-languages>=1.10.0` are installed. These are pinned for compatibility.

### Server not found after install

If `nexus-mcp` command is not found, ensure the install location is on your PATH. With a venv, activate it first. Without a venv, you may need `python3 -m nexus_mcp.server` as a fallback.

### MCP client can't connect

- Ensure `nexus-mcp` is on the PATH that the MCP client uses
- If installed in a venv, use the full path: `/path/to/Nexus-MCP/.venv/bin/nexus-mcp`
- Check the client's logs for connection errors
