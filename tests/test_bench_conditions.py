"""Tests for benchmarks.conditions: pure argv/env builders."""


import pytest

from benchmarks.conditions import (
    apply_bare_isolation,
    apply_reduced_isolation,
    build_argv,
    build_env,
    build_run,
    has_api_key,
    strip_frontmatter,
)


class TestStripFrontmatter:
    def test_strip_frontmatter_removes_yaml_block(self):
        text = "---\nname: x\ndescription: y\n---\n\nBody text here."
        assert strip_frontmatter(text) == "Body text here."

    def test_strip_frontmatter_no_frontmatter_returns_unchanged(self):
        text = "Just a body, no frontmatter."
        assert strip_frontmatter(text) == text

    def test_strip_frontmatter_unterminated_block_returns_unchanged(self):
        text = "---\nname: x\nno closing delimiter"
        assert strip_frontmatter(text) == text


class TestBuildArgv:
    def test_build_argv_baseline_has_no_mcp_config(self):
        argv = build_argv("baseline", "hello", "claude-sonnet-5", 1.0)
        assert "--mcp-config" not in argv
        assert "--append-system-prompt" not in argv
        assert argv[-1] == "hello"

    def test_build_argv_nexus_includes_mcp_config_and_skill(self, tmp_path):
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("---\nname: nexus-mcp\n---\n\nUse nexus tools.")
        mcp_config = tmp_path / "nexus.json"
        mcp_config.write_text("{}")

        argv = build_argv(
            "nexus",
            "hello",
            "claude-sonnet-5",
            1.0,
            mcp_config_path=mcp_config,
            skill_path=skill_path,
        )
        assert "--mcp-config" in argv
        assert str(mcp_config) in argv
        idx = argv.index("--append-system-prompt")
        assert argv[idx + 1] == "Use nexus tools."

    def test_build_argv_nexus_plugin_uses_plugin_dir(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        argv = build_argv(
            "nexus-plugin", "hello", "claude-sonnet-5", 1.0, plugin_dir=plugin_dir
        )
        assert "--plugin-dir" in argv
        assert str(plugin_dir) in argv

    def test_build_argv_unknown_condition_raises(self):
        with pytest.raises(ValueError):
            build_argv("bogus", "hello", "claude-sonnet-5", 1.0)

    def test_build_argv_includes_budget_and_model(self):
        argv = build_argv("baseline", "hello", "claude-haiku-4-5", 0.5)
        assert "--model" in argv
        assert "claude-haiku-4-5" in argv
        assert "--max-budget-usd" in argv
        assert "0.5" in argv

    def test_build_argv_disallows_mutating_tools(self):
        argv = build_argv("baseline", "hello", "claude-sonnet-5", 1.0)
        idx = argv.index("--disallowedTools")
        assert "Edit" in argv[idx + 1]
        assert "Write" in argv[idx + 1]


class TestBuildEnv:
    def test_build_env_sets_config_dir(self, tmp_path):
        env = build_env(tmp_path, base_env={"PATH": "/usr/bin"})
        assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)
        assert env["PATH"] == "/usr/bin"

    def test_has_api_key_true(self):
        assert has_api_key({"ANTHROPIC_API_KEY": "sk-x"}) is True

    def test_has_api_key_false(self):
        assert has_api_key({}) is False


class TestIsolation:
    def test_apply_bare_isolation_inserts_flag(self):
        argv = ["claude", "-p", "--model", "x"]
        result = apply_bare_isolation(argv, {"ANTHROPIC_API_KEY": "sk-x"})
        assert result[2] == "--bare"

    def test_apply_bare_isolation_requires_api_key(self):
        with pytest.raises(ValueError):
            apply_bare_isolation(["claude", "-p"], {})

    def test_apply_reduced_isolation_appends_setting_sources(self):
        argv = ["claude", "-p"]
        result = apply_reduced_isolation(argv)
        assert "--setting-sources" in result
        assert result[-1] == ""


class TestBuildRun:
    def test_build_run_uses_bare_when_api_key_present(self, tmp_path):
        built = build_run(
            "baseline", "hi", "claude-sonnet-5", 1.0, tmp_path, env={"ANTHROPIC_API_KEY": "sk-x"}
        )
        assert built["isolation_mode"] == "bare"
        assert "--bare" in built["argv"]

    def test_build_run_uses_reduced_when_no_api_key(self, tmp_path):
        built = build_run("baseline", "hi", "claude-sonnet-5", 1.0, tmp_path, env={})
        assert built["isolation_mode"] == "reduced"
        assert "--setting-sources" in built["argv"]
