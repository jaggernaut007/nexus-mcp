"""Parallel file parsing utilities.

Ported from CodeGrok MCP. Uses ThreadPoolExecutor with thread-local parsers.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from nexus_mcp.core.models import Symbol
from nexus_mcp.parsing.treesitter_parser import ThreadLocalParserFactory


@dataclass
class ParseResult:
    """Result of parsing a single file."""

    filepath: str
    symbols: List[Symbol]
    success: bool
    error: Optional[str] = None


@dataclass
class ParallelProgress:
    """Thread-safe progress tracker."""

    total: int
    _completed: int = 0
    _errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed

    @property
    def errors(self) -> int:
        with self._lock:
            return self._errors

    def increment_completed(self) -> int:
        with self._lock:
            self._completed += 1
            return self._completed

    def increment_errors(self) -> int:
        with self._lock:
            self._errors += 1
            return self._errors


def parse_file_worker(filepath: Path, parser_factory: ThreadLocalParserFactory) -> ParseResult:
    """Worker function for parallel parsing."""
    parser = parser_factory.get_parser()
    try:
        parsed = parser.parse_file(str(filepath))
        return ParseResult(
            filepath=str(filepath),
            symbols=list(parsed.symbols),
            success=parsed.error is None,
            error=parsed.error,
        )
    except Exception as e:
        return ParseResult(filepath=str(filepath), symbols=[], success=False, error=str(e))


def parallel_parse_files(
    files: List[Path],
    max_workers: Optional[int] = None,
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Tuple[List[Symbol], int]:
    """Parse files in parallel. Returns (symbols, error_count)."""
    if not files:
        return [], 0

    factory = ThreadLocalParserFactory()
    progress = ParallelProgress(total=len(files))
    all_symbols: List[Symbol] = []
    symbols_lock = threading.Lock()

    def emit(event_type: str, data: dict):
        if progress_callback:
            try:
                progress_callback(event_type, data)
            except Exception:
                pass

    if max_workers is None:
        cpu_count = os.cpu_count() or 4
        max_workers = max(1, min(cpu_count - 1, 4))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(parse_file_worker, f, factory): f for f in files
        }

        for future in as_completed(future_to_file):
            filepath = future_to_file[future]
            try:
                result = future.result()
                completed = progress.increment_completed()

                if result.success and result.symbols:
                    with symbols_lock:
                        all_symbols.extend(result.symbols)
                    emit("file_parsed", {
                        "path": result.filepath,
                        "symbols": len(result.symbols),
                        "index": completed,
                        "total": progress.total,
                    })
                elif result.error:
                    progress.increment_errors()
                    emit("parse_error", {"path": result.filepath, "error": result.error})
                else:
                    emit("file_parsed", {
                        "path": result.filepath,
                        "symbols": 0,
                        "index": completed,
                        "total": progress.total,
                    })
            except Exception as e:  # pragma: no cover
                progress.increment_completed()
                progress.increment_errors()
                emit("parse_error", {"path": str(filepath), "error": str(e)})

    return all_symbols, progress.errors
