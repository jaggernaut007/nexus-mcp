#!/usr/bin/env python3
"""Rebuild llms-full.txt by concatenating the project docs for LLM consumption.

Run from anywhere: python scripts/build_llms_full.py
The header (title and summary) comes from the top of llms.txt.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECTIONS = [
    ("README", "README.md"),
    ("Installation Guide", "docs/INSTALLATION.md"),
    ("Usage Guide", "docs/USAGE_GUIDE.md"),
    ("Architecture", "docs/ARCHITECTURE.md"),
    ("Developer Guide", "docs/DEVELOPER_GUIDE.md"),
]


def build() -> str:
    # Title line, blank line, and the "> summary" blockquote from llms.txt.
    header = (ROOT / "llms.txt").read_text(encoding="utf-8").split("\n\n", 2)[:2]
    intro = (
        "This file contains the complete project documentation for Nexus-MCP, "
        "concatenated for LLM consumption."
    )
    parts = ["\n\n".join(header), intro, "---"]
    for number, (title, rel_path) in enumerate(SECTIONS, start=1):
        body = (ROOT / rel_path).read_text(encoding="utf-8").rstrip()
        parts.append(f"# Section {number}: {title}\n\n{body}")
        parts.append("---")
    return "\n\n".join(parts[:-1]) + "\n"


if __name__ == "__main__":
    out = ROOT / "llms-full.txt"
    out.write_text(build(), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
