"""Pure builders for benchmark run conditions (argv + env for the `claude` CLI).

No subprocess execution here — runner.py does that. Keeping this pure makes
argv/env construction testable without spawning a real CLI process.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "plugin" / "skills" / "nexus-mcp" / "SKILL.md"
NEXUS_MCP_CONFIG = Path(__file__).resolve().parent / "mcp-configs" / "nexus.json"
PLUGIN_DIR = REPO_ROOT / "plugin"

BASELINE_TOOLS = "Read,Grep,Glob"
DISALLOWED_TOOLS = "Edit,Write,NotebookEdit,WebFetch,WebSearch,Task"
# Headless runs use `dontAsk` plus this allowlist instead of a bypass flag: a
# tool outside the list is denied (and recorded) rather than silently allowed.
ALLOWED_TOOLS = "Read,Grep,Glob,ToolSearch,mcp__nexus-mcp__*,mcp__plugin_nexus-mcp_nexus-mcp__*"

# `nexus` appends the routing skill to the system prompt; `mcp-only` exposes the
# same server with no skill, so tool descriptions and server instructions are
# the only routing signal.
KNOWN_CONDITIONS = ("baseline", "mcp-only", "nexus", "nexus-plugin")
# These two have no server of ours to configure, so `<name>@<model>` is an error.
NO_MODEL_CONDITIONS = ("baseline", "nexus-plugin")


def split_condition(name: str) -> Tuple[str, Optional[str]]:
    """Split `nexus@granite` into ("nexus", "granite"). A plain name has no model."""
    base, _, model = name.partition("@")
    return base, (model or None)


def expand_conditions(names: List[str], embedding_models: List[str]) -> List[str]:
    """Add the embedding model to each nexus condition: one `<name>@<model>` per model.

    `baseline` has no server and `nexus-plugin` brings its own, so neither takes a model;
    a name that already carries `@model` is kept.
    With no models the list is returned unchanged (the server then uses its default).
    """
    out: List[str] = []
    for name in names:
        base, model = split_condition(name)
        if base in NO_MODEL_CONDITIONS or model or not embedding_models:
            out.append(name)
        else:
            out += [f"{name}@{m}" for m in embedding_models]
    return out


def model_mcp_config(
    embedding_model: str, storage_dir: Path, python: Optional[str] = None
) -> Dict[str, Any]:
    """MCP config that starts nexus with one embedding model and its own index folder.

    The server starts through `benchmarks.nexus_server`, so a candidate model that is
    not in the shipped registry (see evals/retrieval/candidates.py) still loads.
    `storage_dir` must hold an index built with the same model (preindex_models.py).
    """
    src_dirs = os.pathsep.join([str(REPO_ROOT / "src"), str(REPO_ROOT)])
    return {
        "mcpServers": {
            "nexus-mcp": {
                "command": python or sys.executable,
                "args": ["-m", "benchmarks.nexus_server"],
                "env": {
                    "PYTHONPATH": src_dirs,
                    "NEXUS_EMBEDDING_MODEL": embedding_model,
                    "NEXUS_STORAGE_DIR": str(storage_dir),
                },
            }
        }
    }


def write_model_mcp_config(
    path: Path, embedding_model: str, storage_dir: Path, python: Optional[str] = None
) -> Path:
    """Write `model_mcp_config` to `path` (parents are created) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model_mcp_config(embedding_model, storage_dir, python), indent=2))
    return path


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
        "dontAsk",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_budget_usd),
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
    builtin_tools: str = BASELINE_TOOLS,
) -> List[str]:
    """Build the full argv for a `claude` invocation under the given condition.

    `condition` is one of KNOWN_CONDITIONS, optionally with `@<embedding model>` (the model
    is applied through `mcp_config_path`, not here). Raises ValueError otherwise.
    `builtin_tools` is the `--tools` list; the routing eval adds `ToolSearch`
    so deferred MCP tools stay discoverable.
    """
    base, embedding_model = split_condition(condition)
    if base not in KNOWN_CONDITIONS or (embedding_model and base in NO_MODEL_CONDITIONS):
        raise ValueError(f"Unknown condition: {condition!r}, expected one of {KNOWN_CONDITIONS}")
    condition = base

    argv = _common_args(model, max_budget_usd)
    argv += ["--tools", builtin_tools]
    # --strict-mcp-config ignores every MCP server except the ones in --mcp-config.
    # The plugin condition gets its server from the plugin itself, so it must not
    # set the flag, or the server would never start.
    if condition != "nexus-plugin":
        argv.append("--strict-mcp-config")

    if condition == "mcp-only":
        argv += ["--mcp-config", str(mcp_config_path)]
    elif condition == "nexus":
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
    tool_search: Optional[bool] = None,
) -> Dict[str, str]:
    """Build the isolated environment for a benchmark run.

    Uses `--bare`-compatible isolation when ANTHROPIC_API_KEY is present in
    base_env (or the real environment); otherwise callers must additionally
    pass `--strict-mcp-config --setting-sources ""` and accept the reduced
    isolation (real ~/.claude settings/hooks may still apply).

    `tool_search=False` sets ENABLE_TOOL_SEARCH=false so every MCP tool loads up
    front; `True` or `None` removes the variable so Claude Code's default
    (deferred MCP tools) applies.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    if tool_search is False:
        env["ENABLE_TOOL_SEARCH"] = "false"
    else:
        env.pop("ENABLE_TOOL_SEARCH", None)
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

    --strict-mcp-config is added by build_argv (not for the plugin condition). This adds
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
    tool_search: Optional[bool] = None,
    builtin_tools: str = BASELINE_TOOLS,
    mcp_config_path: Path = NEXUS_MCP_CONFIG,
) -> Dict[str, Any]:
    """Build the full (argv, env, isolation_mode) triple for one run."""
    argv = build_argv(
        condition, prompt, model, max_budget_usd, mcp_config_path=mcp_config_path,
        builtin_tools=builtin_tools,
    )
    # --mcp-config, --tools and similar options take a list of values and swallow a
    # prompt that follows them. Take the prompt off, add the isolation flags, then put
    # it back after `--` so it is always read as the prompt.
    prompt_arg = argv.pop()
    run_env = build_env(config_dir, env, tool_search=tool_search)

    if has_api_key(run_env):
        argv = apply_bare_isolation(argv, run_env)
        isolation_mode = "bare"
    else:
        argv = apply_reduced_isolation(argv)
        isolation_mode = "reduced"

    argv += ["--", prompt_arg]
    return {"argv": argv, "env": run_env, "isolation_mode": isolation_mode}
