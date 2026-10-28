"""jina-code is deprecated: it still loads, it warns, and nothing advertises it any more."""

import json
import logging
from pathlib import Path

import pytest
import yaml

from nexus_mcp.config import reset_settings
from nexus_mcp.indexing import embedding_service as es

ROOT = Path(__file__).resolve().parent.parent


class _StopLoading(Exception):
    """Raised by the fake model so a test never downloads real weights."""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_settings()
    es.reset_embedding_service()

    class _Fake:
        def __init__(self, *a, **k):
            raise _StopLoading

    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", _Fake)
    yield
    reset_settings()
    es.reset_embedding_service()


def test_registry_marks_jina_code_deprecated_and_not_the_default():
    assert es.EMBEDDING_MODELS["jina-code"]["deprecated"]
    assert "deprecated" not in es.EMBEDDING_MODELS[es.DEFAULT_MODEL]


def test_loading_jina_code_logs_the_deprecation(caplog):
    svc = es.EmbeddingService(model_name="jina-code")
    with caplog.at_level(logging.WARNING, logger=es.logger.name), pytest.raises(_StopLoading):
        svc._load_model()
    assert any("jina-code is deprecated" in r.message for r in caplog.records)


def test_loading_the_default_model_logs_no_deprecation(caplog):
    svc = es.EmbeddingService(model_name=es.DEFAULT_MODEL)
    with caplog.at_level(logging.WARNING, logger=es.logger.name), pytest.raises(_StopLoading):
        svc._load_model()
    assert not any("deprecated" in r.message for r in caplog.records)


def test_jina_code_still_resolves_so_existing_indexes_keep_working():
    assert es.model_dimensions("jina-code") == 768
    es.EmbeddingService(model_name="jina-code")  # must not raise


def test_hosted_manifests_no_longer_offer_jina_code():
    smithery = yaml.safe_load((ROOT / "smithery.yaml").read_text())
    assert "jina-code" not in json.dumps(smithery)
    assert "jina-code" not in (ROOT / "glama.json").read_text()


def test_the_eval_candidates_no_longer_include_jina_code():
    from evals.retrieval import candidates

    assert "jina-code" not in candidates.CANDIDATES
