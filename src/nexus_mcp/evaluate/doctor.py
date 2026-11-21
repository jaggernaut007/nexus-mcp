"""``nexus-mcp-ci eval doctor``: check what the eval needs before you spend time or money."""

import shutil
import subprocess
from typing import Callable, List, Tuple

from nexus_mcp.evaluate import paths

Check = Tuple[str, bool, str]  # name, ok, detail


def _version(cmd: str, run=subprocess.run) -> str:
    try:
        proc = run([cmd, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (
        (proc.stdout or proc.stderr or "").strip().splitlines()[0] if proc.returncode == 0 else ""
    )


def checks(which: Callable[[str], str] = shutil.which, run=subprocess.run) -> List[Check]:
    out: List[Check] = []
    rg = which("rg")
    out.append(("ripgrep (Grep baseline)", True, rg or "not found: the baseline uses grep"))
    model = paths.model_dir()
    out.append(
        (
            "embedding model cached",
            model.exists(),
            str(model) if model.exists() else "not cached: the first run downloads it (~32MB)",
        )
    )
    for cli, use in (
        ("claude", "--agent claude"),
        ("codex", "--agent codex (experimental)"),
        ("agy", "--agent agy (experimental)"),
    ):
        found = which(cli)
        version = _version(cli, run) if found else ""
        out.append((f"{cli} CLI", bool(found), version or f"not found: needed for {use}"))
    home = paths.eval_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        writable = True
    except OSError:
        writable = False
    out.append(("results folder writable", writable, str(home)))
    return out


def render(items: List[Check]) -> str:
    lines = ["Nexus-MCP eval doctor", ""]
    for name, ok, detail in items:
        lines.append(f"[{'ok' if ok else '--'}] {name}: {detail}")
    lines += [
        "",
        "The quick tier needs only Python and this package. The agent tier needs a logged-in",
        "claude, codex or agy CLI, and it sends prompts and code to that provider.",
    ]
    return "\n".join(lines)
