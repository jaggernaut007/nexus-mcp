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
