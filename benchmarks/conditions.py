"""Pure builders for benchmark run conditions (argv + env for the `claude` CLI).

No subprocess execution here — runner.py does that. Keeping this pure makes
argv/env construction testable without spawning a real CLI process.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md"
NEXUS_MCP_CONFIG = Path(__file__).resolve().parent / "mcp-configs" / "nexus.json"
PLUGIN_DIR = REPO_ROOT / "plugin"

BASELINE_TOOLS = "Read,Grep,Glob"
DISALLOWED_TOOLS = "Edit,Write,NotebookEdit,WebFetch,WebSearch,Task"

KNOWN_CONDITIONS = ("baseline", "nexus", "nexus-plugin")


def strip_frontmatter(skill_text: str) -> str:
    """Strip a leading YAML frontmatter block (delimited by `---` lines).

    Returns the body unchanged if no frontmatter is present.
    """
    lines = skill_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return skill_text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return skill_text


def load_skill_body(skill_path: Path = SKILL_PATH) -> str:
    """Load the nexus-mcp SKILL.md body with frontmatter stripped."""
    return strip_frontmatter(skill_path.read_text())


def _common_args(
    model: str,
    max_budget_usd: float,
) -> List[str]:
    return [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_budget_usd),
        "--strict-mcp-config",
        "--disallowedTools",
        DISALLOWED_TOOLS,
    ]


def build_argv(
    condition: str,
    prompt: str,
    model: str,
    max_budget_usd: float,
    mcp_config_path: Path = NEXUS_MCP_CONFIG,
    skill_path: Path = SKILL_PATH,
    plugin_dir: Path = PLUGIN_DIR,
) -> List[str]:
    """Build the full argv for a `claude` invocation under the given condition.

    `condition` is one of KNOWN_CONDITIONS. Raises ValueError otherwise.
    """
    if condition not in KNOWN_CONDITIONS:
        raise ValueError(f"Unknown condition: {condition!r}, expected one of {KNOWN_CONDITIONS}")

    argv = _common_args(model, max_budget_usd)
    argv += ["--tools", BASELINE_TOOLS]

    if condition == "nexus":
        skill_body = load_skill_body(skill_path)
        argv += [
            "--mcp-config",
            str(mcp_config_path),
            "--append-system-prompt",
            skill_body,
        ]
    elif condition == "nexus-plugin":
        argv += ["--plugin-dir", str(plugin_dir)]

    argv += [prompt]
    return argv


def build_env(
    config_dir: Path,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build the isolated environment for a benchmark run.

    Uses `--bare`-compatible isolation when ANTHROPIC_API_KEY is present in
    base_env (or the real environment); otherwise callers must additionally
    pass `--strict-mcp-config --setting-sources ""` and accept the reduced
    isolation (real ~/.claude settings/hooks may still apply).
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def has_api_key(env: Optional[Dict[str, str]] = None) -> bool:
    """Whether ANTHROPIC_API_KEY is available for full (--bare) isolation."""
    source = env if env is not None else os.environ
    return bool(source.get("ANTHROPIC_API_KEY"))


def apply_bare_isolation(argv: List[str], env: Dict[str, str]) -> List[str]:
    """Insert `--bare` right after the subcommand for full config isolation.

    Only valid when ANTHROPIC_API_KEY is set in env; callers should check
    has_api_key() first.
    """
    if not has_api_key(env):
        raise ValueError("--bare requires ANTHROPIC_API_KEY to be set")
    argv = list(argv)
    argv.insert(2, "--bare")  # after ["claude", "-p"]
    return argv


def apply_reduced_isolation(argv: List[str]) -> List[str]:
    """Fallback isolation when no API key is available: settings sources off.

    --strict-mcp-config is already in _common_args. This adds
    --setting-sources "" so project/user settings files are not loaded.
    Real ~/.claude hooks/plugins loaded outside settings files are NOT
    covered by this fallback — callers must record the isolation mode used.
    """
    argv = list(argv)
    argv += ["--setting-sources", ""]
    return argv


def build_run(
    condition: str,
    prompt: str,
    model: str,
    max_budget_usd: float,
    config_dir: Path,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the full (argv, env, isolation_mode) triple for one run."""
    argv = build_argv(condition, prompt, model, max_budget_usd)
    run_env = build_env(config_dir, env)

    if has_api_key(run_env):
        argv = apply_bare_isolation(argv, run_env)
        isolation_mode = "bare"
    else:
        argv = apply_reduced_isolation(argv)
        isolation_mode = "reduced"

    return {"argv": argv, "env": run_env, "isolation_mode": isolation_mode}
