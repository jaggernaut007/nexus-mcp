"""Tests for `nexus-mcp-ci setup` (src/nexus_mcp/setup_clients.py)."""

import json
import os
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


def test_launch_spec_uses_uvx_with_python_3_12():
    # Requirement changed on 2026-11-08: uvx picks the default Python, not one that fits
    # `requires-python`. On Python 3.13+ the install failed (no tree-sitter-languages wheel).
    spec = sc.launch_spec()
    assert spec["command"] == "uvx"
    assert spec["args"][:2] == ["--python", "3.12"]
    assert spec["args"][2].startswith("nexus-mcp-ci")


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


def test_setup_no_client_found_exits_2(env, capsys, monkeypatch):
    home, project = env
    monkeypatch.setattr(sc.shutil, "which", lambda *a, **k: None)  # no CLI on this PATH
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


# --- regressions from the code review of PR 24 ------------------------------------------


def test_launch_spec_is_not_pinned_for_a_source_install(monkeypatch):
    import importlib.metadata as md

    class Dist:
        version = "9.9.9"

        def __init__(self, direct_url):
            self._direct_url = direct_url

        def read_text(self, name):
            return self._direct_url

    monkeypatch.setattr(md, "distribution", lambda name: Dist('{"url": "file:///src"}'))
    assert sc.launch_spec()["args"] == ["--python", "3.12", "nexus-mcp-ci"]
    monkeypatch.setattr(md, "distribution", lambda name: Dist(None))
    assert sc.launch_spec()["args"] == ["--python", "3.12", "nexus-mcp-ci==9.9.9"]


