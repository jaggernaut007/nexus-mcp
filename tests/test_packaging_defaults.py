"""The embedding model that each packaging manifest defaults to must match the code.

Issue #7: Dockerfile, smithery.yaml and glama.json defaulted to jina-code while the
package defaulted to bge-small-en. jina-code needs trust_remote_code and a 612 MiB
ONNX download, so the hosted builds were the heavy ones.
"""

import json
import re
from pathlib import Path

from nexus_mcp.config import Settings

ROOT = Path(__file__).resolve().parent.parent
CODE_DEFAULT = Settings().embedding_model


def test_dockerfile_embedding_model_matches_code_default():
    text = (ROOT / "Dockerfile").read_text()
    match = re.search(r"NEXUS_EMBEDDING_MODEL=(\S+)", text)
    assert match and match.group(1) == CODE_DEFAULT


def test_glama_embedding_model_matches_code_default():
    data = json.loads((ROOT / "glama.json").read_text())
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "NEXUS_EMBEDDING_MODEL" in node and isinstance(node["NEXUS_EMBEDDING_MODEL"], dict):
                found.append(node["NEXUS_EMBEDDING_MODEL"].get("default"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    assert found == [CODE_DEFAULT]


def test_stdio_dockerfile_embedding_model_matches_code_default():
    text = (ROOT / "docker" / "Dockerfile.stdio").read_text()
    match = re.search(r"NEXUS_EMBEDDING_MODEL=(\S+)", text)
    assert match and match.group(1) == CODE_DEFAULT


def test_desktop_extension_does_not_override_the_embedding_model():
    # Smithery and Claude Desktop share one .mcpb bundle (smithery.yaml no longer applies to a
    # local server). The bundle must leave the model to the package default.
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    env = manifest["server"]["mcp_config"]["env"]
    assert "NEXUS_EMBEDDING_MODEL" not in env
