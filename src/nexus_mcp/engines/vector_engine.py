"""LanceDB vector engine for code chunk storage and search.

Implements IEngine interface with LanceDB as the backend.
Uses mmap for disk-backed vectors (~20-50MB RAM overhead).
"""

import gc
import logging
import threading
from typing import Any, Dict, List, Optional

import lancedb
import pyarrow as pa

from nexus_mcp.core.interfaces import IEngine
from nexus_mcp.indexing.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def _make_schema(vector_dims: int) -> pa.Schema:
    """Build the PyArrow schema for the chunks table."""
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), vector_dims)),
        pa.field("text", pa.string()),
        pa.field("filepath", pa.string()),
        pa.field("symbol_name", pa.string()),
        pa.field("symbol_type", pa.string()),
        pa.field("language", pa.string()),
        pa.field("line_start", pa.int32()),
        pa.field("line_end", pa.int32()),
        pa.field("signature", pa.string()),
        pa.field("parent", pa.string()),
        pa.field("docstring", pa.string()),
    ])


def _escape_sql(value: str) -> str:
    """Escape single quotes for LanceDB SQL-style filter expressions."""
    return value.replace("'", "''")


class LanceDBVectorEngine(IEngine):
    """Vector search engine backed by LanceDB.

    Thread-safe for concurrent reads. Write operations (add, delete,
    upsert, clear) are serialized via lock. Uses flat search (no IVF
    index) which is appropriate for codebases up to ~100K chunks.
    """

    def __init__(
        self,
        db_path: str,
        embedding_service: EmbeddingService,
        table_name: str = "chunks",
        vector_dims: Optional[int] = None,
    ):
        self._db_path = str(db_path)
        self._embedding_service = embedding_service
        self._table_name = table_name
        self._vector_dims = vector_dims or 768  # jina-code default
        self._lock = threading.RLock()
        self._db = lancedb.connect(self._db_path)
        self._table = None

    def _get_or_create_table(self):
        """Lazily open or create the chunks table."""
        if self._table is not None:
            return self._table

        with self._lock:
            if self._table is not None:
                return self._table

            tables = self._db.list_tables()
            table_names = tables.tables if hasattr(tables, "tables") else list(tables)

            if self._table_name in table_names:
                self._table = self._db.open_table(self._table_name)
            else:
                schema = _make_schema(self._vector_dims)
                self._table = self._db.create_table(self._table_name, schema=schema)

            return self._table

    def add(self, items: List[Dict[str, Any]]) -> None:
        """Add chunk dicts to the table."""
        if not items:
            return
        with self._lock:
            table = self._get_or_create_table()
            table.add(items)

    def search(
        self, query: str, limit: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Search for chunks matching query text.

        Args:
            query: Search text (will be embedded).
            limit: Max results.
            **kwargs: Optional filters — language, symbol_type.

        Returns:
            List of result dicts with score field added.
        """
        table = self._get_or_create_table()
        if table.count_rows() == 0:
            return []

        vector = self._embedding_service.embed(query, is_query=True)
        search_builder = table.search(vector).limit(limit)

        # Apply filters (values are escaped to prevent injection)
        filters = []
        if kwargs.get("language"):
            filters.append(f"language = '{_escape_sql(kwargs['language'])}'")
        if kwargs.get("symbol_type"):
            filters.append(f"symbol_type = '{_escape_sql(kwargs['symbol_type'])}'")
        if filters:
            search_builder = search_builder.where(" AND ".join(filters))

        results = search_builder.to_list()

        # Convert _distance to score and clean up
        for r in results:
            distance = r.pop("_distance", 0.0)
            r["score"] = 1.0 / (1.0 + distance)

        return results

    def delete(self, ids: List[str]) -> None:
        """Delete chunks by ID."""
        if not ids:
            return
        with self._lock:
            table = self._get_or_create_table()
            quoted = ", ".join(f"'{_escape_sql(id_)}'" for id_ in ids)
            table.delete(f"id IN ({quoted})")

    def delete_by_filepath(self, filepath: str) -> None:
        """Delete all chunks from a specific file (for incremental reindex)."""
        with self._lock:
            table = self._get_or_create_table()
            table.delete(f"filepath = '{_escape_sql(filepath)}'")

    def upsert(self, items: List[Dict[str, Any]]) -> None:
        """Insert or update chunks by ID."""
        if not items:
            return
        with self._lock:
            table = self._get_or_create_table()
            (
                table.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(items)
            )

    def count(self) -> int:
        """Return total chunk count."""
        table = self._get_or_create_table()
        return table.count_rows()

    def validate(self) -> bool:
        """Validate that the chunks table exists and has the expected schema.

        Returns True if valid, False if corrupt or missing.
        """
        try:
            tables = self._db.list_tables()
            table_names = tables.tables if hasattr(tables, "tables") else list(tables)

            if self._table_name not in table_names:
                return False

            table = self._db.open_table(self._table_name)
            expected_schema = _make_schema(self._vector_dims)
            expected_names = set(expected_schema.names)
            actual_names = set(table.schema.names)

            if not expected_names.issubset(actual_names):
                logger.warning(
                    "Schema mismatch: expected %s, got %s",
                    sorted(expected_names), sorted(actual_names),
                )
                return False

            return True
        except Exception as e:
            logger.warning("Index validation failed: %s", e)
            return False

    def clear(self) -> None:
        """Drop and recreate the table."""
        with self._lock:
            tables = self._db.list_tables()
            table_names = tables.tables if hasattr(tables, "tables") else list(tables)

            if self._table_name in table_names:
                self._db.drop_table(self._table_name)

            schema = _make_schema(self._vector_dims)
            self._table = self._db.create_table(self._table_name, schema=schema)
            gc.collect()
