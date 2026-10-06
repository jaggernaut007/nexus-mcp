"""The tool surface is the routing contract: names, descriptions, enums, annotations.

An agent chooses a tool from its name, its description and the server instructions. These
tests keep that text inside the limits that clients enforce, keep the documented names
equal to the registered names (issues #4 and #10), and keep the hints honest.
"""

import asyncio
import re
from pathlib import Path

import pytest

import nexus_mcp.server as server_module

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CODE_TEXT_LIMIT = 2048  # Claude Code cuts each description and the instructions here
CODEX_OPENING_LIMIT = 512  # Codex reads the first 512 characters of the instructions first
READ_TOOLS = {"status", "health", "search", "find_symbol", "graph", "analyze", "explain", "map"}
ENUMS = {
    ("search", "mode"): ["hybrid", "vector", "bm25"],
    ("graph", "direction"): ["callers", "callees"],
    ("search", "detail"): ["compact", "full"],
    ("graph", "detail"): ["compact", "full"],
    ("explain", "verbosity"): ["summary", "detailed", "full"],
    ("map", "detail"): ["summary", "architecture", "full"],
    ("memory", "action"): ["store", "search", "delete"],
    ("memory", "ttl"): ["permanent", "month", "week", "day", "session"],
}


@pytest.fixture(scope="module")
def mcp():
    return server_module.create_server()


@pytest.fixture(scope="module")
def tools(mcp):
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def _hints(tool):
    return tool.to_mcp_tool().annotations


class TestInstructions:
    def test_instructions_are_sent_and_fit_the_client_limit(self, mcp):
        assert mcp.instructions
        assert len(mcp.instructions) <= CLAUDE_CODE_TEXT_LIMIT

    def test_opening_paragraph_stands_alone_and_names_the_first_steps(self, mcp):
        opening = mcp.instructions.split("\n\n")[0]
        assert len(opening) <= CODEX_OPENING_LIMIT
        assert "codebase" in opening and "where something is" in opening

    def test_instructions_only_name_tools_that_exist(self, mcp, tools):
        named = set(re.findall(r"`([a-z_]+)`", mcp.instructions)) - {"indexed"}
        assert named <= set(tools), named - set(tools)

    def test_instructions_name_every_tool_that_an_agent_should_choose(self, mcp, tools):
        # `status` is left out on purpose since 2026-10-06: the index attaches at server
        # start, and a `status` call first cost one model turn in every benchmark run.
        named = set(re.findall(r"`([a-z_]+)`", mcp.instructions))
        assert {"index", "search", "find_symbol", "explain", "graph", "map",
                "analyze", "memory"} <= named
        assert "status" not in named

    def test_instructions_tell_the_agent_when_not_to_use_the_server(self, mcp):
        assert "built-in grep" in mcp.instructions


class TestDescriptions:
    def test_every_description_fits_the_client_limit_and_is_not_a_stub(self, tools):
        for name, tool in tools.items():
            text = tool.description or ""
            assert 150 <= len(text) <= CLAUDE_CODE_TEXT_LIMIT, name

    def test_no_description_carries_rename_residue(self, tools):
        for name, tool in tools.items():
            params = (tool.parameters or {}).get("properties", {})
            blob = (tool.description or "") + " ".join(
                str(p.get("description", "")) for p in params.values()
            )
            assert "(was " not in blob, name
            assert "MUST" not in blob, name

    def test_every_parameter_has_a_description(self, tools):
        for name, tool in tools.items():
            for param, schema in (tool.parameters or {}).get("properties", {}).items():
                assert schema.get("description"), f"{name}.{param}"

    @pytest.mark.parametrize("name,phrases", [
        ("search", ["where is", "how does", "find the code"]),
        ("graph", ["who calls", "what breaks", "transitive"]),
        ("map", ["overview", "where do I start"]),
        ("memory", ["remember that", "what did we decide"]),
        ("analyze", ["dead code", "most complex"]),
        ("find_symbol", ["by name"]),
        ("explain", ["briefing", "who calls"]),
    ])
    def test_description_contains_the_words_users_say(self, tools, name, phrases):
        text = tools[name].description.lower()
        for phrase in phrases:
            assert phrase.lower() in text, f"{name}: {phrase!r}"


class TestSchema:
    @pytest.mark.parametrize("key,values", list(ENUMS.items()))
    def test_choice_parameters_are_enums(self, tools, key, values):
        tool, param = key
        assert tools[tool].parameters["properties"][param]["enum"] == values


class TestAnnotations:
    def test_read_tools_are_marked_read_only(self, tools):
        for name in READ_TOOLS:
            hints = _hints(tools[name])
            assert hints.readOnlyHint is True, name
            assert hints.destructiveHint is False, name

    def test_index_writes_but_does_not_destroy(self, tools):
        hints = _hints(tools["index"])
        assert hints.readOnlyHint is False
        assert hints.destructiveHint is False

    def test_memory_is_not_read_only_and_may_delete(self, tools):
        hints = _hints(tools["memory"])
        assert hints.readOnlyHint is False
        assert hints.destructiveHint is True

    def test_every_tool_has_a_title(self, tools):
        for name, tool in tools.items():
            assert tool.to_mcp_tool().title or _hints(tool).title, name


class TestDocsMatchRegisteredTools:
    """Docs that list tools must list exactly the registered tools (no stale names)."""

    def test_llms_txt_lists_exactly_the_registered_tools(self, tools):
        text = (ROOT / "llms.txt").read_text()
        listed = set(re.findall(r"^- \[([a-z_]+)\]\(", text, flags=re.M))
        names = set(tools)
        assert names <= listed
        assert listed - names <= set(), listed - names

    def test_readme_tool_table_lists_exactly_the_registered_tools(self, tools):
        text = (ROOT / "README.md").read_text()
        listed = set(re.findall(r"^\| `([a-z_]+)\(", text, flags=re.M))
        assert listed == set(tools), listed ^ set(tools)

    def test_usage_guide_has_a_section_for_each_tool(self, tools):
        text = (ROOT / "docs" / "USAGE_GUIDE.md").read_text()
        listed = set(re.findall(r"^#### `([a-z_]+)`", text, flags=re.M))
        assert listed == set(tools), listed ^ set(tools)

    def test_skill_routes_to_registered_tools_only(self, tools):
        text = (ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md").read_text()
        rows = re.findall(r"^\| .*? \| (.*?) \|$", text, flags=re.M)
        named = {n for row in rows for n in re.findall(r"`([a-z_]+)`", row)}
        routing = {"search", "find_symbol", "explain", "graph", "map", "analyze", "memory"}
        assert routing <= named
        known_args = {"direction", "callers", "callees", "transitive", "detail", "summary",
                      "architecture", "full", "path", "action", "store", "delete"}
        assert named - set(tools) - known_args == set(), named - set(tools) - known_args

    def test_plugin_version_matches_the_package(self):
        import json
        import re as _re

        pyproject = (ROOT / "pyproject.toml").read_text()
        version = _re.search(r'^version = "([^"]+)"', pyproject, flags=_re.M).group(1)
        plugin = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        assert plugin["version"] == version

    def test_plugin_and_manifests_use_the_same_server_name(self):
        import json

        server = json.loads((ROOT / "plugin" / ".mcp.json").read_text())["mcpServers"]
        assert set(server) == {"nexus-mcp"}
