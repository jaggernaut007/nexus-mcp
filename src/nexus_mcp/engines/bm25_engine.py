"""LanceDB full-text search engine using Tantivy FTS.

Read-only consumer of the shared `chunks` table. The vector engine
owns the table lifecycle (create/write/delete). This engine only
creates an FTS index and performs text searches.
"""

import logging
import threading
from typing import Any, Dict, List, Optional

import lancedb

from nexus_mcp.core.interfaces import IEngine

logger = logging.getLogger(__name__)


def _escape_sql(value: str) -> str:
    """Escape single quotes for LanceDB SQL-style filter expressions."""
    return value.replace("'", "''")


class LanceDBBM25Engine(IEngine):
    """Full-text search engine backed by LanceDB Tantivy FTS.

    Shares the same `chunks` table as the vector engine but only reads.
    The add/delete/clear methods are no-ops since the vector engine
    owns the table lifecycle.
    """

    def __init__(
        self,
        db_path: str,
        table_name: str = "chunks",
    ):
        self._db_path = str(db_path)
        self._table_name = table_name
        self._lock = threading.RLock()
        self._db = lancedb.connect(self._db_path)
        self._table: Optional[Any] = None
        self._fts_index_created = False

    def _get_table(self) -> Optional[Any]:
        """Lazily open the chunks table. Returns None if table doesn't exist."""
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
                logger.debug("BM25: table '%s' does not exist yet", self._table_name)
                return None

            return self._table

    def ensure_fts_index(self) -> bool:
        """Create or replace the Tantivy FTS index on the text column.

        Idempotent — safe to call multiple times. Returns True if index
        was created, False if table doesn't exist or index already exists.
        """
        if self._fts_index_created:
            return True

        with self._lock:
            if self._fts_index_created:
                return True

            table = self._get_table()
            if table is None:
                return False

            try:
                table.create_fts_index("text", replace=True)
                self._fts_index_created = True
                logger.info("BM25: FTS index created on '%s.text'", self._table_name)
                return True
            except Exception as e:
                logger.warning("BM25: Failed to create FTS index: %s", e)
                return False

    def search(
        self, query: str, limit: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Full-text search for chunks matching query.

        Args:
            query: Search text.
            limit: Max results.
            **kwargs: Optional filters — language, symbol_type.

        Returns:
            List of result dicts with score field added.
        """
        table = self._get_table()
        if table is None or table.count_rows() == 0:
            return []

        # Ensure FTS index exists before searching
        if not self._fts_index_created:
            if not self.ensure_fts_index():
                return []

        try:
            search_builder = table.search(query, query_type="fts").limit(limit)

            # Apply filters
            filters = []
            if kwargs.get("language"):
                filters.append(f"language = '{_escape_sql(kwargs['language'])}'")
            if kwargs.get("symbol_type"):
                filters.append(f"symbol_type = '{_escape_sql(kwargs['symbol_type'])}'")
            if filters:
                search_builder = search_builder.where(" AND ".join(filters))

            results = search_builder.to_list()

            # Normalize _score to [0, 1] range
            if results:
                max_score = max(r.get("_score", 0.0) for r in results) or 1.0
                for r in results:
                    raw_score = r.pop("_score", 0.0)
                    r.pop("_distance", None)
                    r["score"] = raw_score / max_score

            return results
        except Exception as e:
            logger.warning("BM25 search failed: %s", e)
            return []

    def add(self, items: List[Dict[str, Any]]) -> None:
        """No-op: vector engine owns writes."""

    def delete(self, ids: List[str]) -> None:
        """No-op: vector engine owns deletes."""

    def clear(self) -> None:
        """No-op: vector engine owns table lifecycle."""
        with self._lock:
            self._fts_index_created = False
            self._table = None

    def count(self) -> int:
        """Return total chunk count (delegates to shared table)."""
        table = self._get_table()
        if table is None:
            return 0
        return table.count_rows()
