"""Custom exceptions for Nexus-MCP.

Ported from CodeGrok MCP with NexusException as base.
"""


class NexusException(Exception):
    """Base exception for all Nexus-MCP operations."""

    pass


class ParseError(NexusException):
    """Raised when file parsing fails."""

    def __init__(self, filepath: str, language: str, details: str):
        self.filepath = filepath
        self.language = language
        self.details = details
        super().__init__(f"Failed to parse {filepath} ({language}): {details}")


class IndexingError(NexusException):
    """Raised when indexing operation fails."""

    pass


class EmbeddingError(NexusException):
    """Raised when embedding generation fails."""

    pass


class SearchError(NexusException):
    """Raised when search operation fails."""

    pass


class ConfigurationError(NexusException):
    """Raised when configuration is invalid."""

    pass


class GraphError(NexusException):
    """Raised when graph operations fail."""

    pass


class MemoryStoreError(NexusException):
    """Raised when memory store operations fail."""

    pass


class FusionError(NexusException):
    """Raised when fusion or reranking fails."""

    pass


class AuthenticationError(NexusException):
    """Raised when authentication fails."""

    pass


class AuthorizationError(NexusException):
    """Raised when a tool access is denied by permission policy."""

    pass


class RateLimitError(NexusException):
    """Raised when rate limit is exceeded."""

    pass
