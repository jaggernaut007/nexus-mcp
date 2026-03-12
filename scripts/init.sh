#!/usr/bin/env bash
set -euo pipefail

echo "=== Nexus-MCP Init Check ==="

# Check Python version
python_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $python_version"
if [[ "$(echo "$python_version < 3.10" | bc -l)" -eq 1 ]]; then
    echo "ERROR: Python >= 3.10 required"
    exit 1
fi

# Check package installs
echo ""
echo "--- Import Check ---"
python3 -c "import nexus_mcp; print('nexus_mcp OK')"
python3 -c "from nexus_mcp.core.models import Symbol, ParsedFile; print('core.models OK')"
python3 -c "from nexus_mcp.core.interfaces import IParser, IEngine; print('core.interfaces OK')"
python3 -c "from nexus_mcp.core.exceptions import NexusException; print('core.exceptions OK')"
python3 -c "from nexus_mcp.core.graph_models import UniversalNode, NodeType; print('core.graph_models OK')"
python3 -c "from nexus_mcp.config import Settings; print('config OK')"

# Ruff
echo ""
echo "--- Ruff Check ---"
ruff check src/ tests/ || { echo "Ruff failed"; exit 1; }
echo "Ruff: clean"

# Pytest
echo ""
echo "--- Pytest ---"
pytest tests/ -v --tb=short || { echo "Tests failed"; exit 1; }

echo ""
echo "=== All checks passed ==="
