"""Tests for how EmbeddingService loads a model, and for model-change detection.

The SentenceTransformer class is replaced by a recorder, because these tests check
the arguments the service passes, not the model weights. Model-change detection uses
the real pipeline and a real index on disk.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.indexing import embedding_service as es
from nexus_mcp.indexing.pipeline import IndexingPipeline


class _RecordingModel:
    last_args = None
    last_kwargs = None

    def __init__(self, name, **kwargs):
        type(self).last_args = (name,)
        type(self).last_kwargs = kwargs
        self.max_seq_length = 8192

    def get_sentence_embedding_dimension(self):
        return 384


@pytest.fixture
def recorder(monkeypatch):
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _RecordingModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    reset_settings()
    yield _RecordingModel
    reset_settings()


@pytest.fixture
def registry(monkeypatch):
    """A copy of the registry that a test may extend without leaking."""
    copy = {name: dict(cfg) for name, cfg in es.EMBEDDING_MODELS.items()}
    monkeypatch.setattr(es, "EMBEDDING_MODELS", copy)
    return copy


def test_load_model_passes_onnx_file_name(recorder, registry):
    registry["quantized"] = {
        "hf_name": "org/model", "dimensions": 384, "max_seq_length": 512,
        "trust_remote_code": False, "prompt_prefix": "", "query_prefix": "",
        "backend": "onnx", "onnx_file": "onnx/model_qint8.onnx",
    }
    svc = es.EmbeddingService("quantized", device="cpu")
    svc._load_model()
    assert recorder.last_kwargs["backend"] == "onnx"
    assert recorder.last_kwargs["model_kwargs"]["file_name"] == "onnx/model_qint8.onnx"
    assert recorder.last_kwargs["model_kwargs"]["provider"] == "CPUExecutionProvider"


def test_load_model_without_onnx_file_leaves_file_name_unset(recorder, registry):
    registry["plain-onnx"] = {
        "hf_name": "org/model", "dimensions": 384, "max_seq_length": 512,
        "trust_remote_code": False, "prompt_prefix": "", "query_prefix": "",
        "backend": "onnx",
    }
    es.EmbeddingService("plain-onnx", device="cpu")._load_model()
    assert "file_name" not in recorder.last_kwargs["model_kwargs"]


def test_load_model_caps_sequence_length_at_registry_value(recorder):
    svc = es.EmbeddingService("bge-small-en", device="cpu")
    svc._load_model()
    assert svc._model.max_seq_length == 512  # the fake model reported 8192


def test_load_model_does_not_raise_sequence_length_above_the_model_limit(recorder, registry):
    registry["short"] = {
        "hf_name": "org/model", "dimensions": 384, "max_seq_length": 99999,
        "trust_remote_code": False, "prompt_prefix": "", "query_prefix": "", "backend": None,
    }
    svc = es.EmbeddingService("short", device="cpu")
    svc._load_model()
    assert svc._model.max_seq_length == 8192


def test_model_dimensions_known_model():
    assert es.model_dimensions("jina-code") == 768
    assert es.model_dimensions("bge-small-en") == 384


def test_model_dimensions_unknown_model_uses_default_width():
    assert es.model_dimensions("not-a-model") == es.model_dimensions(es.DEFAULT_MODEL)


def _pipeline(tmp_path, model):
    settings = Settings(storage_dir=str(tmp_path / ".nexus"), embedding_model=model)
    dims = es.model_dimensions(model)
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as get_svc:
        svc = MagicMock()
        svc.model_name = model
        svc.embed.return_value = [0.1] * dims
        svc.embed_batch.side_effect = lambda texts, **kw: [[0.1] * dims for _ in texts]
        get_svc.return_value = svc
        pipeline = IndexingPipeline(settings)
        pipeline._vector_engine._embedding_service = svc
        return pipeline


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "a.py").write_text("def alpha():\n    return 1\n")
    (root / "b.py").write_text("def beta():\n    return 2\n")
    return root


def test_index_metadata_records_model_and_width(codebase, tmp_path):
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    stored = pipeline._load_metadata_raw()
    assert stored["embedding_model"] == "bge-small-en"
    assert stored["embedding_dimensions"] == 384


def test_changed_model_triggers_full_rebuild(codebase, tmp_path):
    _pipeline(tmp_path, "bge-small-en").index(codebase)

    second = _pipeline(tmp_path, "jina-code")  # same storage, wider vectors
    result = second.incremental_index(codebase)

    assert result.total_files == 2  # a full index ran; an incremental no-op would be 0
    assert second._load_metadata_raw()["embedding_model"] == "jina-code"


def test_same_model_stays_incremental(codebase, tmp_path):
    _pipeline(tmp_path, "bge-small-en").index(codebase)
    result = _pipeline(tmp_path, "bge-small-en").incremental_index(codebase)
    assert result.total_files == 0


def test_index_without_model_keys_is_accepted(codebase, tmp_path):
    """An index written before this check carries no model name and must keep working."""
    pipeline = _pipeline(tmp_path, "bge-small-en")
    pipeline.index(codebase)
    import json

    data = json.loads(pipeline._metadata_path.read_text())
    data.pop("embedding_model")
    data.pop("embedding_dimensions")
    pipeline._metadata_path.write_text(json.dumps(data))

    assert _pipeline(tmp_path, "bge-small-en")._validate_index() is True
