"""Tests for trust_remote_code mitigation."""

import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.core.exceptions import ConfigurationError
from nexus_mcp.indexing.embedding_service import (
    EMBEDDING_MODELS,
    EmbeddingService,
    reset_embedding_service,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_settings()
    reset_embedding_service()
    yield
    reset_settings()
    reset_embedding_service()


def test_bge_small_no_trust_remote_code():
    """bge-small-en does not require trust_remote_code."""
    config = EMBEDDING_MODELS["bge-small-en"]
    assert config["trust_remote_code"] is False


def test_granite_no_trust_remote_code():
    """granite-embedding-small does not require trust_remote_code."""
    config = EMBEDDING_MODELS["granite-embedding-small"]
    assert config["trust_remote_code"] is False


def test_jina_requires_trust_remote_code():
    """jina-code config has trust_remote_code=True."""
    config = EMBEDDING_MODELS["jina-code"]
    assert config["trust_remote_code"] is True


def test_trust_remote_code_default_is_true():
    """Settings default trust_remote_code to True (needed for jina-code default model)."""
    s = Settings()
    assert s.trust_remote_code is True


def test_jina_blocked_when_trust_disabled(monkeypatch):
    """Loading jina-code with NEXUS_TRUST_REMOTE_CODE=false raises ConfigurationError."""
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "false")
    reset_settings()
    svc = EmbeddingService(model_name="jina-code")
    with pytest.raises(ConfigurationError, match="trust_remote_code"):
        svc._load_model()


def test_jina_allowed_with_opt_in(monkeypatch):
    """Loading jina-code with NEXUS_TRUST_REMOTE_CODE=true passes the gate.

    Note: actual model download is not tested; we verify the gate doesn't block.
    """
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "true")
    reset_settings()
    svc = EmbeddingService(model_name="jina-code")
    try:
        svc._load_model()
    except ConfigurationError:
        pytest.fail("ConfigurationError should not be raised when trust_remote_code is opted in")
    except (ImportError, Exception):
        # SentenceTransformer may not be installed or model not available in CI
        pass


def test_unsupported_model_rejected():
    """Unsupported model names are rejected with ConfigurationError."""
    from nexus_mcp.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Unsupported embedding model"):
        EmbeddingService(model_name="some-custom-model")


def test_trust_remote_code_env_var(monkeypatch):
    """NEXUS_TRUST_REMOTE_CODE env var correctly sets the config."""
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "true")
    reset_settings()
    s = Settings()
    assert s.trust_remote_code is True

    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "false")
    reset_settings()
    s = Settings()
    assert s.trust_remote_code is False
