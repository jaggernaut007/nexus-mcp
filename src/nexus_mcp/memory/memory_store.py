"""LanceDB-backed semantic memory store.

Provides remember/recall/forget operations with TTL, tag filtering,
and semantic search using embeddings. Uses a separate `memories` table
from the `chunks` table used by vector/BM25 engines.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import lancedb
import pyarrow as pa

from nexus_mcp.core.models import Memory, MemoryType
from nexus_mcp.indexing.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def _escape_sql(value: str) -> str:
    """Escape single quotes for LanceDB SQL-style filter expressions."""
    return value.replace("'", "''")


def _make_memory_schema(vector_dims: int) -> pa.Schema:
    """Build the PyArrow schema for the memories table."""
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), vector_dims)),
        pa.field("content", pa.string()),
        pa.field("memory_type", pa.string()),
        pa.field("project", pa.string()),
        pa.field("tags", pa.string()),  # Comma-separated
        pa.field("created_at", pa.string()),
        pa.field("accessed_at", pa.string()),
        pa.field("ttl", pa.string()),
        pa.field("source", pa.string()),
        pa.field("metadata_json", pa.string()),
    ])


class MemoryStore:
    """LanceDB-backed semantic memory with TTL and tag filtering."""

    def __init__(
        self,
        db_path: str,
        embedding_service: EmbeddingService,
        table_name: str = "memories",
        vector_dims: int = 384,
    ):
        self._db_path = str(db_path)
        self._embedding_service = embedding_service
        self._table_name = table_name
        self._vector_dims = vector_dims
        self._lock = threading.RLock()
        self._db = lancedb.connect(self._db_path)
        self._table: Optional[Any] = None

    def _get_or_create_table(self):
        """Lazily open or create the memories table."""
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
                schema = _make_memory_schema(self._vector_dims)
                self._table = self._db.create_table(self._table_name, schema=schema)

            return self._table

    def _memory_to_row(self, memory: Memory, vector: List[float]) -> Dict[str, Any]:
        """Convert a Memory object to a LanceDB row dict."""
        return {
            "id": memory.id,
            "vector": vector,
            "content": memory.content,
            "memory_type": memory.memory_type.value,
            "project": memory.project,
            "tags": "," + ",".join(memory.tags) + "," if memory.tags else "",
            "created_at": memory.created_at,
            "accessed_at": memory.accessed_at,
            "ttl": memory.ttl,
            "source": memory.source,
            "metadata_json": json.dumps(memory.metadata),
        }

    def _row_to_memory(self, row: Dict[str, Any]) -> Memory:
        """Convert a LanceDB row dict to a Memory object."""
        tags_str = row.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        metadata = {}
        if row.get("metadata_json"):
            try:
                metadata = json.loads(row["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        return Memory(
            id=row["id"],
            content=row["content"],
            memory_type=MemoryType.from_string(row["memory_type"]),
            project=row["project"],
            tags=tags,
            created_at=row.get("created_at", ""),
            accessed_at=row.get("accessed_at", ""),
            ttl=row.get("ttl", "permanent"),
            source=row.get("source", "user"),
            metadata=metadata,
        )

    def remember(self, memory: Memory) -> str:
        """Store a memory. Returns the memory ID.

        Embeds the memory content and appends to LanceDB.
        """
        vector = self._embedding_service.embed(memory.content)
        row = self._memory_to_row(memory, vector)

        with self._lock:
            table = self._get_or_create_table()
            table.add([row])

        logger.debug("Stored memory: %s", memory.id)
        return memory.id

    def recall(
        self,
        query: str,
        limit: int = 5,
        memory_type: str = "",
        tags: Optional[List[str]] = None,
        project: str = "",
    ) -> List[Memory]:
        """Semantic search for memories.

        Args:
            query: Search query text.
            limit: Max results.
            memory_type: Filter by memory type (e.g. "note", "decision").
            tags: Filter by tags (matches any tag).
            project: Filter by project name.

        Returns:
            List of matching Memory objects, sorted by relevance.
        """
        table = self._get_or_create_table()
        if table.count_rows() == 0:
            return []

        # Expire TTL-based entries first
        self.expire_ttl()

        vector = self._embedding_service.embed(query, is_query=True)
        search_builder = table.search(vector).limit(limit)

        # Apply filters
        filters = []
        if memory_type:
            filters.append(f"memory_type = '{_escape_sql(memory_type)}'")
        if project:
            filters.append(f"project = '{_escape_sql(project)}'")
        if tags:
            # Match any tag using LIKE
            tag_filters = [f"tags LIKE '%,{_escape_sql(t)},%'" for t in tags]
            filters.append(f"({' OR '.join(tag_filters)})")
        if filters:
            search_builder = search_builder.where(" AND ".join(filters))

        results = search_builder.to_list()

        memories = []
        for row in results:
            try:
                mem = self._row_to_memory(row)
                mem.touch()
                memories.append(mem)
            except (ValueError, KeyError) as e:
                logger.warning("Failed to deserialize memory: %s", e)

        return memories

    def forget(
        self,
        memory_id: str = "",
        tags: Optional[List[str]] = None,
        memory_type: str = "",
        before: str = "",
    ) -> int:
        """Delete memories by criteria.

        Args:
            memory_id: Delete specific memory by ID.
            tags: Delete memories matching any of these tags.
            memory_type: Delete memories of this type.
            before: Delete memories created before this ISO timestamp.

        Returns:
            Number of memories deleted.
        """
        table = self._get_or_create_table()
        count_before = table.count_rows()

        with self._lock:
            if memory_id:
                table.delete(f"id = '{_escape_sql(memory_id)}'")
            elif tags or memory_type or before:
                filters = []
                if memory_type:
                    filters.append(f"memory_type = '{_escape_sql(memory_type)}'")
                if tags:
                    tag_filters = [f"tags LIKE '%,{_escape_sql(t)},%'" for t in tags]
                    filters.append(f"({' OR '.join(tag_filters)})")
                if before:
                    filters.append(f"created_at < '{_escape_sql(before)}'")
                if filters:
                    table.delete(" AND ".join(filters))

        count_after = table.count_rows()
        deleted = count_before - count_after
        logger.debug("Deleted %d memories", deleted)
        return deleted

    def expire_ttl(self) -> int:
        """Delete memories past their TTL. Returns count deleted."""
        table = self._get_or_create_table()
        if table.count_rows() == 0:
            return 0

        now = datetime.now(timezone.utc)
        ttl_deltas = {
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
        }

        total_deleted = 0
        with self._lock:
            for ttl_value, delta in ttl_deltas.items():
                cutoff = (now - delta).isoformat()
                try:
                    count_before = table.count_rows()
                    table.delete(
                        f"ttl = '{ttl_value}' AND created_at < '{_escape_sql(cutoff)}'"
                    )
                    count_after = table.count_rows()
                    total_deleted += count_before - count_after
                except Exception as e:
                    logger.warning("TTL expiration failed for %s: %s", ttl_value, e)

        if total_deleted > 0:
            logger.info("Expired %d memories", total_deleted)
        return total_deleted

    def count(self) -> int:
        """Return total memory count."""
        table = self._get_or_create_table()
        return table.count_rows()

    def clear(self) -> None:
        """Drop and recreate the memories table."""
        import gc

        with self._lock:
            tables = self._db.list_tables()
            table_names = tables.tables if hasattr(tables, "tables") else list(tables)

            if self._table_name in table_names:
                self._db.drop_table(self._table_name)

            schema = _make_memory_schema(self._vector_dims)
            self._table = self._db.create_table(self._table_name, schema=schema)
            gc.collect()
