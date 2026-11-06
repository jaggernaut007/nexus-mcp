"""Print a table of a languages-<label>.json summary: one line per corpus.

    python -m benchmarks.summarize_languages before [after]

With two labels the table shows "before → after" for each cell. Read-only.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULTS_DIR = Path(__file__).resolve().parent / "results"
COLUMNS = ("corpus", "language", "quick n/g hit@1", "tokens n/g", "retrieval@1", "fn nodes",
           "calls/fn", "live tok n/g", "live ok n/g")


def load(label: str) -> Dict[str, Any]:
    """The rows of one labelled run, keyed by corpus name."""
    return json.loads((RESULTS_DIR / f"languages-{label}.json").read_text())["rows"]


def _status(tier: Any) -> Optional[str]:
    if isinstance(tier, dict) and tier.get("unsupported"):
        return "no symbols"
    if isinstance(tier, dict) and "error" in tier:
        return "crash"
    return None


def cells(row: Dict[str, Any]) -> List[str]:
    """The table cells of one corpus row."""
    q = row.get("quick")
    if q is None:
        quick = tok = "-"
    elif _status(q):
        quick = tok = _status(q)
    else:
        s = q["summary"]
        quick = f"{s['nexus']['hit_at_1']:.2f}/{s['grep']['hit_at_1']:.2f}"
        tok = f"{s['nexus']['median_tokens']:.0f}/{s['grep']['median_tokens']:.0f}"
    r = row.get("retrieval")
    if r is None:
        ret = "-"
    elif _status(r):
        ret = _status(r)
    else:
        ret = f"{r['modes']['hybrid-source-first']['hit@1']:.2f}"
    g = row.get("graph")
    if g is None:
        fn = cpf = "-"
    elif _status(g):
        fn = cpf = _status(g)
    else:
        fns = sum(v["functions"] for v in g.values())
        calls = sum(v["calls"] for v in g.values())
        fn, cpf = str(fns), f"{calls / fns:.2f}" if fns else "0"
    live = row.get("live")
    if live is None:
        lt = lo = "-"
    elif _status(live):
        lt = lo = _status(live)
    else:
        c = live["conditions"]
        n, b = c.get("nexus", {}), c.get("baseline", {})
        lt = f"{n.get('total_tokens', 0):.0f}/{b.get('total_tokens', 0):.0f}"
        lo = f"{n.get('tasks_correct')}/{b.get('tasks_correct')} of {n.get('n_tasks')}"
    return [row["name"], row["language"], quick, tok, ret, fn, cpf, lt, lo]


def render(rows: Dict[str, Any], other: Optional[Dict[str, Any]] = None) -> str:
    """A text table; with `other` each cell reads 'before → after'."""
    lines = [" | ".join(COLUMNS), " | ".join("---" for _ in COLUMNS)]
    for name, row in rows.items():
        a = cells(row)
        if other is not None and name in other:
            b = cells(other[name])
            a = a[:2] + [x if x == y else f"{x} → {y}" for x, y in zip(a[2:], b[2:])]
        lines.append(" | ".join(a))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: print the table of one or two labels."""
    labels = (argv if argv is not None else sys.argv[1:])[:2]
    if not labels:
        print(__doc__)
        return 2
    rows = load(labels[0])
    other = load(labels[1]) if len(labels) > 1 else None
    print(render(rows, other))
    return 0


if __name__ == "__main__":
    sys.exit(main())
