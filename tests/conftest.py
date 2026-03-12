"""Shared test fixtures and helpers for Nexus-MCP tests."""

import json
from unittest.mock import MagicMock, patch

import pytest

import nexus_mcp.server as server_module
from nexus_mcp.state import reset_state


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state before each test."""
    reset_state()
    server_module._pipeline = None
    yield
    reset_state()
    server_module._pipeline = None


@pytest.fixture
def mini_codebase(tmp_path):
    """Create a small Python codebase."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        'def hello():\n    """Say hello."""\n    print("hello")\n'
    )
    (src / "utils.py").write_text(
        'def helper():\n    """A helper."""\n    return 42\n'
    )
    return tmp_path


@pytest.fixture
def mini_codebase_with_calls(tmp_path):
    """Create a Python codebase with call relationships for graph tests."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        'from utils import helper\n\n\ndef hello():\n    """Say hello."""\n    helper()\n'
    )
    (src / "utils.py").write_text(
        'def helper():\n    """A helper."""\n    return 42\n'
    )
    (src / "app.py").write_text(
        "from main import hello\nfrom utils import helper\n\n\n"
        "def orchestrate():\n"
        '    """Orchestrate calls."""\n'
        "    hello()\n"
        "    helper()\n"
    )
    return tmp_path


def _mock_embedding_service():
    svc = MagicMock()
    svc.embed.return_value = [0.1] * 768
    def dynamic_batch(texts, **kwargs):
        return [[0.1] * 768 for _ in texts]
    svc.embed_batch.side_effect = dynamic_batch
    return svc


async def _call_tool(mcp, name, args=None):
    """Call an MCP tool and return the structured result."""
    result = await mcp.call_tool(name, args or {})
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    # Fallback: parse JSON from text content
    for content in result.content:
        if hasattr(content, "text"):
            data = json.loads(content.text)
            return data.get("result", data) if isinstance(data, dict) else data
    return {}


async def _setup_indexed(codebase_path, storage_dir):
    """Index a codebase and return (mcp, mock_svc, result)."""
    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as mock_get, \
         patch.dict("os.environ", {"NEXUS_STORAGE_DIR": str(storage_dir)}):
        from nexus_mcp.config import reset_settings
        reset_settings()

        mock_svc = _mock_embedding_service()
        mock_get.return_value = mock_svc

        mcp = server_module.create_server()
        result = await _call_tool(mcp, "index", {"path": str(codebase_path)})

        # Patch vector engine's embedding service for search
        from nexus_mcp.state import get_state
        state = get_state()
        if state.vector_engine:
            state.vector_engine._embedding_service = mock_svc

        reset_settings()
        return mcp, mock_svc, result
