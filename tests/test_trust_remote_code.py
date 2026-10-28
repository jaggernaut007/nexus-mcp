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

    The model class is a stand-in (the download is the boundary), so the test is fast
    and checks that the load reaches it with trust_remote_code set.
    """
    import sys
    import types

    seen = {}

    class FakeModel:
        max_seq_length = 8192

        def __init__(self, name, **kwargs):
            seen.update(kwargs)

        def get_sentence_embedding_dimension(self):
            return 768

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "true")
    reset_settings()
    svc = EmbeddingService(model_name="jina-code", device="cpu")

    svc._load_model()

    assert seen.get("trust_remote_code") is True


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
