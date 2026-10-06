"""Live search engine using ripgrep (rg) with a fallback to standard grep.

Provides 100% code coverage for unindexed or newly created files.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REFERENCES_TIMEOUT_S = 10
# ripgrep skips hidden and git-ignored paths by itself; plain grep needs the list.
_GREP_SKIP_DIRS = (
    ".git", ".nexus*", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
)


class LiveGrepEngine:
    """Live search using ripgrep (rg) with a fallback to standard grep."""

    def __init__(self, workspace_path: Optional[str] = None):
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self.rg_path = shutil.which("rg")
        self.grep_path = shutil.which("grep")

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for query in workspace. Favors ripgrep, falls back to grep."""
        if not query or not query.strip():
            return []

        if self.rg_path:
            try:
                return self._search_rg(query, limit)
            except Exception as e:
                logger.warning("Ripgrep search failed, trying grep: %s", e)

        if self.grep_path:
            try:
                return self._search_grep(query, limit)
            except Exception as e:
                logger.warning("Grep search failed: %s", e)

        logger.warning("Neither ripgrep nor grep found on system.")
        return []

    def references(
        self, name: str, max_files: int = 40, max_lines_per_file: int = 8
    ) -> Optional[Dict[str, Any]]:
        """Every line where ``name`` appears as a whole word, grouped by file.

        This is the complete text answer to "where is this name used". Call edges in the
        graph are a lower bound (a name with several definitions gets no edge), so an
        agent that only gets edges runs its own grep to check them. Returns None when
        neither ripgrep nor grep is available or the search fails.

        Source files come before test files. ``truncated`` is True when the caps cut the
        list; ``total_files`` and ``total_lines`` always count everything found.
        """
        from nexus_mcp.engines.fusion import is_test_path

        name = name.rsplit(".", 1)[-1].strip()
        if not name:
            return None
        if self.rg_path:
            cmd = [
                self.rg_path, "--no-heading", "--line-number", "--word-regexp",
                "--fixed-strings", "--no-messages", "--color", "never", "-e", name, ".",
            ]
        elif self.grep_path:
            cmd = [self.grep_path, "-rnIwF"]
            cmd += [f"--exclude-dir={d}" for d in _GREP_SKIP_DIRS]
            cmd += ["-e", name, "."]
        else:
            return None
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                cwd=str(self.workspace_path), timeout=REFERENCES_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("Reference search failed: %s", e)
            return None

        by_file: Dict[str, List[int]] = {}
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            path = parts[0][2:] if parts[0].startswith("./") else parts[0]
            by_file.setdefault(path, []).append(int(parts[1]))

        ordered = sorted(by_file, key=lambda p: (is_test_path(p), p))
        files = {p: by_file[p][:max_lines_per_file] for p in ordered[:max_files]}
        return {
            "total_files": len(by_file),
            "total_lines": sum(len(v) for v in by_file.values()),
            "files": files,
            "truncated": len(by_file) > max_files
            or any(len(by_file[p]) > max_lines_per_file for p in files),
        }

    def _search_rg(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Run ripgrep with JSON output."""
        # Use -M for max-columns to avoid huge lines, --json for structured data
        cmd = [
            self.rg_path,
            "--json",
            "--line-number",
            "--max-count",
            str(limit),
            "--smart-case",
            "--heading",
            query,
            str(self.workspace_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            results = []
            for line in result.stdout.splitlines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        payload = data["data"]
                        path_text = payload["path"]["text"]

                        # Handle both relative and absolute paths from rg
                        if os.path.isabs(path_text):
                            abs_path = path_text
                        else:
                            abs_path = str((self.workspace_path / path_text).absolute())

                        results.append(
                            {
                                "absolute_path": abs_path,
                                "line_start": payload["line_number"],
                                "code_snippet": payload["lines"]["text"].strip(),
                                "score": 0.5,  # Baseline score for live-grep
                                "search_mode": "live_grep_rg",
                            }
                        )
                except (json.JSONDecodeError, KeyError):
                    continue
            return results[:limit]
        except Exception as e:
            logger.error("Ripgrep execution error: %s", e)
            raise

    def _search_grep(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Run standard grep -rn."""
        # Note: grep -rn is recursive, line numbered, and ignores binary files with -I
        # macOS grep supports -m for max-count
        cmd = [
            self.grep_path,
            "-rnI",
            "-m",
            str(limit),
            query,
            str(self.workspace_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            results = []
            for line in result.stdout.splitlines():
                if not line:
                    continue
                # Format: file:line:content
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    try:
                        path_text = parts[0]
                        if os.path.isabs(path_text):
                            abs_path = path_text
                        else:
                            abs_path = str((self.workspace_path / path_text).absolute())

                        results.append(
                            {
                                "absolute_path": abs_path,
                                "line_start": int(parts[1]),
                                "code_snippet": parts[2].strip(),
                                "score": 0.4,  # Slightly lower score for grep fallback
                                "search_mode": "live_grep_grep",
                            }
                        )
                    except ValueError:
                        continue
            return results[:limit]
        except Exception as e:
            logger.error("Grep execution error: %s", e)
            raise
