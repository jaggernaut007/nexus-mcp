"""Property-based tests for embedding service using hypothesis.

Fast tests use mocks. Slow integration tests (marked @pytest.mark.slow)
load real models — run with: pytest -m slow
"""

from unittest.mock import MagicMock

import numpy as np
import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

try:
    import optimum  # noqa: F401
    _has_optimum = True
except ImportError:
    _has_optimum = False

from nexus_mcp.config import reset_settings
from nexus_mcp.indexing.embedding_service import (
    DEFAULT_MODEL,
    EMBEDDING_MODELS,
    EmbeddingService,
    _detect_device,
    get_embedding_service,
    reset_embedding_service,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_settings()
    reset_embedding_service()
    yield
    reset_settings()
    reset_embedding_service()


# ---------------------------------------------------------------------------
# Required keys every model config must have
# ---------------------------------------------------------------------------
REQUIRED_KEYS = {
    "hf_name",
    "dimensions",
    "max_seq_length",
    "trust_remote_code",
    "prompt_prefix",
    "query_prefix",
    "backend",
}


# ===========================================================================
# Fast property tests (mocked, no model loading)
# ===========================================================================


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_model_config_has_all_required_keys(model_name):
    """Every registered model config contains all required keys."""
    config = EMBEDDING_MODELS[model_name]
    assert REQUIRED_KEYS.issubset(config.keys())


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_config_dimensions_are_valid(model_name):
    """Dimensions must be positive and one of the supported sizes."""
    config = EMBEDDING_MODELS[model_name]
    assert config["dimensions"] > 0
    assert config["dimensions"] in (384, 768)


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_config_max_seq_length_positive(model_name):
    """Max sequence length must be positive."""
    config = EMBEDDING_MODELS[model_name]
    assert config["max_seq_length"] > 0


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_service_config_matches_registry(model_name):
    """EmbeddingService.__init__ copies the correct config from registry."""
    svc = EmbeddingService(model_name=model_name)
    assert svc.config["dimensions"] == EMBEDDING_MODELS[model_name]["dimensions"]
    assert svc.config["hf_name"] == EMBEDDING_MODELS[model_name]["hf_name"]
    assert svc.config["trust_remote_code"] == EMBEDDING_MODELS[model_name]["trust_remote_code"]


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_service_init_does_not_load_model(model_name):
    """Creating an EmbeddingService should not trigger model loading (lazy)."""
    svc = EmbeddingService(model_name=model_name)
    assert svc._model is None
    assert svc._model_loaded is False


def test_default_model_is_in_registry():
    """DEFAULT_MODEL must exist in EMBEDDING_MODELS."""
    assert DEFAULT_MODEL in EMBEDDING_MODELS


def test_device_detection_returns_valid_string():
    """_detect_device must return one of the known device strings."""
    device = _detect_device()
    assert device in ("cpu", "cuda", "mps")


@given(n=st.integers(min_value=1, max_value=50))
@hyp_settings(deadline=None)
def test_batch_embed_returns_correct_count(n):
    """embed_batch returns exactly as many vectors as input texts."""
    svc = EmbeddingService(model_name="jina-code")
    dims = svc.config["dimensions"]

    # Mock the model
    mock_model = MagicMock()
    mock_model.encode.return_value = np.array([[0.1] * dims] * n)
    svc._model = mock_model
    svc._model_loaded = True

    texts = [f"text {i}" for i in range(n)]
    results = svc.embed_batch(texts)
    assert len(results) == n
    for vec in results:
        assert len(vec) == dims


@given(text=st.text(min_size=1, max_size=100))
@hyp_settings(deadline=None)
def test_embed_single_returns_correct_dims(text):
    """embed() returns vector of correct dimensions (mocked)."""
    svc = EmbeddingService(model_name="granite-embedding-small")
    dims = svc.config["dimensions"]

    mock_model = MagicMock()
    mock_model.encode.return_value = np.array([[0.5] * dims])
    svc._model = mock_model
    svc._model_loaded = True

    result = svc.embed(text)
    assert len(result) == dims


@given(model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())))
@hyp_settings(deadline=None)
def test_unload_resets_state(model_name):
    """After unload(), model is cleared and _model_loaded is False."""
    svc = EmbeddingService(model_name=model_name)
    svc._model = MagicMock()
    svc._model_loaded = True

    svc.unload()
    assert svc._model is None
    assert svc._model_loaded is False


