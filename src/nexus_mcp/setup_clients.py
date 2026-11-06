"""``nexus-mcp-ci setup``: add the Nexus-MCP server and its skill to an MCP client.

One command, for a person or for an agent. It writes only two kinds of file:

- the MCP server entry of a client, and
- the skill text (a skill file, a rules file, or a marked block in ``AGENTS.md`` /
  ``GEMINI.md``).

Every change is shown first. Without ``--yes`` the command asks before each client.
``--dry-run`` writes nothing. ``--uninstall`` removes what ``setup`` wrote.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

SERVER_NAME = "nexus-mcp"
PACKAGE = "nexus-mcp-ci"
BEGIN = "<!-- nexus-mcp:begin -->"
END = "<!-- nexus-mcp:end -->"
TOML_BEGIN = "# nexus-mcp:begin"
TOML_END = "# nexus-mcp:end"
RELOAD_LINE = "Reload your client (or start a new session) to see the nexus tools."

# Each client: where its MCP config lives and how its skill text is installed.
# `skill` is one of: "skill-dir" (a SKILL.md folder), "rules-mdc" (Cursor rules file),
# "block:<FILE>" (a marked block in a project file), or "" (no skill support).
CLIENTS = (
    "claude-code",
    "claude-desktop",
    "cursor",
    "vscode",
    "windsurf",
    "gemini",
    "codex",
    "cline",
)


class SetupError(Exception):
    """A change that cannot be made safely (for example, a config file that is not valid JSON)."""


@dataclass
class Change:
    """One planned file change."""

    client: str
    path: Path
    what: str
    apply: Callable[[], None]
    undo: Callable[[], None]
    present: Callable[[], bool]


@dataclass
class Plan:
    client: str
    changes: List[Change] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def package_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version(PACKAGE)
    except Exception:
        return None


def launch_spec(pin: bool = True) -> Dict[str, object]:
    """The `uvx` launch for the server. Pinned to the installed version when it is known."""
    ver = package_version() if pin else None
    return {"command": "uvx", "args": [f"{PACKAGE}=={ver}" if ver else PACKAGE]}


def skill_text() -> str:
    """The full skill file (with its front matter), from the wheel or from a checkout."""
    here = Path(__file__).resolve()
    for path in (
        here.parent / "evaluate" / "data" / "SKILL.md",
        here.parents[2] / "plugin" / "skills" / "nexus-mcp" / "SKILL.md",
    ):
        if path.is_file():
            return path.read_text()
    raise SetupError("The skill file was not found in this install.")


def _body(text: str) -> str:
    """The skill text for an always-loaded file: no front matter, no install section.

    The install section is for a client that has no nexus tools. A rules file or an
    ``AGENTS.md`` block exists only after setup, and it is read in every session.
    """
    text = text.split("<!-- nexus-install -->")[0].rstrip() + "\n"
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


# --- file helpers -----------------------------------------------------------------


def _backup(path: Path) -> None:
    """Keep one copy of a file before the first change (`<name>.nexus-bak`)."""
    bak = path.with_name(path.name + ".nexus-bak")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".nexus-tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SetupError(f"{path} is not valid JSON ({exc.msg}). Fix it or remove it, then retry.")
    if not isinstance(data, dict):
        raise SetupError(f"{path} does not hold a JSON object.")
    return data


def _json_entry(path: Path, key: str, entry: dict) -> Change:
    """Add `entry` under `key` ("mcpServers" or "servers") and keep every other server."""

    def apply() -> None:
        data = _read_json(path)
        servers = data.setdefault(key, {})
        if not isinstance(servers, dict):
            raise SetupError(f"{path}: '{key}' is not an object.")
        servers[SERVER_NAME] = entry
        _backup(path)
        _write_atomic(path, json.dumps(data, indent=2) + "\n")

    def undo() -> None:
        if not path.exists():
            return
        data = _read_json(path)
        servers = data.get(key)
        if isinstance(servers, dict) and SERVER_NAME in servers:
            del servers[SERVER_NAME]
            _write_atomic(path, json.dumps(data, indent=2) + "\n")

    def present() -> bool:
        try:
            return SERVER_NAME in (_read_json(path).get(key) or {})
        except SetupError:
            return False

    return Change("", path, f"add MCP server '{SERVER_NAME}' to {path}", apply, undo, present)


def _marked_block(path: Path, block: str, begin: str, end: str, what: str) -> Change:
    """Write `block` between markers in a text file. Text outside the markers is not touched."""

    def strip(text: str) -> str:
        if begin in text and end in text:
            head, rest = text.split(begin, 1)
            _, tail = rest.split(end, 1)
            return (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n")
        return text

    def apply() -> None:
        existing = path.read_text() if path.exists() else ""
        base = strip(existing)
        sep = "\n\n" if base else ""
        _backup(path)
        _write_atomic(path, f"{base}{sep}{begin}\n{block.rstrip()}\n{end}\n")

    def undo() -> None:
        if not path.exists():
            return
        text = path.read_text()
        if begin not in text:
            return
        rest = strip(text)
        if rest.strip():
            _write_atomic(path, rest + "\n")
        else:
            path.unlink()

    def present() -> bool:
        return path.exists() and begin in path.read_text()

    return Change("", path, what, apply, undo, present)


def _own_file(path: Path, text: str, what: str) -> Change:
    """A file that setup owns completely (a skill file or a rules file)."""

    def apply() -> None:
        _write_atomic(path, text)

    def undo() -> None:
        if path.exists():
            path.unlink()
            try:
                path.parent.rmdir()  # only when empty
            except OSError:
                pass

    return Change("", path, what, apply, undo, path.exists)


# --- per-client plans -------------------------------------------------------------


def _desktop_config(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return (
            Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
            / "Claude"
            / ("claude_desktop_config.json")
        )
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _cline_config(home: Path) -> Path:
    tail = Path("globalStorage") / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Code" / "User" / tail
    if sys.platform.startswith("win"):
        return (
            Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Code" / "User" / tail
        )
    return home / ".config" / "Code" / "User" / tail


def _toml_block(spec: Dict[str, object]) -> str:
    args = ", ".join(json.dumps(a) for a in spec["args"])  # type: ignore[union-attr]
    return f"[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(spec['command'])}\nargs = [{args}]"


def _claude_code_change(spec: Dict[str, object], home: Path) -> Change:
    """Use the `claude` CLI when it exists. Otherwise edit ~/.claude.json.

    The CLI always writes to the real home. When ``home`` is another folder (the hidden
    ``--home`` flag, used by tests and dry runs) the CLI would change the real config, so the
    file path is used.
    """
    cfg = home / ".claude.json"
    fallback = _json_entry(cfg, "mcpServers", {"type": "stdio", **spec, "env": {}})
    claude = shutil.which("claude")
    if not claude or home.resolve() != Path.home().resolve():
        return fallback

    def apply() -> None:
        cmd = [
            claude,
            "mcp",
            "add",
            "--scope",
            "user",
            SERVER_NAME,
            "--",
            spec["command"],
            *spec["args"],
        ]  # type: ignore[misc]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)

    def undo() -> None:
        subprocess.run(
            [claude, "mcp", "remove", "--scope", "user", SERVER_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    launch = " ".join([str(spec["command"]), *map(str, spec["args"])])  # type: ignore[union-attr]
    return Change(
        "",
        cfg,
        f"run: claude mcp add --scope user {SERVER_NAME} -- {launch}",
        apply,
        undo,
        fallback.present,
    )


def build_plan(client: str, project: Path, home: Path, with_skill: bool = True) -> Plan:
    """Plan the changes for one client. Nothing is written here."""
    spec = launch_spec()
    plan = Plan(client)
    changes: List[Change] = []
    skill = skill_text() if with_skill else ""

    if client == "claude-code":
        changes.append(_claude_code_change(spec, home))
        if with_skill:
            changes.append(
                _own_file(
                    home / ".claude" / "skills" / "nexus-mcp" / "SKILL.md",
                    skill,
                    "install the skill",
                )
            )
    elif client == "claude-desktop":
        changes.append(_json_entry(_desktop_config(home), "mcpServers", dict(spec)))
        plan.notes.append(
            "Claude Desktop has no skill folder; the server instructions carry the routing."
        )
    elif client == "cursor":
        changes.append(_json_entry(home / ".cursor" / "mcp.json", "mcpServers", dict(spec)))
        if with_skill:
            mdc = (
                "---\ndescription: Use the nexus-mcp tools for code questions\n"
                "alwaysApply: false\n---\n\n" + _body(skill)
            )
            changes.append(
                _own_file(
                    project / ".cursor" / "rules" / "nexus-mcp.mdc", mdc, "install the rules file"
                )
            )
    elif client == "vscode":
        changes.append(
            _json_entry(project / ".vscode" / "mcp.json", "servers", {"type": "stdio", **spec})
        )
    elif client == "windsurf":
        changes.append(
            _json_entry(
                home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers", dict(spec)
            )
        )
    elif client == "gemini":
        changes.append(_json_entry(home / ".gemini" / "settings.json", "mcpServers", dict(spec)))
        if with_skill:
            changes.append(
                _marked_block(
                    project / "GEMINI.md",
                    _body(skill),
                    BEGIN,
                    END,
                    f"add a marked nexus-mcp block to {project / 'GEMINI.md'}",
                )
            )
    elif client == "codex":
        changes.append(
            _marked_block(
                home / ".codex" / "config.toml",
                _toml_block(spec),
                TOML_BEGIN,
                TOML_END,
                f"add [mcp_servers.{SERVER_NAME}] to {home / '.codex' / 'config.toml'}",
            )
        )
        if with_skill:
            changes.append(
                _marked_block(
                    project / "AGENTS.md",
                    _body(skill),
                    BEGIN,
                    END,
                    f"add a marked nexus-mcp block to {project / 'AGENTS.md'}",
                )
            )
    elif client == "cline":
        changes.append(_json_entry(_cline_config(home), "mcpServers", dict(spec)))
    else:
        raise SetupError(f"Unknown client '{client}'. Choose from: {', '.join(CLIENTS)}.")

    for change in changes:
        change.client = client
    plan.changes = changes
    return plan


def detect_clients(home: Path) -> List[str]:
    """Clients that look installed on this machine (a config folder or a CLI exists)."""
    found = []
    probes = {
        "claude-code": lambda: shutil.which("claude") or (home / ".claude").is_dir(),
        "claude-desktop": lambda: _desktop_config(home).parent.is_dir(),
        "cursor": lambda: (home / ".cursor").is_dir(),
        "windsurf": lambda: (home / ".codeium" / "windsurf").is_dir(),
        "gemini": lambda: shutil.which("gemini") or (home / ".gemini").is_dir(),
        "codex": lambda: shutil.which("codex") or (home / ".codex").is_dir(),
        "cline": lambda: _cline_config(home).parent.is_dir(),
    }
    for name, probe in probes.items():
        try:
            if probe():
                found.append(name)
        except OSError:
            continue
    return found


# --- command ----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci setup",
        description=(
            "Add the Nexus-MCP server and its skill to your MCP client. "
            "Shows each change first. Writes nothing until you say yes (or pass --yes)."
        ),
    )
    p.add_argument(
        "--client",
        action="append",
        choices=CLIENTS,
        help="Client to set up (repeat for more). Default: every client found on this machine",
    )
    p.add_argument("--yes", action="store_true", help="Do not ask; make the shown changes")
    p.add_argument("--dry-run", action="store_true", help="Show the changes and write nothing")
    p.add_argument("--uninstall", action="store_true", help="Remove what setup wrote")
    p.add_argument(
        "--no-skill", action="store_true", help="Add the server only, not the skill text"
    )
    p.add_argument("--no-index", action="store_true", help="Do not start an index of this folder")
    p.add_argument(
        "--project", default=".", help="Project folder for project-level files (default: .)"
    )
    p.add_argument("--home", default=None, help=argparse.SUPPRESS)
    return p


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _start_index(project: Path) -> None:
    code = "import asyncio; from nexus_mcp import core_api; asyncio.run(core_api.index('.'))"
    subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=project,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_setup(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    home = Path(args.home).expanduser() if args.home else Path.home()
    project = Path(args.project).expanduser().resolve()

    clients = args.client or detect_clients(home)
    if not clients:
        print(
            "No MCP client found on this machine. Name one: "
            f"nexus-mcp-ci setup --client <{'|'.join(CLIENTS)}>",
            file=sys.stderr,
        )
        return 2

    try:
        plans = [build_plan(c, project, home, with_skill=not args.no_skill) for c in clients]
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    verb = "Remove" if args.uninstall else "Change"
    failed = 0
    changed = 0
    for plan in plans:
        print(f"\n{plan.client}")
        for change in plan.changes:
            print(f"  {verb.lower()}: {change.what}")
        for note in plan.notes:
            print(f"  note: {note}")
        if args.dry_run:
            continue
        if not args.yes and not _confirm(f"{verb} {plan.client}?"):
            print("  skipped (pass --yes to make the changes without asking)")
            continue
        for change in plan.changes:
            try:
                (change.undo if args.uninstall else change.apply)()
                changed += 1
            except (SetupError, OSError, subprocess.SubprocessError) as exc:
                detail = getattr(exc, "stderr", None) or exc
                print(f"  error: {change.path}: {str(detail).strip()}", file=sys.stderr)
                failed += 1

    if args.dry_run:
        print("\nDry run. Nothing was written.")
        return 0
    if failed:
        return 1
    if changed and not args.uninstall:
        if not args.no_index and args.yes:
            _start_index(project)
            print(f"\nIndexing {project} in the background.")
        print(f"\n{RELOAD_LINE}")
    elif changed:
        print("\nRemoved. Reload your client.")
    return 0
