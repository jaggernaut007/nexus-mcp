"""Tests for `nexus-mcp-ci setup` (src/nexus_mcp/setup_clients.py)."""

import json
import shutil

import pytest

from nexus_mcp import setup_clients as sc
from nexus_mcp.evaluate import cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake home and project. The `claude` CLI is hidden so the file path is tested."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    real_which = shutil.which
    monkeypatch.setattr(
        sc.shutil,
        "which",
        lambda name, *a, **k: None if name == "claude" else real_which(name, *a, **k),
    )
    return home, project


def _run(home, project, *extra):
    return sc.run_setup(["--home", str(home), "--project", str(project), "--no-index", *extra])


def test_launch_spec_uses_uvx():
    spec = sc.launch_spec()
    assert spec["command"] == "uvx"
    assert spec["args"][0].startswith("nexus-mcp-ci")


def test_skill_text_has_front_matter_and_install_section():
    text = sc.skill_text()
    assert text.startswith("---")
    assert "nexus-install" in text


def test_setup_dry_run_writes_nothing(env, capsys):
    home, project = env
    assert _run(home, project, "--client", "cursor", "--client", "codex", "--dry-run") == 0
    assert list(home.rglob("*")) == []
    assert list(project.rglob("*")) == []
    assert "Dry run" in capsys.readouterr().out


def test_setup_without_yes_and_no_tty_writes_nothing(env, monkeypatch, capsys):
    home, project = env
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: False)
    assert _run(home, project, "--client", "cursor") == 0
    assert list(home.rglob("*")) == []
    assert "skipped" in capsys.readouterr().out


def test_setup_declined_prompt_writes_nothing(env, monkeypatch):
    home, project = env
    monkeypatch.setattr(sc, "_confirm", lambda question: False)
    assert _run(home, project, "--client", "cursor") == 0
    assert list(home.rglob("*")) == []


def test_setup_cursor_writes_server_and_rules(env):
    home, project = env
    assert _run(home, project, "--client", "cursor", "--yes") == 0
    cfg = json.loads((home / ".cursor" / "mcp.json").read_text())
    assert cfg["mcpServers"]["nexus-mcp"]["command"] == "uvx"
    rules = (project / ".cursor" / "rules" / "nexus-mcp.mdc").read_text()
    assert rules.startswith("---\ndescription:")
    assert "If the nexus tools are missing" not in rules
    assert "Using nexus-mcp" in rules


def test_setup_keeps_other_servers_in_existing_config(env):
    home, project = env
    cfg_path = home / ".cursor" / "mcp.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    assert _run(home, project, "--client", "cursor", "--yes", "--no-skill") == 0
    cfg = json.loads(cfg_path.read_text())
    assert cfg["mcpServers"]["other"] == {"command": "x"}
    assert "nexus-mcp" in cfg["mcpServers"]
    assert cfg["theme"] == "dark"
    assert cfg_path.with_name("mcp.json.nexus-bak").exists()