@given(
    model_name=st.sampled_from(sorted(EMBEDDING_MODELS.keys())),
    batch_size=st.integers(min_value=1, max_value=128),
)
@hyp_settings(deadline=None)
def test_batch_size_clamped(model_name, batch_size):
    """Batch size is clamped to max_batch_size."""
    svc = EmbeddingService(model_name=model_name, batch_size=batch_size)
    assert svc.batch_size == batch_size
    assert svc.max_batch_size == 128


def test_unsupported_model_raises_error():
    """Unsupported model names raise ConfigurationError."""
    from nexus_mcp.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Unsupported embedding model"):
        EmbeddingService(model_name="my-custom-model/v1")


# ===========================================================================
# Slow integration tests (real model loading)
# ===========================================================================


def _requires_onnx(model_name):
    """Check if a model requires ONNX backend (and thus optimum)."""
    return EMBEDDING_MODELS.get(model_name, {}).get("backend") == "onnx"


@pytest.mark.slow
@pytest.mark.parametrize("model_name", sorted(EMBEDDING_MODELS.keys()))
def test_model_loads_and_embeds(model_name):
    """Each registered model can load and produce embeddings with correct dimensions."""
    if _requires_onnx(model_name) and not _has_optimum:
        pytest.skip("optimum not installed (required for ONNX backend)")
    reset_embedding_service()
    try:
        svc = get_embedding_service(model_name)
        vec = svc.embed("hello world")
        assert len(vec) == EMBEDDING_MODELS[model_name]["dimensions"]
    finally:
        reset_embedding_service()


@pytest.mark.slow
@pytest.mark.parametrize("model_name", sorted(EMBEDDING_MODELS.keys()))
def test_normalized_embeddings_unit_length(model_name):
    """When normalize=True (default), embeddings should have approximately unit length."""
    if _requires_onnx(model_name) and not _has_optimum:
        pytest.skip("optimum not installed (required for ONNX backend)")
    reset_embedding_service()
    try:
        svc = get_embedding_service(model_name)
        vec = svc.embed("def hello(): return 42")
        norm = sum(x ** 2 for x in vec) ** 0.5
        assert abs(norm - 1.0) < 0.01, f"Expected unit norm, got {norm}"
    finally:
        reset_embedding_service()


@pytest.mark.slow
@pytest.mark.parametrize("model_name", sorted(EMBEDDING_MODELS.keys()))
def test_batch_matches_individual(model_name):
    """Batch embedding results should closely match individual embeddings."""
    if _requires_onnx(model_name) and not _has_optimum:
        pytest.skip("optimum not installed (required for ONNX backend)")
    reset_embedding_service()
    try:
        texts = ["hello world", "def foo(): pass", "import os"]
        svc = get_embedding_service(model_name)
        batch_results = svc.embed_batch(texts)
        for text, batch_vec in zip(texts, batch_results):
            # Clear cache to get fresh individual result
            svc._embed_cached.cache_clear()
            single_vec = svc.embed(text)
            for a, b in zip(single_vec, batch_vec):
                assert abs(a - b) < 1e-4, f"Mismatch for model {model_name}"
    finally:
        reset_embedding_service()


@pytest.mark.slow
@pytest.mark.skipif(not _has_optimum, reason="optimum not installed (required for ONNX backend)")
@given(
    text=st.text(
        min_size=1,
        max_size=200,
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    )
)
@hyp_settings(max_examples=5, deadline=None)
def test_hypothesis_normalized_unit_length(text):
    """Property: any non-empty text produces a unit-norm embedding (using lightest model)."""
    svc = get_embedding_service("granite-embedding-small")
    vec = svc.embed(text)
    norm = sum(x ** 2 for x in vec) ** 0.5
    assert abs(norm - 1.0) < 0.01


@pytest.mark.slow
@pytest.mark.skipif(not _has_optimum, reason="optimum not installed (required for ONNX backend)")
@given(
    texts=st.lists(
        st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N"))),
        min_size=1,
        max_size=5,
    )
)
@hyp_settings(max_examples=3, deadline=None)
def test_hypothesis_batch_count_matches(texts):
    """Property: embed_batch returns exactly len(texts) vectors."""
    svc = get_embedding_service("granite-embedding-small")
    results = svc.embed_batch(texts)
    assert len(results) == len(texts)
