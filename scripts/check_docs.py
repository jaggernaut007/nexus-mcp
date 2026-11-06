"""Fail when the docs say something the code does not. Runs in CI.

Checks:
- the version in CITATION.cff, server.json and the plugin manifest matches pyproject.toml;
- every "N tools" claim in README.md, CLAUDE.md, llms.txt and CITATION.cff matches the
  number of tools in server.py;
- no hand-written test count ("1,132 tests") in README.md or CLAUDE.md: it goes stale on
  every PR;
- every page in docs/ is linked from docs/README.md;
- the generated pages (CLI.md, CONFIGURATION.md) are current.

Run: python scripts/check_docs.py
"""

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
TOOLS_CLAIM = re.compile(r"\b(\d+) (?:focused |MCP )?tools\b")
TEST_COUNT = re.compile(r"\b\d{1,2},\d{3} tests\b")
INTERNAL_DOCS = {"README.md"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_version() -> str:
    match = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    return match.group(1)


def check_versions() -> List[str]:
    want = project_version()
    found = {
        "CITATION.cff": re.search(
            r"^version: (\S+)", (ROOT / "CITATION.cff").read_text(), re.M
        ).group(1),
        "plugin/.claude-plugin/plugin.json": json.loads(
            (ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text()
        )["version"],
        "plugin/.cursor-plugin/plugin.json": json.loads(
            (ROOT / "plugin" / ".cursor-plugin" / "plugin.json").read_text()
        )["version"],
        # The server reports this value to MCP clients.
        "src/nexus_mcp/__init__.py": re.search(
            r'^__version__ = "([^"]+)"',
            (ROOT / "src" / "nexus_mcp" / "__init__.py").read_text(),
            re.M,
        ).group(1),
    }
    server = json.loads((ROOT / "server.json").read_text())
    found["server.json"] = server["version"]
    for package in server["packages"]:
        found[f"server.json package {package['identifier']}"] = package["version"]
    # The channel manifests: a version field and a pinned `nexus-mcp-ci==X` launch.
    found["mcpb/manifest.json"] = json.loads((ROOT / "mcpb" / "manifest.json").read_text())[
        "version"
    ]
    mcpb_toml = (ROOT / "mcpb" / "pyproject.toml").read_text()
    found["mcpb/pyproject.toml"] = re.search(r'^version = "([^"]+)"', mcpb_toml, re.M).group(1)
    found["mcpb/pyproject.toml dependency"] = re.search(
        r"nexus-mcp-ci==([^\"']+)", mcpb_toml
    ).group(1)
    gemini = json.loads((ROOT / "gemini-extension.json").read_text())
    found["gemini-extension.json"] = gemini["version"]
    found["gemini-extension.json launch"] = gemini["mcpServers"]["nexus-mcp"]["args"][0].split(
        "=="
    )[-1]
    plugin_mcp = json.loads((ROOT / "plugin" / ".mcp.json").read_text())
    found["plugin/.mcp.json launch"] = plugin_mcp["mcpServers"]["nexus-mcp"]["args"][0].split("==")[
        -1
    ]
    return [
        f"{where}: version {v}, pyproject.toml says {want}"
        for where, v in found.items()
        if v != want
    ]


def check_tool_count() -> List[str]:
    count = (ROOT / "src" / "nexus_mcp" / "server.py").read_text().count("@mcp.tool")
    errors = []
    for rel in ("README.md", "CLAUDE.md", "llms.txt", "CITATION.cff", "site/index.html"):
        for number in TOOLS_CLAIM.findall((ROOT / rel).read_text()):
            if int(number) != count:
                errors.append(f"{rel}: says {number} tools, server.py has {count}")
    return errors


def check_test_counts() -> List[str]:
    errors = []
    for rel in ("README.md", "CLAUDE.md"):
        for hit in TEST_COUNT.findall((ROOT / rel).read_text()):
            errors.append(f"{rel}: hand-written test count '{hit}'. Remove it; it goes stale.")
    return errors


def check_docs_index() -> List[str]:
    index = (ROOT / "docs" / "README.md").read_text()
    return [
        f"docs/{page.name} is not linked from docs/README.md"
        for page in sorted((ROOT / "docs").glob("*.md"))
        if page.name not in INTERNAL_DOCS and f"({page.name})" not in index
    ]


def check_generated() -> List[str]:
    errors = []
    if _load("gen_config_docs").main(["--check"]):
        errors.append("docs/CONFIGURATION.md is out of date")
    # argparse help differs between Python versions; CI checks CLI.md on 3.12 only.
    if sys.version_info[:2] == (3, 12) and _load("gen_cli_docs").main(["--check"]):
        errors.append("docs/CLI.md is out of date")
    return errors


def main() -> int:
    errors = (
        check_versions()
        + check_tool_count()
        + check_test_counts()
        + check_docs_index()
        + check_generated()
    )
    for error in errors:
        print(f"docs: {error}")
    if not errors:
        print("docs: all checks passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
