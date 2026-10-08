"""Embedding-model candidates for the retrieval eval.

Only `bge-small-en` is recommended in nexus_mcp.indexing.embedding_service (`jina-code`
also ships but is deprecated and is not benchmarked).
The other entries exist only inside this eval, so a trial never changes the shipped
registry. Each entry has the same keys as a registry entry, plus `approx_mb`, the
size of the ONNX file that gets downloaded (from the Hugging Face file listing,
2026-10-03).

All candidates are Apache-2.0 or MIT and need no trust_remote_code.
"""

from typing import Any, Dict, Optional

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# None means "use the entry that already ships in the registry".
CANDIDATES: Dict[str, Optional[Dict[str, Any]]] = {
    "bge-small-en": None,
    "bge-small-en-int8": {
        "hf_name": "Xenova/bge-small-en-v1.5",
        "dimensions": 384,
        "max_seq_length": 512,
        "trust_remote_code": False,
        "prompt_prefix": "",
        "query_prefix": BGE_QUERY_PREFIX,
        "backend": "onnx",
        "onnx_file": "onnx/model_quantized.onnx",
        "approx_mb": 34,
    },
    "granite-97m-r2-int8": {
        "hf_name": "ibm-granite/granite-embedding-97m-multilingual-r2",
        "dimensions": 384,
        "max_seq_length": 512,
        "trust_remote_code": False,
        "prompt_prefix": "",
        "query_prefix": "",
        "backend": "onnx",
        "onnx_file": "onnx/model_quint8_avx2.onnx",
        "approx_mb": 98,
    },
    "gte-modernbert-int8": {
        "hf_name": "Alibaba-NLP/gte-modernbert-base",
        "dimensions": 768,
        "max_seq_length": 512,
        "trust_remote_code": False,
        "prompt_prefix": "",
        "query_prefix": "",
        "backend": "onnx",
        "onnx_file": "onnx/model_int8.onnx",
        "approx_mb": 150,
    },
}


def register(name: str, registry: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Add the candidate to `registry` if it is eval-only. Returns its config.

    Raises KeyError for a name that is neither a candidate nor in the registry.
    """
    if name not in CANDIDATES:
        raise KeyError(f"Unknown candidate {name!r}. Known: {', '.join(CANDIDATES)}")
    config = CANDIDATES[name]
    if config is not None:
        registry[name] = {k: v for k, v in config.items() if k != "approx_mb"}
    return registry[name]
