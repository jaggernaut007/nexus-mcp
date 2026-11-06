"""Per-language benchmark corpora: load the manifest and make pinned checkouts.

    python -m benchmarks.corpora list
    python -m benchmarks.corpora setup                  # every corpus
    python -m benchmarks.corpora setup --languages C#,PHP

Each corpus is a shallow, sparse checkout of one commit under benchmarks/repos/<name>.
Agent config files of the corpus (CLAUDE.md, .claude/, .mcp.json) are deleted after the
checkout, so neither benchmark condition gets instructions that the other does not get.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

BENCH_DIR = Path(__file__).resolve().parent
MANIFEST = BENCH_DIR / "corpora.yaml"
REPOS_DIR = BENCH_DIR / "repos"
TIERS = ("quick", "retrieval", "graph", "live")
# Files that Claude Code reads on its own. A corpus must not bring them into a run.
AGENT_CONFIG = ("CLAUDE.md", "CLAUDE.local.md", ".claude", ".mcp.json")


def load(path: Path = MANIFEST) -> List[Dict[str, Any]]:
    """The corpora of the manifest. Raises ValueError on a bad entry."""
    corpora = yaml.safe_load(path.read_text())["corpora"]
    names = set()
    for c in corpora:
        for key in ("name", "language", "url", "pin", "tiers"):
            if not c.get(key):
                raise ValueError(f"corpus {c.get('name')!r} has no {key!r}")
        if c["name"] in names:
            raise ValueError(f"corpus {c['name']!r} is listed twice")
        names.add(c["name"])
        unknown = set(c["tiers"]) - set(TIERS)
        if unknown:
            raise ValueError(f"corpus {c['name']!r} has unknown tiers {sorted(unknown)}")
        if "live" in c["tiers"] and not c.get("tasks"):
            raise ValueError(f"corpus {c['name']!r} is in the live tier but has no tasks")
        c.setdefault("sparse", [])
    return corpora


def select(
    corpora: List[Dict[str, Any]],
    languages: Optional[Iterable[str]] = None,
    tier: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """The corpora of the given languages (case-insensitive) and tier. None: all."""
    wanted = {lang.strip().lower() for lang in languages} if languages else None
    return [
        c for c in corpora
        if (wanted is None or c["language"].lower() in wanted)
        and (tier is None or tier in c["tiers"])
    ]


def repo_dir(corpus: Dict[str, Any], repos_dir: Path = REPOS_DIR) -> Path:
    """The local checkout folder of one corpus."""
    return repos_dir / corpus["name"]


def _git(args: List[str], cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {out.stderr.strip()}")
    return out.stdout.strip()


def head(path: Path) -> Optional[str]:
    """The checked-out commit of `path`, or None when it is not a git checkout."""
    if not (path / ".git").exists():
        return None
    try:
        return _git(["rev-parse", "HEAD"], path)
    except RuntimeError:
        return None


def strip_agent_config(path: Path) -> List[str]:
    """Delete the agent config files at the top of a checkout. Returns the names deleted."""
    removed = []
    for name in AGENT_CONFIG:
        target = path / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(name)
        elif target.exists():
            target.unlink()
            removed.append(name)
    return removed


def checkout(corpus: Dict[str, Any], repos_dir: Path = REPOS_DIR) -> Path:
    """Make the pinned, sparse, shallow checkout of one corpus. Idempotent."""
    dest = repo_dir(corpus, repos_dir)
    if head(dest) == corpus["pin"]:
        strip_agent_config(dest)
        print(f"[corpora] {corpus['name']} already at {corpus['pin'][:10]}", file=sys.stderr)
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    print(f"[corpora] {corpus['name']} @ {corpus['pin'][:10]}", file=sys.stderr)
    _git(["init", "-q"], dest)
    _git(["remote", "add", "origin", corpus["url"]], dest)
    if corpus["sparse"]:
        _git(["sparse-checkout", "set", "--cone", *corpus["sparse"]], dest)
    _git(["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", corpus["pin"]], dest)
    _git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest)
    if head(dest) != corpus["pin"]:
        raise RuntimeError(f"{corpus['name']}: checkout is not at {corpus['pin']}")
    strip_agent_config(dest)
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: `list` the corpora, or `setup` (clone) them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "setup"])
    parser.add_argument("--languages", default="", help="Comma-separated languages")
    parser.add_argument("--tier", choices=TIERS, default=None)
    args = parser.parse_args(argv)
    langs = [s for s in args.languages.split(",") if s.strip()] or None
    chosen = select(load(), langs, args.tier)
    if args.command == "list":
        for c in chosen:
            print(f"{c['language']:<11} {c['name']:<16} {c['pin'][:10]}  {','.join(c['tiers'])}")
        return 0
    for c in chosen:
        checkout(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
