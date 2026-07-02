"""Session state management for Nexus-MCP server."""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from nexus_mcp.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Singleton state for the MCP server session."""

    settings: Settings = field(default_factory=get_settings)
    codebase_path: Optional[Path] = None
    codebase_paths: list = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # Engine references (populated during indexing)
    _vector_engine: Any = None
    _graph_engine: Any = None
    _bm25_engine: Any = None
    _memory_store: Any = None
    _reranker: Any = None
    _shutting_down: bool = False
    _shutdown_lock: threading.Lock = field(default_factory=threading.Lock)

    # Auto-watch (one DebouncedFileWatcher per indexed root)
    _file_watchers: list = field(default_factory=list)

    # Throttled staleness-check cache (see indexing/pipeline.py:check_staleness)
    _staleness_cache: Optional[dict] = None
    _staleness_checked_at: float = 0.0

    @property
    def is_indexed(self) -> bool:
        """Check if a codebase has been indexed."""
        return self.codebase_path is not None

    @property
    def vector_engine(self):
        return self._vector_engine

    @vector_engine.setter
    def vector_engine(self, engine):
        self._vector_engine = engine

    @property
    def graph_engine(self):
        return self._graph_engine

    @graph_engine.setter
    def graph_engine(self, engine):
        self._graph_engine = engine

    @property
    def bm25_engine(self):
        return self._bm25_engine

    @bm25_engine.setter
    def bm25_engine(self, engine):
        self._bm25_engine = engine

    @property
    def memory_store(self):
        return self._memory_store

    @memory_store.setter
    def memory_store(self, store):
        self._memory_store = store

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    async def _stop_watchers(self) -> None:
        """Stop every active file watcher. Best-effort — one failure doesn't block the rest."""
        for watcher in self._file_watchers:
            try:
                await watcher.stop()
            except Exception as e:
                logger.warning("Failed to stop a file watcher: %s", e)

    def shutdown(self) -> None:
        """Gracefully shut down: stop watchers, persist graph state, and clean up resources."""
        with self._shutdown_lock:
            if self._shutting_down:
                return
            self._shutting_down = True

        # Stop file watchers before persisting graph state, so nothing mutates
        # engine state while it's being written to SQLite. shutdown() runs from
        # main()'s `finally` block after server.run() has already returned/raised,
        # so there is normally no running event loop to await onto here —
        # asyncio.run() is the correct way to drive the async stop() from this
        # sync context. Check for a running loop *before* constructing the
        # coroutine (rather than catching RuntimeError from asyncio.run) so an
        # unrunnable coroutine is never created — creating one and discarding it
        # triggers Python's "coroutine was never awaited" warning.
        if self._file_watchers:
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                try:
                    asyncio.run(self._stop_watchers())
                except Exception as e:
                    logger.warning("Failed to stop file watchers: %s", e)
            else:
                logger.warning(
                    "Could not stop file watchers synchronously: called from "
                    "within a running event loop."
                )

        # Persist graph to SQLite if populated
        if self._graph_engine and self.codebase_path:
            try:
                from nexus_mcp.persistence.store import GraphPersistence

                persistence = GraphPersistence(str(self.settings.graph_path))
                persistence.save(self._graph_engine)
                logger.info("Graph state persisted to %s", self.settings.graph_path)
            except Exception as e:
                logger.warning("Failed to persist graph state: %s", e)

        logger.info("Nexus-MCP shutdown complete.")


_state: Optional[SessionState] = None


def get_state() -> SessionState:
    """Get or create the singleton session state."""
    global _state
    if _state is None:
        _state = SessionState()
    return _state


def reset_state() -> None:
    """Reset session state (for testing)."""
    global _state
    _state = None
