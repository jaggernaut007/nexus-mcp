"""Embedding service with ONNX Runtime support.

Ported from CodeGrok MCP. Default: bge-small-en (50MB).
CodeRankEmbed available as opt-in. ONNX Runtime replaces PyTorch.
"""

import gc
import logging
import threading
from functools import lru_cache
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Supported embedding models
EMBEDDING_MODELS = {
    "bge-small-en": {
        "hf_name": "BAAI/bge-small-en-v1.5",
        "dimensions": 384,
        "max_seq_length": 512,
        "trust_remote_code": False,
        "prompt_prefix": "",
        "query_prefix": "Represent this sentence for searching relevant passages: ",
    },
    "coderankembed": {
        "hf_name": "nomic-ai/CodeRankEmbed",
        "dimensions": 768,
        "max_seq_length": 8192,
        "trust_remote_code": True,
        "prompt_prefix": "",
        "query_prefix": "Represent this query for searching relevant code: ",
    },
}

DEFAULT_MODEL = "bge-small-en"


class EmbeddingService:
    """Embedding service with lazy model loading and batch processing."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: Optional[str] = None,
        batch_size: int = 32,
        max_batch_size: int = 128,
        normalize: bool = True,
        cache_dir: Optional[str] = None,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_batch_size = max_batch_size
        self.normalize = normalize
        self.cache_dir = cache_dir

        if model_name in EMBEDDING_MODELS:
            self.config = EMBEDDING_MODELS[model_name].copy()
        else:
            self.config = {
                "hf_name": model_name,
                "dimensions": None,
                "max_seq_length": 512,
                "trust_remote_code": False,
                "prompt_prefix": "",
                "query_prefix": "",
            }

        self.device = device or "cpu"
        self._lock = threading.Lock()
        self._model = None
        self._model_loaded = False

        self.stats = {
            "total_embeddings": 0,
            "total_batches": 0,
            "total_time": 0.0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

        self._embed_cached = lru_cache(maxsize=1000)(self._embed_single_uncached)

    def _load_model(self):
        if self._model_loaded:
            return
        with self._lock:
            if self._model_loaded:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers required. Install: pip install sentence-transformers"
                )

            kwargs = {"device": self.device}
            if self.cache_dir:
                kwargs["cache_folder"] = self.cache_dir
            if self.config.get("trust_remote_code"):
                from nexus_mcp.config import get_settings
                from nexus_mcp.core.exceptions import ConfigurationError

                if not get_settings().trust_remote_code:
                    raise ConfigurationError(
                        f"Model '{self.model_name}' requires trust_remote_code=True, "
                        f"which allows arbitrary code execution from HuggingFace. "
                        f"Set NEXUS_TRUST_REMOTE_CODE=true to accept this risk."
                    )
                kwargs["trust_remote_code"] = True

            import io
            import sys
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
            try:
                self._model = SentenceTransformer(self.config["hf_name"], **kwargs)
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

            if self.config["dimensions"] is None:
                self.config["dimensions"] = self._model.get_sentence_embedding_dimension()
            self._model_loaded = True

    @property
    def dimensions(self) -> int:
        self._load_model()
        return self.config["dimensions"]

    def _embed_single_uncached(self, text: str, is_query: bool) -> tuple:
        self._load_model()
        prefix = self.config["query_prefix"] if is_query else self.config["prompt_prefix"]
        if prefix:
            text = prefix + text
        embeddings = self._model.encode(
            [text],
            show_progress_bar=False,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
        )
        return tuple(embeddings[0].tolist())

    def embed(self, text: str, is_query: bool = False) -> List[float]:
        """Embed single text with LRU caching."""
        info_before = self._embed_cached.cache_info()
        result = self._embed_cached(text, is_query)
        info_after = self._embed_cached.cache_info()
        if info_after.hits > info_before.hits:
            self.stats["cache_hits"] += 1
        else:
            self.stats["cache_misses"] += 1
            self.stats["total_embeddings"] += 1
        return list(result)

    def embed_batch(
        self, texts: List[str], is_query: bool = False, batch_size: Optional[int] = None
    ) -> List[List[float]]:
        """Embed batch of texts."""
        import time

        if not texts:
            return []
        self._load_model()

        bs = min(batch_size or self.batch_size, self.max_batch_size)
        prefix = self.config["query_prefix"] if is_query else self.config["prompt_prefix"]
        if prefix:
            texts = [prefix + t for t in texts]

        start = time.time()
        embeddings = self._model.encode(
            texts,
            batch_size=bs,
            show_progress_bar=False,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
        )
        elapsed = time.time() - start

        self.stats["total_embeddings"] += len(texts)
        self.stats["total_batches"] += (len(texts) + bs - 1) // bs
        self.stats["total_time"] += elapsed

        if self.stats["total_batches"] % 100 == 0:
            gc.collect()

        return embeddings.tolist()

    def get_stats(self) -> dict:
        stats = self.stats.copy()
        stats["embeddings_per_second"] = (
            stats["total_embeddings"] / stats["total_time"]
            if stats["total_time"] > 0
            else 0
        )
        return stats

    def unload(self):
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                self._model_loaded = False
                self._embed_cached.cache_clear()
                gc.collect()


# Singleton management
_services: Dict[str, EmbeddingService] = {}
_singleton_lock = threading.Lock()


def get_embedding_service(model_name: str = DEFAULT_MODEL, **kwargs) -> EmbeddingService:
    """Get or create singleton embedding service for a model."""
    if model_name not in _services:
        with _singleton_lock:
            if model_name not in _services:
                _services[model_name] = EmbeddingService(model_name, **kwargs)
    return _services[model_name]


def reset_embedding_service(model_name: Optional[str] = None):
    """Reset embedding service(s)."""
    with _singleton_lock:
        if model_name is None:
            for svc in _services.values():
                svc.unload()
            _services.clear()
        elif model_name in _services:
            _services[model_name].unload()
            del _services[model_name]
