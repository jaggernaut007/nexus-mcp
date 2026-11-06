#!/usr/bin/env bash
# Install one Nexus-MCP version into its own venv, so a benchmark measures that version.
#
# Usage: benchmarks/install_nexus.sh <git-ref> <label>
#   benchmarks/install_nexus.sh 0430f01 v2.2.2     # the 2.2.2 release
#   benchmarks/install_nexus.sh HEAD after          # this checkout's last commit
#
# The venv is benchmarks/.venv-<label>. It holds the package built from the commit, not
# this checkout's src/, so uncommitted edits never reach a measured run. The resolved
# commit goes to <venv>/NEXUS_REF; runner.py --nexus-venv records it on each run.
# Idempotent: a venv that already holds the same commit is kept.
#
# Set NEXUS_BENCH_PYTHON to choose the interpreter (default 3.12).
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <git-ref> <label>" >&2
  exit 2
fi
ref="$1"
label="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
venv="$SCRIPT_DIR/.venv-$label"
sha="$(git -C "$PROJECT_ROOT" rev-parse --verify "$ref^{commit}")"

if [ -f "$venv/NEXUS_REF" ] && [ "$(cat "$venv/NEXUS_REF")" = "$sha" ]; then
  echo "[install] $venv already holds $sha"
  exit 0
fi

rm -rf "$venv"
uv venv --python "${NEXUS_BENCH_PYTHON:-3.12}" "$venv"
uv pip install --python "$venv/bin/python" "nexus-mcp-ci @ git+file://$PROJECT_ROOT@$sha"
echo "$sha" > "$venv/NEXUS_REF"
version="$("$venv/bin/python" -c "import importlib.metadata as m; print(m.version('nexus-mcp-ci'))")"
echo "[install] nexus-mcp-ci $version from $sha in $venv"
