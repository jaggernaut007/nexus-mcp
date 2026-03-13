"""Tests for the indexing pipeline."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus_mcp.config import Settings
from nexus_mcp.indexing.pipeline import (
    IndexingPipeline,
    IndexResult,
    discover_files,
)

# --- Fixtures ---

@pytest.fixture
def mini_codebase(tmp_path):
    """Create a small Python codebase for testing."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "main.py").write_text(
        'def hello():\n    """Say hello."""\n    print("hello")\n\n'
        'def goodbye():\n    """Say goodbye."""\n    print("bye")\n'
    )
    (src / "utils.py").write_text(
        'import os\n\ndef get_path():\n    """Get cwd."""\n    return os.getcwd()\n'
    )
    return tmp_path


@pytest.fixture
def settings(tmp_path):
    """Create Settings pointing to tmp storage."""
    return Settings(storage_dir=str(tmp_path / ".nexus"))


def _mock_embedding_service(dims=384):
    """Create a mock embedding service."""
    svc = MagicMock()
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.return_value = [[0.1] * dims]
    return svc


def _make_pipeline(settings):
    """Create pipeline with mocked embedding service."""
    from nexus_mcp.indexing.embedding_service import EMBEDDING_MODELS
    dims = EMBEDDING_MODELS.get(settings.embedding_model, {}).get("dimensions", 384)
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as mock_get:
        mock_svc = _mock_embedding_service(dims)
        mock_get.return_value = mock_svc
        pipeline = IndexingPipeline(settings)
        # Patch the embedding service on the vector engine too
        pipeline._vector_engine._embedding_service = mock_svc
        # Make embed_batch return correct number of vectors
        def dynamic_batch(texts, **kwargs):
            return [[0.1] * dims for _ in texts]
        mock_svc.embed_batch.side_effect = dynamic_batch
        return pipeline


# --- discover_files tests ---

class TestDiscoverFiles:
    def test_finds_python_files(self, mini_codebase, settings):
        files = discover_files(mini_codebase, settings)
        py_files = [f for f in files if f.suffix == ".py"]
        assert len(py_files) == 2

    def test_skips_hidden_dirs(self, tmp_path, settings):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x = 1")
        (tmp_path / "visible.py").write_text("y = 2")

        files = discover_files(tmp_path, settings)
        names = [f.name for f in files]
        assert "visible.py" in names
        assert "secret.py" not in names

    def test_skips_node_modules(self, tmp_path, settings):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module.exports = {}")
        (tmp_path / "app.js").write_text("const x = 1;")

        files = discover_files(tmp_path, settings)
        names = [f.name for f in files]
        assert "app.js" in names
        assert "pkg.js" not in names

    def test_skips_unsupported_extensions(self, tmp_path, settings):
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "code.py").write_text("x = 1")

        files = discover_files(tmp_path, settings)
        names = [f.name for f in files]
        assert "code.py" in names
        assert "data.csv" not in names

    def test_skips_large_files(self, tmp_path, settings):
        settings.max_file_size_mb = 0  # 0 MB = skip everything
        (tmp_path / "big.py").write_text("x = 1")

        files = discover_files(tmp_path, settings)
        assert len(files) == 0

    def test_respects_gitignore(self, tmp_path, settings):
        (tmp_path / ".gitignore").write_text("ignored.py\n")
        (tmp_path / "ignored.py").write_text("x = 1")
        (tmp_path / "kept.py").write_text("y = 2")

        files = discover_files(tmp_path, settings)
        names = [f.name for f in files]
        assert "kept.py" in names
        assert "ignored.py" not in names

    def test_empty_directory(self, tmp_path, settings):
        files = discover_files(tmp_path, settings)
        assert files == []

    def test_returns_sorted(self, tmp_path, settings):
        (tmp_path / "z.py").write_text("z = 1")
        (tmp_path / "a.py").write_text("a = 1")
        (tmp_path / "m.py").write_text("m = 1")

        files = discover_files(tmp_path, settings)
        names = [f.name for f in files]
        assert names == sorted(names)


# --- IndexResult tests ---

class TestIndexResult:
    def test_to_dict(self):
        result = IndexResult(total_files=5, total_symbols=10, total_chunks=8)
        d = result.to_dict()
        assert d["total_files"] == 5
        assert d["total_symbols"] == 10
        assert d["total_chunks"] == 8
        assert "time_seconds" in d


# --- IndexingPipeline tests ---

class TestIndexingPipeline:
    def test_index_populates_vector_engine(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        result = pipeline.index(mini_codebase)

        assert result.total_files >= 2
        assert result.total_symbols > 0
        assert result.total_chunks > 0
        assert pipeline.vector_engine.count() > 0

    def test_index_populates_graph_engine(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        result = pipeline.index(mini_codebase)

        assert result.graph_nodes > 0
        stats = pipeline.graph_engine.get_statistics()
        assert stats["total_nodes"] > 0

    def test_index_saves_metadata(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        pipeline.index(mini_codebase)

        metadata_path = Path(settings.storage_dir) / "index_metadata.json"
        assert metadata_path.exists()
        data = json.loads(metadata_path.read_text())
        assert "mtimes" in data
        assert len(data["mtimes"]) >= 2

    def test_index_result_has_timing(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        result = pipeline.index(mini_codebase)
        assert result.time_seconds > 0

    def test_index_unloads_model(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        pipeline.index(mini_codebase)
        pipeline._embedding_service.unload.assert_called()

    def test_index_empty_codebase(self, tmp_path, settings):
        pipeline = _make_pipeline(settings)
        result = pipeline.index(tmp_path)
        assert result.total_files == 0
        assert result.total_chunks == 0

    def test_incremental_detects_new_file(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        pipeline.index(mini_codebase)

        # Add a new file
        (mini_codebase / "src" / "new.py").write_text(
            "def new_func():\n    pass\n"
        )
        # Ensure mtime is different
        time.sleep(0.05)

        result = pipeline.incremental_index(mini_codebase)
        assert result.files_added >= 1

    def test_incremental_detects_deleted_file(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        pipeline.index(mini_codebase)

        # Delete a file
        (mini_codebase / "src" / "utils.py").unlink()

        result = pipeline.incremental_index(mini_codebase)
        assert result.files_deleted >= 1

    def test_incremental_no_changes(self, mini_codebase, settings):
        pipeline = _make_pipeline(settings)
        pipeline.index(mini_codebase)

        result = pipeline.incremental_index(mini_codebase)
        assert result.files_added == 0
        assert result.files_modified == 0
        assert result.files_deleted == 0

    def test_incremental_falls_back_to_full(self, mini_codebase, settings):
        """If no metadata exists, incremental falls back to full index."""
        pipeline = _make_pipeline(settings)
        result = pipeline.incremental_index(mini_codebase)
        # Should still have indexed files
        assert result.total_files >= 2
