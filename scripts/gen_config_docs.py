"""Write docs/CONFIGURATION.md from src/nexus_mcp/config.py, so the variable list cannot drift.

The variable names and defaults come from the source (``env_map`` and the ``Settings``
fields). Only the one-line descriptions live here. A new variable with no description
fails ``--check``.

Run: python scripts/gen_config_docs.py          (rewrite docs/CONFIGURATION.md)
     python scripts/gen_config_docs.py --check  (exit 1 when it is out of date)
"""

import ast
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "src" / "nexus_mcp" / "config.py"
OUT = ROOT / "docs" / "CONFIGURATION.md"

DESCRIPTIONS = {
    "NEXUS_STORAGE_DIR": "Folder for the index, memories and graph. Relative to the folder the server starts in.",
    "NEXUS_EMBEDDING_MODEL": "Embedding model key. Only registered models load. Re-index after a change.",
    "NEXUS_EMBEDDING_DEVICE": "`auto`, `cpu`, `cuda` or `mps`.",
    "NEXUS_EMBEDDING_BATCH_SIZE": "Chunks embedded in one batch.",
    "NEXUS_MAX_FILE_SIZE_MB": "Skip files larger than this.",
    "NEXUS_MAX_WORKERS": "Parser threads. Empty means the default of the thread pool.",
    "NEXUS_CHUNK_MAX_CHARS": "Largest code snippet stored for one chunk.",
    "NEXUS_INDEX_FILE_BATCH_SIZE": "Files parsed and stored in one streaming batch.",
    "NEXUS_GRAPH_MAX_DEPTH": "Deepest traversal of `graph` with `transitive=true`.",
    "NEXUS_SEARCH_MODE": "`hybrid`, `vector` or `bm25`.",
    "NEXUS_FUSION_WEIGHT_VECTOR": "Weight of vector results in Reciprocal Rank Fusion.",
    "NEXUS_FUSION_WEIGHT_BM25": "Weight of BM25 results in Reciprocal Rank Fusion.",
    "NEXUS_MAX_MEMORY_MB": "Memory budget in MB (about 90 MB idle, 460 MB with the model loaded).",
    "NEXUS_AUTO_RESTORE": "Attach the stored index when a new server process starts.",
    "NEXUS_WARM_START": "Load the index and the model in the background while the server starts.",
    "NEXUS_AUTO_WATCH": "Watch indexed folders and reindex changed files in the background.",
    "NEXUS_STALENESS_CHECK_INTERVAL": "Seconds between stale-file checks in `status` and `search`.",
    "NEXUS_LOG_LEVEL": "`DEBUG`, `INFO`, `WARNING` or `ERROR`.",
    "NEXUS_LOG_FORMAT": "`text` or `json`.",
    "NEXUS_TRUST_REMOTE_CODE": "Needed only by the deprecated `jina-code` model.",
    "NEXUS_AUTH_MODE": "`none` or `local`.",
    "NEXUS_PERMISSION_LEVEL": "`full`, or `read` to block tools that change the index or memory.",
    "NEXUS_AUDIT_ENABLED": "Log each tool call with a correlation id. Secret fields are redacted.",
    "NEXUS_AUDIT_LOG_FILE": "Write the audit log to this file. Empty means stderr.",
    "NEXUS_RATE_LIMIT_ENABLED": "Turn on per-tool token-bucket rate limits.",
    "NEXUS_RATE_LIMIT_DEFAULT_RATE": "Requests per second for each tool.",
    "NEXUS_RATE_LIMIT_DEFAULT_BURST": "Burst size for each tool.",
}

# Variables read outside Settings: name -> (default, description).
OTHER = {
    "NEXUS_OFFLINE": (
        "unset",
        "Set to `1` to block the model download (sets `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE`).",
    ),
    "NEXUS_EVAL_HOME": ("`~/.nexus-mcp/evals`", "Where `nexus-mcp-ci eval` saves its reports."),
    "NEXUS_EVAL_CACHE": ("`~/.cache/nexus-mcp/eval`", "Where `eval <url>` keeps its clones."),
    "NEXUS_EVAL_PRICING": (
        "unset",
        "A JSON file of Codex prices (USD per million tokens) for `eval --agent codex`.",
    ),
}


def _settings_defaults(tree: ast.Module) -> Dict[str, str]:
    """Field name -> default, as written in the Settings dataclass."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                stmt.target.id: ast.unparse(stmt.value)
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.value
            }
    raise SystemExit("Settings class not found in config.py")


def _env_map(tree: ast.Module) -> List[Tuple[str, str]]:
    """(variable, field) pairs, in source order, from the env_map dict."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "env_map" for t in node.targets)
            and isinstance(node.value, ast.Dict)
        ):
            return [(k.value, v.elts[0].value) for k, v in zip(node.value.keys, node.value.values)]
    raise SystemExit("env_map not found in config.py")


def _fmt(default: str) -> str:
    """A Python default written the way a user types it in an environment variable."""
    value = ast.literal_eval(default)
    if value is None or value == "":
        return "unset"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    return f"`{value}`"


def build() -> str:
    tree = ast.parse(CONFIG.read_text())
    defaults = _settings_defaults(tree)
    pairs = _env_map(tree)
    missing = [name for name, _ in pairs if name not in DESCRIPTIONS]
    if missing:
        raise SystemExit(
            f"Add a description in scripts/gen_config_docs.py for: {', '.join(missing)}"
        )
    lines = [
        "# Configuration",
        "",
        "<!-- Generated by scripts/gen_config_docs.py from src/nexus_mcp/config.py. Do not edit. -->",
        "",
        "Set these as environment variables of the server, for example "
        "`claude mcp add nexus-mcp -e NEXUS_SEARCH_MODE=bm25 -- nexus-mcp-ci`. "
        "An invalid value logs a warning and keeps the default.",
        "",
        "## Server",
        "",
        "| Variable | Default | Effect |",
        "|---|---|---|",
    ]
    for name, field in pairs:
        lines.append(f"| `{name}` | {_fmt(defaults.get(field, ''))} | {DESCRIPTIONS[name]} |")
    lines += [
        "",
        "## Privacy and the eval CLI",
        "",
        "| Variable | Default | Effect |",
        "|---|---|---|",
    ]
    for name, (default, text) in OTHER.items():
        lines.append(f"| `{name}` | {default} | {text} |")
    lines += [
        "",
        "`HF_HUB_DISABLE_TELEMETRY` is set to `1` unless you set it yourself. See "
        "[PRIVACY.md](PRIVACY.md).",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = build()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("docs/CONFIGURATION.md is out of date. Run: python scripts/gen_config_docs.py")
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
