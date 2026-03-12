"""Tests for trust_remote_code mitigation (Phase 5a)."""

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


def test_default_model_no_trust_remote_code():
    """bge-small-en does not require trust_remote_code."""
    config = EMBEDDING_MODELS["bge-small-en"]
    assert config["trust_remote_code"] is False


def test_coderankembed_requires_trust_remote_code():
    """coderankembed config has trust_remote_code=True."""
    config = EMBEDDING_MODELS["coderankembed"]
    assert config["trust_remote_code"] is True


def test_trust_remote_code_default_is_false():
    """Settings default trust_remote_code to False."""
    s = Settings()
    assert s.trust_remote_code is False


def test_coderankembed_blocked_without_opt_in(monkeypatch):
    """Loading coderankembed without NEXUS_TRUST_REMOTE_CODE raises ConfigurationError."""
    monkeypatch.delenv("NEXUS_TRUST_REMOTE_CODE", raising=False)
    svc = EmbeddingService(model_name="coderankembed")
    with pytest.raises(ConfigurationError, match="trust_remote_code"):
        svc._load_model()


def test_coderankembed_allowed_with_opt_in(monkeypatch):
    """Loading coderankembed with NEXUS_TRUST_REMOTE_CODE=true passes the gate.

    Note: actual model download is not tested; we verify the gate doesn't block.
    The SentenceTransformer import may fail in CI without the package, so we
    check that ConfigurationError is NOT raised (other errors are acceptable).
    """
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "true")
    reset_settings()
    svc = EmbeddingService(model_name="coderankembed")
    try:
        svc._load_model()
    except ConfigurationError:
        pytest.fail("ConfigurationError should not be raised when trust_remote_code is opted in")
    except (ImportError, Exception):
        # SentenceTransformer may not be installed or model not available in CI
        pass


def test_custom_model_defaults_to_no_trust():
    """Custom model names default to trust_remote_code=False and won't hit the gate."""
    svc = EmbeddingService(model_name="some-custom-model")
    assert svc.config["trust_remote_code"] is False


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