def test_write_atomic_keeps_the_file_mode(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text("{}")
    os.chmod(cfg, 0o600)
    sc._write_atomic(cfg, '{"a": 1}')
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_write_atomic_follows_a_symlink(tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    sc._write_atomic(link, '{"a": 1}')
    assert link.is_symlink()
    assert json.loads(real.read_text()) == {"a": 1}


def test_setup_keeps_a_skill_file_of_the_user_and_uninstall_puts_it_back(env):
    home, project = env
    skill = home / ".claude" / "skills" / "nexus-mcp" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my own notes")
    assert _run(home, project, "--client", "claude-code", "--yes") == 0
    assert skill.read_text().startswith("---")
    assert _run(home, project, "--client", "claude-code", "--yes", "--uninstall") == 0
    assert skill.read_text() == "my own notes"


def test_setup_codex_refuses_a_table_the_user_already_wrote(env):
    home, project = env
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    original = '[mcp_servers.nexus-mcp]\ncommand = "mine"\n'
    cfg.write_text(original)
    assert _run(home, project, "--client", "codex", "--yes", "--no-skill") == 1
    assert cfg.read_text() == original


def test_setup_refuses_a_block_with_only_one_marker(env):
    home, project = env
    gemini_md = project / "GEMINI.md"
    original = f"notes\n{sc.BEGIN}\nhalf a block\n"
    gemini_md.write_text(original)
    assert _run(home, project, "--client", "gemini", "--yes") == 1
    assert gemini_md.read_text() == original


def test_setup_claude_code_second_run_does_not_call_the_cli_again(env, monkeypatch):
    # Requirement changed on 2026-11-08 (2.4.4): an entry with another launch is now replaced,
    # so `setup` can update an older install. Only a current entry skips the CLI.
    home, project = env
    current = {"type": "stdio", **sc.launch_spec(), "env": {}}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {sc.SERVER_NAME: current}}))
    calls = []
    monkeypatch.setattr(
        sc.shutil, "which", lambda name, *a, **k: "/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(sc.Path, "home", classmethod(lambda cls: home))
    argv = ["--project", str(project), "--no-index", "--client", "claude-code", "--yes"]
    assert sc.run_setup(argv) == 0
    assert calls == []


def test_setup_indexes_only_a_folder_that_looks_like_a_project(env, monkeypatch, capsys):
    home, project = env
    started = []
    monkeypatch.setattr(sc, "_start_index", lambda path: started.append(path))
    argv = ["--home", str(home), "--project", str(project), "--client", "cursor", "--yes"]
    assert sc.run_setup(argv) == 0
    assert started == []
    assert "does not look like a project folder" in capsys.readouterr().out
    (project / ".git").mkdir()
    assert sc.run_setup(argv) == 0
    assert started == [project.resolve()]
    assert "130 MB" in capsys.readouterr().out
    # The home folder is never indexed, even if it holds a marker.
    (home / ".git").mkdir()
    started.clear()
    argv_home = ["--home", str(home), "--project", str(home), "--client", "cursor", "--yes"]
    assert sc.run_setup(argv_home) == 0
    assert started == []


def test_setup_asks_for_a_star_only_after_a_successful_install(env, capsys):
    home, project = env
    assert _run(home, project, "--client", "cursor", "--dry-run") == 0
    assert sc.STAR_LINE not in capsys.readouterr().out
    assert _run(home, project, "--client", "cursor", "--yes") == 0
    assert sc.STAR_LINE in capsys.readouterr().out
    assert _run(home, project, "--client", "cursor", "--yes", "--uninstall") == 0
    assert sc.STAR_LINE not in capsys.readouterr().out


def test_setup_cline_writes_a_stdio_entry(env, monkeypatch):
    home, project = env
    monkeypatch.setattr(sc, "_cline_config", lambda h: h / "cline" / "cline_mcp_settings.json")
    (home / "cline").mkdir()
    assert _run(home, project, "--client", "cline", "--yes") == 0
    entry = json.loads((home / "cline" / "cline_mcp_settings.json").read_text())["mcpServers"][
        "nexus-mcp"
    ]
    assert entry["type"] == "stdio"
    assert entry["command"] == "uvx"


def _cline(home, monkeypatch):
    monkeypatch.setattr(sc, "_cline_config", lambda h: h / "cline" / "cline_mcp_settings.json")
    (home / "cline").mkdir()
    return home / "cline" / "cline_mcp_settings.json"


def test_setup_replaces_an_older_entry_and_says_replace(env, monkeypatch, capsys):
    # Regression (Cline report, 2.4.3): `setup` replaced 2.4.2 but printed "add MCP server".
    home, project = env
    cfg = _cline(home, monkeypatch)
    old = {"command": "uvx", "args": ["--python", "3.12", "nexus-mcp-ci==2.4.2"]}
    cfg.write_text(json.dumps({"mcpServers": {"nexus-mcp": old, "other": {"command": "x"}}}))
    assert _run(home, project, "--client", "cline", "--yes") == 0
    out = capsys.readouterr().out
    assert "replace MCP server 'nexus-mcp'" in out
    assert "(was `uvx --python 3.12 nexus-mcp-ci==2.4.2`)" in out
    assert "add MCP server" not in out
    servers = json.loads(cfg.read_text())["mcpServers"]
    assert servers["nexus-mcp"]["args"] == sc.launch_spec()["args"]
    assert servers["other"] == {"command": "x"}


def test_setup_says_keep_for_a_current_entry(env, monkeypatch, capsys):
    home, project = env
    _cline(home, monkeypatch)
    assert _run(home, project, "--client", "cline", "--yes") == 0
    assert "add MCP server 'nexus-mcp'" in capsys.readouterr().out
    assert _run(home, project, "--client", "cline", "--yes") == 0
    assert "keep MCP server 'nexus-mcp'" in capsys.readouterr().out


def test_setup_claude_code_cli_replaces_an_older_entry(env, monkeypatch, capsys):
    # Regression: a second `setup` with the `claude` CLI kept the old pinned version.
    home, project = env
    old = {"type": "stdio", "command": "uvx", "args": ["nexus-mcp-ci==2.4.2"], "env": {}}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {sc.SERVER_NAME: old}}))
    calls = []
    monkeypatch.setattr(
        sc.shutil, "which", lambda name, *a, **k: "/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(sc.Path, "home", classmethod(lambda cls: home))
    argv = ["--project", str(project), "--no-index", "--client", "claude-code", "--yes"]
    assert sc.run_setup(argv) == 0
    assert [c[2] for c in calls] == ["remove", "add"]
    assert "(replaces `uvx nexus-mcp-ci==2.4.2`)" in capsys.readouterr().out


def test_start_index_writes_a_log_file(tmp_path, monkeypatch):
    started = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            started.update(kwargs, cmd=cmd)

    monkeypatch.setattr(sc.subprocess, "Popen", FakePopen)
    log = sc._start_index(tmp_path)
    assert log == tmp_path / ".nexus" / "setup-index.log"
    assert log.exists()
    assert started["cwd"] == tmp_path
    assert started["stdout"].name == str(log)
    assert started["stderr"] is sc.subprocess.STDOUT
    assert "DONE" in started["cmd"][-1] and "FAILED" in started["cmd"][-1]


def test_setup_prints_the_index_log_path(env, monkeypatch, capsys):
    home, project = env
    (project / ".git").mkdir()
    log = project / ".nexus" / "setup-index.log"
    monkeypatch.setattr(sc, "_start_index", lambda path: log)
    argv = ["--home", str(home), "--project", str(project), "--client", "cursor", "--yes"]
    assert sc.run_setup(argv) == 0
    assert f"The last line of {log} says DONE or FAILED" in capsys.readouterr().out


# --- Cline CLI file (`~/.cline/data/settings`), found in the 2.4.4 install test ------------------


def _cli_file(home):
    path = sc._cline_cli_config(home)
    path.parent.mkdir(parents=True)
    return path


def test_setup_cline_writes_the_cli_file_when_that_install_exists(env):
    # Regression (Cline report, 2.4.3 and 2.4.4): `setup` wrote only the VS Code file, and the
    # Cline that Antigravity runs read `~/.cline/data/settings`. Both tests failed silently.
    home, project = env
    cli = _cli_file(home)
    assert _run(home, project, "--client", "cline", "--yes") == 0
    entry = json.loads(cli.read_text())["mcpServers"]["nexus-mcp"]
    assert entry["transport"] == {"type": "stdio", **sc.launch_spec()}
    assert entry["disabled"] is False
    assert entry["timeout"] == 300
    assert "command" not in entry  # this file nests the launch under `transport`
    assert not sc._cline_config(home).exists()  # no VS Code folder: nothing is made there


def test_setup_cline_writes_both_files_when_both_installs_exist(env):
    home, project = env
    cli = _cli_file(home)
    vscode = sc._cline_config(home)
    vscode.parent.mkdir(parents=True)
    assert _run(home, project, "--client", "cline", "--yes") == 0
    assert "transport" in json.loads(cli.read_text())["mcpServers"]["nexus-mcp"]
    flat = json.loads(vscode.read_text())["mcpServers"]["nexus-mcp"]
    assert flat["type"] == "stdio" and flat["command"] == "uvx"


def test_setup_cline_cli_file_replaces_an_old_entry_and_keeps_user_keys(env, capsys):
    home, project = env
    cli = _cli_file(home)
    http = {"transport": {"type": "streamableHttp", "url": "https://example.test/mcp"}}
    old = {
        "transport": {
            "type": "stdio",
            "command": "/dev/nexus-mcp/.venv/bin/nexus-mcp-ci",
            "args": [],
        },
        "disabled": True,
        "timeout": 120,
        "autoApprove": ["search"],
    }
    cli.write_text(json.dumps({"mcpServers": {"context7": http, sc.SERVER_NAME: old}}))
    assert _run(home, project, "--client", "cline", "--yes") == 0
    out = capsys.readouterr().out
    assert "replace MCP server 'nexus-mcp'" in out
    assert "(was `/dev/nexus-mcp/.venv/bin/nexus-mcp-ci`)" in out
    servers = json.loads(cli.read_text())["mcpServers"]
    assert servers["context7"] == http
    entry = servers[sc.SERVER_NAME]
    assert entry["transport"] == {"type": "stdio", **sc.launch_spec()}
    assert entry["disabled"] is False  # an install turns it on
    assert entry["timeout"] == 120 and entry["autoApprove"] == ["search"]


def test_setup_cline_cli_file_says_keep_for_a_current_entry(env, capsys):
    home, project = env
    _cli_file(home)
    assert _run(home, project, "--client", "cline", "--yes") == 0
    capsys.readouterr()
    assert _run(home, project, "--client", "cline", "--yes") == 0
    assert "keep MCP server 'nexus-mcp'" in capsys.readouterr().out


def test_setup_cline_uninstall_removes_the_cli_entry(env):
    home, project = env
    cli = _cli_file(home)
    assert _run(home, project, "--client", "cline", "--yes") == 0
    assert _run(home, project, "--client", "cline", "--uninstall", "--yes") == 0
    assert "nexus-mcp" not in json.loads(cli.read_text())["mcpServers"]


def test_detect_clients_finds_cline_by_the_cli_folder(tmp_path):
    assert "cline" not in sc.detect_clients(tmp_path)
    sc._cline_cli_config(tmp_path).parent.mkdir(parents=True)
    assert "cline" in sc.detect_clients(tmp_path)


def test_setup_cline_cli_file_says_update_for_the_same_launch_in_another_shape(env, capsys):
    # The Cline agent wrote a flat entry by hand: the launch is current, the shape is not.
    home, project = env
    cli = _cli_file(home)
    flat = {"type": "stdio", **sc.launch_spec(), "disabled": False, "timeout": 300}
    cli.write_text(json.dumps({"mcpServers": {sc.SERVER_NAME: flat}}))
    assert _run(home, project, "--client", "cline", "--yes") == 0
    out = capsys.readouterr().out
    assert "update MCP server 'nexus-mcp'" in out and "keep MCP server" not in out
    entry = json.loads(cli.read_text())["mcpServers"][sc.SERVER_NAME]
    assert entry["transport"] == {"type": "stdio", **sc.launch_spec()}
    assert "command" not in entry
