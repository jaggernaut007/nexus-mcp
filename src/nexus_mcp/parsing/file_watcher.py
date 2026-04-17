"""Debounced file watcher for automatic re-indexing.

Ported from code-graph-mcp. Monitors source files and triggers
callbacks with intelligent debouncing.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set, Union

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class DebouncedFileWatcher:
    """File system watcher with debounced callbacks."""

    def __init__(
        self,
        project_root: Path,
        callback: Union[Callable[[], None], Callable[[], Awaitable[None]]],
        debounce_delay: float = 2.0,
        should_ignore_path: Optional[Callable[[Path, Path], bool]] = None,
        supported_extensions: Optional[Set[str]] = None,
    ):
        self.project_root = project_root
        self.callback = callback
        self.debounce_delay = debounce_delay
        self.should_ignore_path = should_ignore_path
        self.supported_extensions = supported_extensions or set()

        self._observer: Optional[Observer] = None
        self._debounce_task: Optional[asyncio.Task] = None
        self._last_change_time = 0.0
        self._is_running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_changes: Set[str] = set()
        self._change_cleanup_timer: Optional[float] = None

    class _EventHandler(FileSystemEventHandler):
        def __init__(self, watcher: "DebouncedFileWatcher"):
            self.watcher = watcher
            super().__init__()

        def on_modified(self, event: FileSystemEvent) -> None:
            if not event.is_directory:
                self.watcher._handle_file_change(Path(event.src_path))

        def on_created(self, event: FileSystemEvent) -> None:
            if not event.is_directory:
                self.watcher._handle_file_change(Path(event.src_path))

        def on_deleted(self, event: FileSystemEvent) -> None:
            if not event.is_directory:
                self.watcher._handle_file_change(Path(event.src_path))

        def on_moved(self, event: FileSystemEvent) -> None:
            if not event.is_directory:
                self.watcher._handle_file_change(Path(event.src_path))
                if hasattr(event, "dest_path"):
                    self.watcher._handle_file_change(Path(event.dest_path))

    def _should_watch_file(self, file_path: Path) -> bool:
        try:
            if self.should_ignore_path and self.should_ignore_path(file_path, self.project_root):
                return False
            if (
                self.supported_extensions
                and file_path.suffix.lower() not in self.supported_extensions
            ):
                return False
            if file_path.name.startswith(".") or file_path.name.endswith("~"):
                return False
            temp_patterns = {".tmp", ".temp", ".swp", ".swo", ".bak", ".orig"}
            if any(file_path.name.endswith(p) for p in temp_patterns):
                return False
            return True
        except Exception as e:
            logger.debug("Failed to process file change: %s", e)
            return False

    def _handle_file_change(self, file_path: Path) -> None:
        if not self._should_watch_file(file_path):
            return
        file_str = str(file_path)
        self._cleanup_recent_changes_if_needed()
        if file_str in self._recent_changes:
            return
        self._recent_changes.add(file_str)
        self._change_cleanup_timer = time.time() + 10.0
        self._last_change_time = time.time()
        if self._loop and self._loop.is_running():
            if self._debounce_task and not self._debounce_task.done():
                self._debounce_task.cancel()
            self._loop.call_soon_threadsafe(self._create_debounce_task)

    def _create_debounce_task(self) -> None:
        self._debounce_task = asyncio.create_task(self._debounced_callback())

    def _cleanup_recent_changes_if_needed(self) -> None:
        if self._change_cleanup_timer and time.time() > self._change_cleanup_timer:
            self._recent_changes.clear()
            self._change_cleanup_timer = None

    async def _debounced_callback(self) -> None:
        try:
            await asyncio.sleep(self.debounce_delay)
            time_since = time.time() - self._last_change_time
            if time_since < self.debounce_delay:
                await asyncio.sleep(self.debounce_delay - time_since)
            result = self.callback()
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Error in debounced callback: %s", e)

    async def start(self) -> None:
        if self._is_running:
            return
        self._loop = asyncio.get_running_loop()
        self._observer = Observer()
        self._observer.schedule(
            self._EventHandler(self), str(self.project_root), recursive=True
        )
        self._observer.start()
        self._is_running = True
        logger.info("File watcher started: %s", self.project_root)

    async def stop(self) -> None:
        if not self._is_running:
            return
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        self._change_cleanup_timer = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
        self._is_running = False
        self._recent_changes.clear()
        self._loop = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_stats(self) -> dict:
        return {
            "is_running": self._is_running,
            "project_root": str(self.project_root),
            "debounce_delay": self.debounce_delay,
            "recent_changes_count": len(self._recent_changes),
        }
