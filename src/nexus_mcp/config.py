"""Configuration for Nexus-MCP with NEXUS_ env prefix."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Settings:
    """Nexus-MCP configuration. All settings can be overridden via NEXUS_ env vars."""

    # Storage
    storage_dir: str = ".nexus"

    # Embedding model
    embedding_model: str = "bge-small-en"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32

    # Indexing
    max_file_size_mb: int = 10
    max_workers: Optional[int] = None
    chunk_max_chars: int = 4000

    # Graph
    graph_max_depth: int = 10

    # Search
    search_mode: str = "hybrid"  # "hybrid", "vector", "bm25"
    reranker_model: str = "ms-marco-MiniLM-L-12-v2"
    fusion_weight_vector: float = 0.5
    fusion_weight_bm25: float = 0.3
    fusion_weight_graph: float = 0.2

    # Memory limits
    max_memory_mb: int = 350

    # Logging
    log_level: str = "INFO"
    log_format: str = "text"  # "text" or "json"

    # Security
    trust_remote_code: bool = False
    auth_mode: str = "none"  # "none", "local", "oauth" (oauth deferred)
    default_permission_level: str = "full"  # "read" or "full"

    # Audit
    audit_enabled: bool = True
    audit_log_file: str = ""  # empty = stderr

    # Rate limiting
    rate_limit_enabled: bool = False
    rate_limit_default_rate: float = 10.0  # requests per second
    rate_limit_default_burst: int = 20

    def __post_init__(self):
        """Override settings from NEXUS_ environment variables."""
        def _bool(v):
            return v.lower() in ("true", "1", "yes")
        env_map = {
            "NEXUS_STORAGE_DIR": ("storage_dir", str),
            "NEXUS_EMBEDDING_MODEL": ("embedding_model", str),
            "NEXUS_EMBEDDING_DEVICE": ("embedding_device", str),
            "NEXUS_EMBEDDING_BATCH_SIZE": ("embedding_batch_size", int),
            "NEXUS_MAX_FILE_SIZE_MB": ("max_file_size_mb", int),
            "NEXUS_MAX_WORKERS": ("max_workers", lambda v: int(v) if v else None),
            "NEXUS_CHUNK_MAX_CHARS": ("chunk_max_chars", int),
            "NEXUS_GRAPH_MAX_DEPTH": ("graph_max_depth", int),
            "NEXUS_SEARCH_MODE": ("search_mode", str),
            "NEXUS_RERANKER_MODEL": ("reranker_model", str),
            "NEXUS_FUSION_WEIGHT_VECTOR": ("fusion_weight_vector", float),
            "NEXUS_FUSION_WEIGHT_BM25": ("fusion_weight_bm25", float),
            "NEXUS_FUSION_WEIGHT_GRAPH": ("fusion_weight_graph", float),
            "NEXUS_MAX_MEMORY_MB": ("max_memory_mb", int),
            "NEXUS_LOG_LEVEL": ("log_level", str),
            "NEXUS_LOG_FORMAT": ("log_format", str),
            "NEXUS_TRUST_REMOTE_CODE": ("trust_remote_code", _bool),
            "NEXUS_AUTH_MODE": ("auth_mode", str),
            "NEXUS_PERMISSION_LEVEL": ("default_permission_level", str),
            "NEXUS_AUDIT_ENABLED": ("audit_enabled", _bool),
            "NEXUS_AUDIT_LOG_FILE": ("audit_log_file", str),
            "NEXUS_RATE_LIMIT_ENABLED": ("rate_limit_enabled", _bool),
            "NEXUS_RATE_LIMIT_DEFAULT_RATE": ("rate_limit_default_rate", float),
            "NEXUS_RATE_LIMIT_DEFAULT_BURST": ("rate_limit_default_burst", int),
        }

        for env_key, (attr, converter) in env_map.items():
            value = os.environ.get(env_key)
            if value is not None:
                try:
                    setattr(self, attr, converter(value))
                except (ValueError, TypeError):
                    pass  # Keep default on conversion error

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir)

    @property
    def lancedb_path(self) -> Path:
        return self.storage_path / "lancedb"

    @property
    def graph_path(self) -> Path:
        return self.storage_path / "graph.db"


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create singleton settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset settings (for testing)."""
    global _settings
    _settings = None
