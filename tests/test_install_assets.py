"""The install-channel files: manifests, pins, links and the bump script."""

import importlib.util
import json
import re
import shutil
import urllib.parse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"index", "status", "search", "find_symbol", "graph", "analyze", "map", "memory"}


def _version() -> str:
    return re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcpb_manifest_declares_a_uv_server_with_an_existing_entry_point():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    assert manifest["manifest_version"] == "0.4"
    assert manifest["server"]["type"] == "uv"
    assert (ROOT / "mcpb" / manifest["server"]["entry_point"]).is_file()
    assert (ROOT / "mcpb" / "pyproject.toml").is_file()


def test_mcpb_manifest_lists_exactly_the_server_tools():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    assert {tool["name"] for tool in manifest["tools"]} == TOOLS


def test_mcpb_licence_key_is_optional_and_sensitive():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    field = manifest["user_config"]["license_key"]
    assert field["sensitive"] is True
    assert field["required"] is False
    env = manifest["server"]["mcp_config"]["env"]
    assert env["NEXUS_LICENSE_KEY"] == "${user_config.license_key}"


def test_mcpb_entry_point_starts_the_server():
    text = (ROOT / "mcpb" / "src" / "server.py").read_text()
    assert "from nexus_mcp.server import main" in text
    assert "main()" in text


def test_every_channel_pins_the_project_version():
    want = _version()
    gemini = json.loads((ROOT / "gemini-extension.json").read_text())
    assert gemini["mcpServers"]["nexus-mcp"]["args"] == [f"nexus-mcp-ci=={want}"]
    plugin = json.loads((ROOT / "plugin" / ".mcp.json").read_text())
    assert plugin["mcpServers"]["nexus-mcp"]["command"] == "uvx"
    assert plugin["mcpServers"]["nexus-mcp"]["args"] == [f"nexus-mcp-ci=={want}"]
    assert f'"nexus-mcp-ci=={want}"' in (ROOT / "mcpb" / "pyproject.toml").read_text()


def test_gemini_context_file_is_the_skill_without_the_install_section():
    skill = (ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md").read_text()
    skill = skill.split("<!-- nexus-install -->")[0].rstrip() + "\n"
    body = skill[skill.find("\n---", 3) + 4 :].lstrip("\n")
    gemini = json.loads((ROOT / "gemini-extension.json").read_text())
    context = (ROOT / gemini["contextFileName"]).read_text()
    assert context == body
    assert "If the nexus tools are missing" not in context


def test_vscode_link_in_the_readme_decodes_to_a_uvx_launch():
    readme = (ROOT / "README.md").read_text()
    match = re.search(
        r"https://vscode\.dev/redirect/mcp/install\?name=nexus-mcp&config=(\S+?)\)", readme
    )
    assert match
    config = json.loads(urllib.parse.unquote(match.group(1)))
    assert config == {"type": "stdio", "command": "uvx", "args": ["nexus-mcp-ci"]}


def test_cursor_link_on_the_site_decodes_to_a_uvx_launch():
    import base64

    site = (ROOT / "site" / "index.html").read_text()
    match = re.search(
        r"cursor://anysphere\.cursor-deeplink/mcp/install\?name=nexus-mcp&amp;config=([^\"]+)", site
    )
    assert match
    config = json.loads(base64.b64decode(urllib.parse.unquote(match.group(1))))
    assert config == {"command": "uvx", "args": ["nexus-mcp-ci"]}


def test_action_is_a_composite_action_with_the_documented_inputs():
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {"args", "version", "python-version", "license-key"}
    assert action["inputs"]["license-key"]["default"] == ""


def test_action_does_not_put_inputs_into_the_shell_script_text():
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"]


def test_stdio_dockerfile_runs_as_a_normal_user_without_the_proxy():
    text = (ROOT / "docker" / "Dockerfile.stdio").read_text()
    assert re.search(r"^USER nexus$", text, re.M)
    assert 'ENTRYPOINT ["nexus-mcp-ci"]' in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "mcp-proxy" not in code


def test_publish_workflow_builds_the_bundle_after_pypi_and_pushes_the_image():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "publish.yml").read_text())
    assert wf["jobs"]["mcpb"]["needs"] == "publish"
    assert wf["jobs"]["mcpb"]["permissions"] == {"contents": "write"}
    assert wf["jobs"]["image"]["permissions"]["packages"] == "write"


def test_smithery_yaml_is_gone_because_the_mcpb_replaced_it():
    assert not (ROOT / "smithery.yaml").exists()


def test_bump_version_changes_every_pinned_file(tmp_path, monkeypatch):
    bump = _script("bump_version")
    for rel in (
        "pyproject.toml",
        "CITATION.cff",
        "server.json",
        "plugin/.claude-plugin/plugin.json",
        "plugin/.mcp.json",
        "mcpb/manifest.json",
        "mcpb/pyproject.toml",
        "gemini-extension.json",
    ):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    monkeypatch.setattr(bump, "ROOT", tmp_path)
    bump.bump("9.8.7")

    assert 'version = "9.8.7"' in (tmp_path / "pyproject.toml").read_text()
    assert "version: 9.8.7" in (tmp_path / "CITATION.cff").read_text()
    server = json.loads((tmp_path / "server.json").read_text())
    assert {server["version"], *(p["version"] for p in server["packages"])} == {"9.8.7"}
    for rel in ("plugin/.claude-plugin/plugin.json", "mcpb/manifest.json", "gemini-extension.json"):
        assert json.loads((tmp_path / rel).read_text())["version"] == "9.8.7"
    for rel in ("plugin/.mcp.json", "gemini-extension.json"):
        args = json.loads((tmp_path / rel).read_text())["mcpServers"]["nexus-mcp"]["args"]
        assert args == ["nexus-mcp-ci==9.8.7"]
    assert 'nexus-mcp-ci==9.8.7"' in (tmp_path / "mcpb" / "pyproject.toml").read_text()


def test_bump_version_rejects_a_bad_version(tmp_path, monkeypatch):
    bump = _script("bump_version")
    try:
        bump.bump("2.3")
    except SystemExit as exc:
        assert "not a version" in str(exc)
    else:
        raise AssertionError("a bad version was accepted")


def test_check_docs_flags_a_channel_pin_that_drifted(tmp_path, monkeypatch):
    check = _script("check_docs")
    assert check.check_versions() == []
    shutil.copytree(ROOT / "mcpb", tmp_path / "mcpb")
    (tmp_path / "gemini-extension.json").write_text(
        (ROOT / "gemini-extension.json").read_text().replace(_version(), "0.0.1")
    )
    for rel in ("pyproject.toml", "CITATION.cff", "server.json"):
        shutil.copy(ROOT / rel, tmp_path / rel)
    (tmp_path / "plugin" / ".claude-plugin").mkdir(parents=True)
    shutil.copy(
        ROOT / "plugin" / ".claude-plugin" / "plugin.json",
        tmp_path / "plugin" / ".claude-plugin" / "plugin.json",
    )
    shutil.copy(ROOT / "plugin" / ".mcp.json", tmp_path / "plugin" / ".mcp.json")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    errors = check.check_versions()
    assert any("gemini-extension.json" in e for e in errors)
