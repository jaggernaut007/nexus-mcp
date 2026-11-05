"""Set the release version in every file that carries it.

Run: python scripts/bump_version.py 2.3.0

It edits the version of pyproject.toml, CITATION.cff, server.json, the plugin manifest, the
Desktop Extension, the Gemini extension, and the pinned `nexus-mcp-ci==X` launch commands.
It does not touch `site/index.html`, the README "New in" note, CHANGELOG.md or
`date-released`: see docs/RELEASING.md. Then run `python scripts/check_docs.py`.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _json_set(path: Path, edit) -> None:
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def bump(new: str) -> None:
    if not SEMVER.match(new):
        raise SystemExit(f"'{new}' is not a version like 2.3.0")

    def regex(rel: str, pattern: str, repl: str) -> None:
        path = ROOT / rel
        text, count = re.subn(pattern, repl, path.read_text(), count=1, flags=re.M)
        if not count:
            raise SystemExit(f"{rel}: pattern not found: {pattern}")
        path.write_text(text)

    regex("pyproject.toml", r'^version = "[^"]+"', f'version = "{new}"')
    regex("CITATION.cff", r"^version: \S+", f"version: {new}")
    regex("mcpb/pyproject.toml", r'^version = "[^"]+"', f'version = "{new}"')
    regex("mcpb/pyproject.toml", r"nexus-mcp-ci==[0-9.]+", f"nexus-mcp-ci=={new}")

    def server(data: dict) -> None:
        data["version"] = new
        for package in data["packages"]:
            package["version"] = new

    _json_set(ROOT / "server.json", server)
    _json_set(ROOT / "plugin" / ".claude-plugin" / "plugin.json", lambda d: d.update(version=new))
    _json_set(ROOT / "mcpb" / "manifest.json", lambda d: d.update(version=new))

    def gemini(data: dict) -> None:
        data["version"] = new
        data["mcpServers"]["nexus-mcp"]["args"] = [f"nexus-mcp-ci=={new}"]

    _json_set(ROOT / "gemini-extension.json", gemini)

    def plugin_mcp(data: dict) -> None:
        data["mcpServers"]["nexus-mcp"]["args"] = [f"nexus-mcp-ci=={new}"]

    _json_set(ROOT / "plugin" / ".mcp.json", plugin_mcp)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("Usage: python scripts/bump_version.py X.Y.Z", file=sys.stderr)
        return 2
    bump(argv[0])
    print(
        f"Set version {argv[0]}. Now: python scripts/check_docs.py, CHANGELOG.md, site/index.html."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
