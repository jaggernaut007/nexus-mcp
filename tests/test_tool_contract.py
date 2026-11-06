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
READ_TOOLS = {"status", "search", "find_symbol", "graph", "analyze", "map"}
ENUMS = {
    ("graph", "direction"): ["callers", "callees"],
    ("map", "detail"): ["summary", "architecture", "full"],
    ("memory", "action"): ["store", "search", "delete"],
}
# The schema holds only what agents used in 64 live runs. The other options stay in
# `core_api` (the Python API) and are not sent to the model on each turn.
SCHEMA_PARAMETERS = {
    "search": {"query", "limit"},
    "find_symbol": {"symbol_name", "exact"},
    "graph": {"symbol_name", "direction", "transitive"},
    "memory": {"action", "content", "query", "tags", "memory_id"},
}


DEFAULT_TOOLS = {"index", "search", "find_symbol", "graph", "map"}


def _server(extra_tools: str):
    """Build the server with NEXUS_EXTRA_TOOLS set to `extra_tools`."""
    import os

    from nexus_mcp.config import reset_settings

    old = os.environ.get("NEXUS_EXTRA_TOOLS")
    os.environ["NEXUS_EXTRA_TOOLS"] = extra_tools
    reset_settings()
    try:
        return server_module.create_server()
    finally:
        if old is None:
            os.environ.pop("NEXUS_EXTRA_TOOLS", None)
        else:
            os.environ["NEXUS_EXTRA_TOOLS"] = old
        reset_settings()


@pytest.fixture(scope="module")
def mcp():
    return _server("all")


@pytest.fixture(scope="module")
def tools(mcp):
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


@pytest.fixture(scope="module")
def default_mcp():
    return _server("")


@pytest.fixture(scope="module")
def default_tools(default_mcp):
    return {t.name: t for t in asyncio.run(default_mcp.list_tools())}


class TestDefaultToolSurface:
    """By default the server shows 5 tools. Each description costs tokens on every turn."""

    def test_create_server_default_shows_only_the_five_core_tools(self, default_tools):
        assert set(default_tools) == DEFAULT_TOOLS

    def test_create_server_default_instructions_name_no_hidden_tool(self, default_mcp):
        named = set(re.findall(r"`([a-z_]+)`", default_mcp.instructions))
        assert named - {"indexed", "parse_config"} <= DEFAULT_TOOLS
        assert "what was decided" not in default_mcp.instructions

    def test_create_server_default_descriptions_name_no_hidden_tool(self, default_tools):
        for name, tool in default_tools.items():
            named = set(re.findall(r"`([a-z_]+)`", tool.description or ""))
            assert not named & {"status", "analyze", "memory"}, name

    def test_create_server_extra_tools_turns_on_only_the_named_tools(self):
        tools = {t.name for t in asyncio.run(_server("memory").list_tools())}
        assert tools == DEFAULT_TOOLS | {"memory"}

    def test_create_server_hidden_tool_cannot_be_called(self, default_mcp):
        with pytest.raises(Exception):
            asyncio.run(default_mcp.call_tool("status", {}))

    def test_skill_names_default_tools_only(self):
        text = (ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md").read_text()
        named = set(re.findall(r"`([a-z_]+)`", text))
        assert not named & {"status", "analyze", "memory"}


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
        named = set(re.findall(r"`([a-z_]+)`", mcp.instructions)) - {"indexed", "parse_config"}
        assert named <= set(tools), named - set(tools)

    def test_instructions_name_every_tool_that_an_agent_should_choose(self, mcp, tools):
        # `status` is left out on purpose since 2026-10-06: the index attaches at server
        # start, and a `status` call first cost one model turn in every benchmark run.
        named = set(re.findall(r"`([a-z_]+)`", mcp.instructions))
        assert {"index", "search", "find_symbol", "graph", "map",
                "analyze", "memory"} <= named
        assert not {"explain", "health"} & named
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
    ])
    def test_description_contains_the_words_users_say(self, tools, name, phrases):
        text = tools[name].description.lower()
        for phrase in phrases:
            assert phrase.lower() in text, f"{name}: {phrase!r}"


class TestSchema:
    @pytest.mark.parametrize("name,expected", list(SCHEMA_PARAMETERS.items()))
    def test_the_schema_holds_only_the_parameters_that_agents_use(self, tools, name, expected):
        assert set(tools[name].parameters["properties"]) == expected

    def test_the_tool_list_has_no_explain_and_no_health(self, tools):
        assert set(tools) == {
            "index", "status", "search", "find_symbol", "graph", "analyze", "map", "memory",
        }

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

    def test_skill_names_registered_tools_only(self, tools):
        text = (ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md").read_text()
        named = set(re.findall(r"`([a-z_]+)`", text))
        tool_like = {"status", "index", "health", "explain", "find_symbol", "search", "graph",
                     "map", "analyze", "memory", "find_callers", "recall", "remember"}
        assert (named & tool_like) <= set(tools), (named & tool_like) - set(tools)
        assert "find_symbol" in named

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
