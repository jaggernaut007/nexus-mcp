import asyncio
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import json

import nexus_mcp.server as server_module
from nexus_mcp.state import reset_state

def _mock_embedding_service():
    dims = 384
    svc = MagicMock()
    svc.embed.return_value = [0.1] * dims
    svc.embed_batch.return_value = [[0.1] * dims] * 10
    return svc

async def _call_tool(mcp, name, args=None):
    result = await mcp.call_tool(name, args or {})
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return {}

async def reproduce():
    tmp_path = Path("repro_tmp")
    tmp_path.mkdir(exist_ok=True)
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "parser.py").write_text('def parse_tokens(text): return text.split()')

    storage = tmp_path / "storage"
    storage.mkdir(exist_ok=True)

    reset_state()

    with patch("nexus_mcp.indexing.pipeline.get_embedding_service") as mock_get, \
         patch.dict("os.environ", {"NEXUS_STORAGE_DIR": str(storage)}):
        from nexus_mcp.config import reset_settings
        reset_settings()

        mock_svc = _mock_embedding_service()
        mock_get.return_value = mock_svc

        mcp = server_module.create_server()
        print("Indexing...")
        await _call_tool(mcp, "index", {"path": str(tmp_path)})

        from nexus_mcp.state import get_state
        state = get_state()
        state.vector_engine._embedding_service = mock_svc

        print("Searching...")
        result = await _call_tool(mcp, "search", {
            "query": "parse",
            "language": "python",
        })

        print(f"Total results: {result.get('total')}")
        for r in result.get("results", []):
            print(f"DEBUG: {json.dumps(r)}")
            print(f"File: {r.get('filepath')}, Language: {r.get('language')}, Absolute Path: {r.get('absolute_path')}")
            if r.get("language") == "unknown":
                print("Found unknown language!")

if __name__ == "__main__":
    asyncio.run(reproduce())
