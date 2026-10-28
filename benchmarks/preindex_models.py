"""Build one nexus index per embedding model for a benchmark repo.

    PYTHONPATH=src:. python -m benchmarks.preindex_models --repo jobscout \\
        --models bge-small-en,granite-97m-r2-int8

Each model gets its own folder, `benchmarks/repos/<repo>/.nexus-<model>`, so the indexes
never mix. The runner starts the server with the same folder (conditions.model_mcp_config).
A model that already has an index is skipped; delete its folder to rebuild. A failed build
removes its own folder.
Each model is indexed in its own process, so peak memory is that model's own.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from benchmarks.conditions import BENCH_EMBEDDING_DEVICE

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
REPOS_DIR = BENCH_DIR / "repos"
META_FILE = BENCH_DIR / "results" / "setup_meta.json"


def storage_dir_for(repo_dir: Path, model: str) -> Path:
    """Index folder of one model inside a benchmark repo."""
    return repo_dir / f".nexus-{model}"


def index_env(
    storage_dir: Path, model: str, base_env: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Environment of the child process that builds the index of one model."""
    env = dict(base_env if base_env is not None else os.environ)
    env["NEXUS_STORAGE_DIR"] = str(storage_dir)
    env["NEXUS_EMBEDDING_MODEL"] = model
    env["NEXUS_EMBEDDING_DEVICE"] = BENCH_EMBEDDING_DEVICE
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), str(REPO_ROOT)])
    return env


def preindex(repo_dir: Path, repo_name: str, model: str) -> bool:
    """Index `repo_dir` with `model`. Returns False when the child process failed."""
    storage = storage_dir_for(repo_dir, model)
    if storage.exists():
        print(f"[preindex] {repo_name}@{model}: {storage.name} exists, skipping")
        return True
    print(f"[preindex] {repo_name}@{model}: indexing (can take several minutes)...")
    cmd = [
        sys.executable, "-m", "benchmarks._preindex_one",
        str(repo_dir), f"{repo_name}@{model}", str(META_FILE),
    ]
    proc = subprocess.run(cmd, env=index_env(storage, model), cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        # A half-built folder would count as "already indexed" on the next run.
        shutil.rmtree(storage, ignore_errors=True)
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Folder name under benchmarks/repos/")
    parser.add_argument("--models", required=True, help="Comma-separated embedding models")
    args = parser.parse_args(argv)

    repo_dir = REPOS_DIR / args.repo
    if not repo_dir.is_dir():
        raise SystemExit(f"Repo not found at {repo_dir}.")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    failed = [m for m in models if not preindex(repo_dir, args.repo, m)]
    for m in failed:
        print(f"[preindex] {m} failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
