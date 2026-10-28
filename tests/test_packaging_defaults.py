"""The embedding model that each packaging manifest defaults to must match the code.

Issue #7: Dockerfile, smithery.yaml and glama.json defaulted to jina-code while the
package defaulted to bge-small-en. jina-code needs trust_remote_code and a 612 MiB
ONNX download, so the hosted builds were the heavy ones.
"""

import json
import re
from pathlib import Path

import yaml

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


def test_smithery_embedding_model_matches_code_default():
    data = yaml.safe_load((ROOT / "smithery.yaml").read_text())
    props = data["startCommand"]["configSchema"]["properties"]
    assert props["embedding_model"]["default"] == CODE_DEFAULT
    assert f"config.embedding_model || '{CODE_DEFAULT}'" in (ROOT / "smithery.yaml").read_text()