def test_setup_refuses_a_config_that_is_not_valid_json(env, capsys):
    home, project = env
    cfg_path = home / ".cursor" / "mcp.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("{not json")
    assert _run(home, project, "--client", "cursor", "--yes", "--no-skill") == 1
    assert cfg_path.read_text() == "{not json"
    assert "not valid JSON" in capsys.readouterr().err


def test_setup_vscode_uses_servers_key(env):
    home, project = env
    assert _run(home, project, "--client", "vscode", "--yes") == 0
    cfg = json.loads((project / ".vscode" / "mcp.json").read_text())
    assert cfg["servers"]["nexus-mcp"]["type"] == "stdio"


def test_setup_codex_writes_toml_and_agents_block(env):
    home, project = env
    (project / "AGENTS.md").write_text("# My rules\n\nKeep this.\n")
    assert _run(home, project, "--client", "codex", "--yes") == 0
    toml = (home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.nexus-mcp]" in toml
    assert 'command = "uvx"' in toml
    agents = (project / "AGENTS.md").read_text()
    assert "Keep this." in agents
    assert sc.BEGIN in agents and sc.END in agents
    assert "If the nexus tools are missing" not in agents


def test_setup_claude_code_skill_keeps_the_install_section(env):
    home, project = env
    assert _run(home, project, "--client", "claude-code", "--yes") == 0
    skill = (home / ".claude" / "skills" / "nexus-mcp" / "SKILL.md").read_text()
    assert "If the nexus tools are missing" in skill


def test_setup_twice_keeps_one_marked_block(env):
    home, project = env
    for _ in range(2):
        assert _run(home, project, "--client", "gemini", "--yes") == 0
    text = (project / "GEMINI.md").read_text()
    assert text.count(sc.BEGIN) == 1


def test_setup_uninstall_restores_text_and_removes_files(env):
    home, project = env
    (project / "AGENTS.md").write_text("# My rules\n\nKeep this.\n")
    cursor_cfg = home / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    cursor_cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    clients = ["--client", "codex", "--client", "cursor"]
    assert _run(home, project, *clients, "--yes") == 0
    assert _run(home, project, *clients, "--yes", "--uninstall") == 0
    assert (project / "AGENTS.md").read_text().strip() == "# My rules\n\nKeep this."
    assert not (project / ".cursor" / "rules" / "nexus-mcp.mdc").exists()
    assert json.loads(cursor_cfg.read_text()) == {"mcpServers": {"other": {"command": "x"}}}


def test_setup_uninstall_deletes_block_only_file(env):
    home, project = env
    assert _run(home, project, "--client", "gemini", "--yes") == 0
    assert _run(home, project, "--client", "gemini", "--yes", "--uninstall") == 0
    assert not (project / "GEMINI.md").exists()


def test_setup_claude_code_uses_cli_when_present(env, monkeypatch):
    home, project = env
    calls = []
    monkeypatch.setattr(
        sc.shutil, "which", lambda name, *a, **k: "/bin/claude" if name == "claude" else None
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    # Requirement changed on 2026-11-06: the CLI runs only for the real home. The test now
    # makes the fake folder the real home, instead of passing the hidden --home flag.
    monkeypatch.setattr(sc.Path, "home", classmethod(lambda cls: home))
    code = sc.run_setup(
        ["--project", str(project), "--no-index", "--client", "claude-code", "--yes"]
    )
    assert code == 0
    assert calls[0][:5] == ["/bin/claude", "mcp", "add", "--scope", "user"]
    assert (home / ".claude" / "skills" / "nexus-mcp" / "SKILL.md").read_text().startswith("---")


def test_setup_with_another_home_never_runs_the_claude_cli(env, monkeypatch):
    # Regression: `setup --home <dir>` ran the real `claude mcp add` and changed the real config.
    home, project = env
    calls = []
    monkeypatch.setattr(
        sc.shutil, "which", lambda name, *a, **k: "/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert _run(home, project, "--client", "claude-code", "--yes", "--no-skill") == 0
    assert calls == []
    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["mcpServers"]["nexus-mcp"]["command"] == "uvx"


def test_setup_claude_code_falls_back_to_claude_json(env):
    home, project = env
    assert _run(home, project, "--client", "claude-code", "--yes", "--no-skill") == 0
    cfg = json.loads((home / ".claude.json").read_text())
    assert cfg["mcpServers"]["nexus-mcp"]["command"] == "uvx"


def test_setup_no_client_found_exits_2(env, capsys):
    home, project = env
    assert _run(home, project) == 2
    assert "No MCP client found" in capsys.readouterr().err


def test_setup_unknown_client_is_rejected_by_the_parser(env):
    home, project = env
    with pytest.raises(SystemExit):
        _run(home, project, "--client", "nope")


def test_detect_clients_finds_config_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda *a, **k: None)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".codex").mkdir()
    assert sc.detect_clients(tmp_path) == ["cursor", "codex"]


def test_dispatch_routes_setup(env):
    home, project = env
    code = cli.dispatch(
        ["setup", "--home", str(home), "--project", str(project), "--client", "cursor", "--dry-run"]
    )
    assert code == 0


def test_skill_loaders_cut_the_install_section():
    from nexus_mcp.evaluate import runners

    assert "If the nexus tools are missing" not in runners.skill_text()
