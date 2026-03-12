# Nexus-MCP Installation Guide

## Prerequisites

- **Python 3.10+** (tested on 3.10, 3.11, 3.12)
- **pip** (comes with Python)
- **git** (to clone the repository)

Check your Python version:

```bash
python3 --version
```

If you have multiple Python versions, ensure you use 3.10 or later.

---

## Quick Start (Setup Script)

The recommended way to install Nexus-MCP:

```bash
# Clone the repository
git clone https://github.com/shreyasjagannath/Nexus-MCP.git
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

---

## Manual Install

If you prefer not to use the setup script:

```bash
git clone https://github.com/shreyasjagannath/Nexus-MCP.git
cd Nexus-MCP

# Option 1: Production only
pip install -e .

# Option 2: With dev dependencies
pip install -e ".[dev]"

# Option 3: With dev + reranker
pip install -e ".[dev,reranker]"
```

---

## Verify Installation

```bash
# Check the module imports correctly
python3 -c "import nexus_mcp; print('OK')"

# Check the CLI is available
nexus-mcp --help

# Run the self-test demo (exercises all 13 tools, 26 checks)
python self_test/demo_mcp.py
```

---

## AI Tool Integrations

### Claude Code (CLI)

The easiest way to add Nexus-MCP to Claude Code:

```bash
claude mcp add nexus-mcp -- nexus-mcp
```

Or manually add to your settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "command": "nexus-mcp"
    }
  }
}
```

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
      "command": "nexus-mcp",
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
    "command": "nexus-mcp",
    "transport": "stdio"
  }
}
```

Or add to `.cursor/mcp.json` in your project:

```json
{
  "servers": {
    "nexus-mcp": {
      "command": "nexus-mcp"
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
    "command": "nexus-mcp",
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
      "command": "nexus-mcp"
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
        "command": "nexus-mcp"
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
      "command": "nexus-mcp"
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
| `NEXUS_EMBEDDING_MODEL` | `bge-small-en` | Embedding model name |
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
