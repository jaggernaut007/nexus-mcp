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
    _shutting_down: bool = False
    _shutdown_lock: threading.Lock = field(default_factory=threading.Lock)

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

    def shutdown(self) -> None:
        """Gracefully shut down: persist graph state and clean up resources."""
        with self._shutdown_lock:
            if self._shutting_down:
                return
            self._shutting_down = True

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
