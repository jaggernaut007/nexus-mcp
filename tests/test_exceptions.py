"""Tests for core/exceptions.py."""

import pytest

from nexus_mcp.core.exceptions import (
    ConfigurationError,
    EmbeddingError,
    GraphError,
    IndexingError,
    NexusException,
    ParseError,
    SearchError,
)


def test_nexus_exception_base():
    with pytest.raises(NexusException):
        raise NexusException("base error")


def test_parse_error():
    err = ParseError("/test.py", "python", "syntax error")
    assert err.filepath == "/test.py"
    assert err.language == "python"
    assert "syntax error" in str(err)


def test_indexing_error():
    with pytest.raises(IndexingError):
        raise IndexingError("indexing failed")


def test_embedding_error():
    with pytest.raises(EmbeddingError):
        raise EmbeddingError("model load failed")


def test_search_error():
    with pytest.raises(SearchError):
        raise SearchError("query failed")


def test_configuration_error():
    with pytest.raises(ConfigurationError):
        raise ConfigurationError("invalid config")


def test_graph_error():
    with pytest.raises(GraphError):
        raise GraphError("graph op failed")


def test_all_inherit_from_nexus():
    assert issubclass(ParseError, NexusException)
    assert issubclass(IndexingError, NexusException)
    assert issubclass(EmbeddingError, NexusException)
    assert issubclass(SearchError, NexusException)
    assert issubclass(ConfigurationError, NexusException)
    assert issubclass(GraphError, NexusException)
