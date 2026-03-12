"""Abstract interfaces for Nexus-MCP components.

Ported from CodeGrok MCP with IEngine added for search engines.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import ParsedFile


class IParser(ABC):
    """Abstract interface for code parsers."""

    @abstractmethod
    def can_parse(self, filepath: str) -> bool:
        """Check if this parser can handle the given file."""
        pass  # pragma: no cover

    @abstractmethod
    def parse_file(self, filepath: str) -> ParsedFile:
        """Parse a source file and extract all symbols."""
        pass  # pragma: no cover

    @abstractmethod
    def get_supported_extensions(self) -> List[str]:
        """Return supported file extensions (with leading dots)."""
        pass  # pragma: no cover


class IEngine(ABC):
    """Abstract interface for search/storage engines (vector, BM25, graph)."""

    @abstractmethod
    def add(self, items: List[Dict[str, Any]]) -> None:
        """Add items to the engine."""
        pass  # pragma: no cover

    @abstractmethod
    def search(self, query: str, limit: int = 10, **kwargs) -> List[Dict[str, Any]]:
        """Search and return ranked results."""
        pass  # pragma: no cover

    @abstractmethod
    def delete(self, ids: List[str]) -> None:
        """Delete items by ID."""
        pass  # pragma: no cover

    @abstractmethod
    def count(self) -> int:
        """Return total number of items."""
        pass  # pragma: no cover

    def clear(self) -> None:
        """Clear all items. Default implementation deletes all."""
        pass  # pragma: no cover
