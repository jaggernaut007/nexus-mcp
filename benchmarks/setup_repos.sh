#!/usr/bin/env bash
# Clone + pin the benchmark target repos and pre-build their nexus-mcp index.
#
# Idempotent: re-running skips repos already cloned at the pinned SHA and
# skips re-indexing if a .nexus dir already exists. Delete
# benchmarks/repos/<name> to force a fresh clone+reindex.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_DIR="$SCRIPT_DIR/repos"
RESULTS_DIR="$SCRIPT_DIR/results"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$REPOS_DIR" "$RESULTS_DIR"

clone_and_pin() {
  local name="$1" url="$2" sha="$3"
  local dest="$REPOS_DIR/$name"

  if [ -d "$dest/.git" ]; then
    local current_sha
    current_sha="$(git -C "$dest" rev-parse HEAD)"
    if [ "$current_sha" = "$sha" ]; then
      echo "[setup] $name already at $sha, skipping clone"
      return 0
    fi
    echo "[setup] $name present but at wrong SHA ($current_sha != $sha) — re-fetching"
    git -C "$dest" fetch --depth 1 origin "$sha"
    git -C "$dest" checkout --detach "$sha"
    return 0
  fi

  echo "[setup] cloning $name @ $sha"
  git clone --filter=blob:none "$url" "$dest"
  git -C "$dest" checkout --detach "$sha"
}

preindex() {
  local name="$1"
  local dest="$REPOS_DIR/$name"
  local meta_file="$RESULTS_DIR/setup_meta.json"

  if [ -d "$dest/.nexus" ]; then
    echo "[setup] $name already indexed, skipping"
    return 0
  fi

  echo "[setup] indexing $name (this can take a few minutes on large repos)..."
  python3 "$SCRIPT_DIR/_preindex_one.py" "$dest" "$name" "$meta_file"
}

clone_and_pin "django" "https://github.com/django/django.git" "318a316a4c86a65bede68144f9546a6056d91379"
clone_and_pin "home-assistant-core" "https://github.com/home-assistant/core.git" "5b0d396bd84113aabf694d23cdbddbdaf574ca78"

# The pre-index step needs nexus_mcp importable; run from the project's venv.
export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"

preindex "django"
preindex "home-assistant-core"

echo "[setup] done. Repos + indexes ready under $REPOS_DIR"
