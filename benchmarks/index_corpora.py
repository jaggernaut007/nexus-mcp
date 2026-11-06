"""Build the index of each language corpus with one pinned Nexus-MCP install.

    python -m benchmarks.index_corpora --nexus-venv benchmarks/.venv-v2.2.2
    python -m benchmarks.index_corpora --nexus-venv benchmarks/.venv-after --languages C#,PHP

The index goes to benchmarks/indexes/<venv name>/<corpus>, outside the corpus. Build time
and peak memory go to benchmarks/results/setup_meta-<venv name>.json. A corpus that
already has an index is skipped. The tier filter defaults to every corpus that any tier uses.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from benchmarks import corpora, runner


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: index the selected corpora with the install in --nexus-venv."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nexus-venv", type=Path, required=True)
    parser.add_argument("--languages", default="", help="Comma-separated languages")
    parser.add_argument("--names", default="", help="Comma-separated corpus names")
    args = parser.parse_args(argv)
    pinned = runner.load_pinned(args.nexus_venv)
    langs = [s for s in args.languages.split(",") if s.strip()] or None
    names = {s.strip() for s in args.names.split(",") if s.strip()}
    for corpus in corpora.select(corpora.load(), langs):
        if names and corpus["name"] not in names:
            continue
        runner.ensure_pinned_index(pinned, corpora.repo_dir(corpus))
    print("done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
