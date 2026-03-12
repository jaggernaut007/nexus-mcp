"""Indexing pipeline: discover → parse → chunk → embed → store.

Orchestrates tree-sitter (symbols for vectors) and ast-grep (graph structure)
parsing, embedding via ONNX, and storage in LanceDB + rustworkx.
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from nexus_mcp.config import Settings, get_settings
from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.engines.bm25_engine import LanceDBBM25Engine
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.engines.vector_engine import LanceDBVectorEngine
from nexus_mcp.indexing.chunker import create_chunks
from nexus_mcp.indexing.embedding_service import get_embedding_service
from nexus_mcp.indexing.parallel_indexer import parallel_parse_files
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.parsing.language_registry import get_supported_extensions

logger = logging.getLogger(__name__)

SKIP_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".nexus", "venv", ".venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
    ".ruff_cache", ".hg", ".svn",
}


@dataclass
class IndexResult:
    """Result of an indexing operation."""

    total_files: int = 0
    total_symbols: int = 0
    total_chunks: int = 0
    parse_errors: int = 0
    graph_nodes: int = 0
    graph_relationships: int = 0
    time_seconds: float = 0.0
    files_added: int = 0
    files_modified: int = 0
    files_deleted: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def discover_files(root: Path, settings: Settings) -> List[Path]:
    """Discover source files under root, respecting .gitignore and size limits.

    Filters:
    - Skips SKIP_DIRS (hidden dirs, node_modules, etc.)
    - Skips files not in EXTENSION_MAP
    - Skips files exceeding max_file_size_mb
    - Respects .gitignore if pathspec is available
    """
    supported_extensions = get_supported_extensions()
    max_size = settings.max_file_size_mb * 1024 * 1024
    root = root.resolve()

    # Load .gitignore patterns if available
    gitignore_spec = None
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        try:
            import pathspec
            patterns = gitignore_path.read_text().splitlines()
            gitignore_spec = pathspec.PathSpec.from_lines("gitignore", patterns)
        except ImportError:
            logger.debug("pathspec not installed, skipping .gitignore support")
        except Exception as e:
            logger.warning("Failed to parse .gitignore: %s", e)

    files: List[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            ext = filepath.suffix.lower()

            # Check extension
            if ext not in supported_extensions:
                continue

            # Check .gitignore
            if gitignore_spec:
                rel = filepath.relative_to(root)
                if gitignore_spec.match_file(str(rel)):
                    continue

            # Check file size
            try:
                if filepath.stat().st_size > max_size:
                    continue
            except OSError:
                continue

            files.append(filepath)

    return sorted(files)


def _transfer_graph(universal_graph: UniversalGraph, code_graph: RustworkxCodeGraph) -> None:
    """Transfer nodes and relationships from UniversalGraph to RustworkxCodeGraph."""
    for node in universal_graph.nodes.values():
        code_graph.add_node(node)
    for rel in universal_graph.relationships.values():
        code_graph.add_relationship(rel)


class IndexingPipeline:
    """Orchestrates the full indexing flow.

    Steps:
    1. Discover files (pathspec + extension filter)
    2. Parse symbols (tree-sitter, parallel)
    3. Parse graph structure (ast-grep, sequential)
    4. Chunk symbols into CodeChunks
    5. Embed chunks in batches
    6. Store in LanceDB + rustworkx graph
    7. Save metadata for incremental reindex
    8. Unload embedding model to free RAM
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._embedding_service = get_embedding_service(
            self._settings.embedding_model,
            batch_size=self._settings.embedding_batch_size,
        )
        self._vector_engine = LanceDBVectorEngine(
            db_path=str(self._settings.lancedb_path),
            embedding_service=self._embedding_service,
        )
        self._bm25_engine = LanceDBBM25Engine(
            db_path=str(self._settings.lancedb_path),
        )
        self._graph_engine = RustworkxCodeGraph()
        self._astgrep = AstGrepParser()
        self._metadata_path = self._settings.storage_path / "index_metadata.json"

    @property
    def vector_engine(self) -> LanceDBVectorEngine:
        return self._vector_engine

    @property
    def bm25_engine(self) -> LanceDBBM25Engine:
        return self._bm25_engine

    @property
    def graph_engine(self) -> RustworkxCodeGraph:
        return self._graph_engine

    def index(
        self,
        codebase_path: Path,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> IndexResult:
        """Full index of a codebase. Clears existing data first."""
        start = time.time()
        codebase_path = Path(codebase_path).resolve()

        # Ensure storage dir exists
        self._settings.storage_path.mkdir(parents=True, exist_ok=True)

        # Step 1: Discover files
        files = discover_files(codebase_path, self._settings)
        logger.info("Discovered %d files in %s", len(files), codebase_path)

        if not files:
            return IndexResult(time_seconds=time.time() - start)

        # Step 2: Parse symbols with tree-sitter (parallel)
        symbols, parse_errors = parallel_parse_files(
            files, self._settings.max_workers, progress_callback
        )
        logger.info("Parsed %d symbols (%d errors)", len(symbols), parse_errors)

        # Step 3: Parse graph structure with ast-grep (sequential)
        self._graph_engine.clear()
        universal_graph = UniversalGraph()
        for filepath in files:
            if self._astgrep.can_parse(str(filepath)):
                try:
                    self._astgrep.parse_file(str(filepath), universal_graph)
                except Exception as e:
                    logger.warning("ast-grep failed for %s: %s", filepath, e)

        _transfer_graph(universal_graph, self._graph_engine)
        graph_stats = self._graph_engine.get_statistics()

        # Step 4: Chunk symbols
        chunks = create_chunks(symbols)

        if not chunks:
            self._save_metadata(codebase_path, files)
            return IndexResult(
                total_files=len(files),
                total_symbols=len(symbols),
                parse_errors=parse_errors,
                graph_nodes=graph_stats["total_nodes"],
                graph_relationships=graph_stats["total_relationships"],
                time_seconds=time.time() - start,
            )

        # Step 5: Embed in batches, store, and clean up
        try:
            batch_size = self._settings.embedding_batch_size
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                texts = [c.text for c in batch]
                vectors = self._embedding_service.embed_batch(texts)
                for chunk, vector in zip(batch, vectors):
                    chunk.vector = vector

            # Step 6: Store in vector engine
            self._vector_engine.clear()
            chunk_dicts = [c.to_dict() for c in chunks]
            self._vector_engine.add(chunk_dicts)

            # Step 6b: Create FTS index for BM25
            self._bm25_engine.clear()
            self._bm25_engine.ensure_fts_index()

            # Step 7: Save metadata
            self._save_metadata(codebase_path, files)
        finally:
            # Step 8: Always unload model to free RAM
            self._embedding_service.unload()

        elapsed = time.time() - start
        logger.info(
            "Indexed %d files, %d symbols, %d chunks in %.1fs",
            len(files), len(symbols), len(chunks), elapsed,
        )

        return IndexResult(
            total_files=len(files),
            total_symbols=len(symbols),
            total_chunks=len(chunks),
            parse_errors=parse_errors,
            graph_nodes=graph_stats["total_nodes"],
            graph_relationships=graph_stats["total_relationships"],
            time_seconds=elapsed,
        )

    def _validate_index(self) -> bool:
        """Validate that stored index artifacts are intact.

        Returns True if valid, False if corrupt or missing.
        """
        # Check metadata file
        if not self._metadata_path.exists():
            return False
        try:
            data = json.loads(self._metadata_path.read_text())
            if "mtimes" not in data:
                return False
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt index metadata file.")
            return False

        # Check vector engine table schema
        if not self._vector_engine.validate():
            logger.warning("Corrupt vector index detected.")
            return False

        return True

    def incremental_index(
        self,
        codebase_path: Path,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> IndexResult:
        """Incrementally re-index only changed/new/deleted files."""
        start = time.time()
        codebase_path = Path(codebase_path).resolve()

        # Validate existing index integrity
        if not self._validate_index():
            logger.warning("Corrupt index detected, performing full rebuild.")
            try:
                self._metadata_path.unlink(missing_ok=True)
            except OSError:
                pass
            return self.index(codebase_path, progress_callback)

        # Load stored metadata
        stored_mtimes = self._load_metadata()
        if not stored_mtimes:
            return self.index(codebase_path, progress_callback)

        # Discover current files
        files = discover_files(codebase_path, self._settings)
        current_mtimes = {}
        for f in files:
            try:
                current_mtimes[str(f)] = f.stat().st_mtime
            except OSError:
                continue

        # Categorize changes
        current_set = set(current_mtimes.keys())
        stored_set = set(stored_mtimes.keys())

        new_files = current_set - stored_set
        deleted_files = stored_set - current_set
        modified_files = {
            f for f in current_set & stored_set
            if current_mtimes[f] != stored_mtimes.get(f)
        }

        changed_files = new_files | modified_files

        if not changed_files and not deleted_files:
            return IndexResult(time_seconds=time.time() - start)

        # Remove deleted/modified from engines
        for fp in deleted_files | modified_files:
            self._vector_engine.delete_by_filepath(fp)
            self._graph_engine.remove_file_nodes(fp)

        # Parse and index changed files
        changed_paths = [Path(f) for f in changed_files]
        symbols, parse_errors = parallel_parse_files(
            changed_paths, self._settings.max_workers, progress_callback
        )

        # ast-grep for changed files
        universal_graph = UniversalGraph()
        for filepath in changed_paths:
            if self._astgrep.can_parse(str(filepath)):
                try:
                    self._astgrep.parse_file(str(filepath), universal_graph)
                except Exception as e:
                    logger.warning("ast-grep failed for %s: %s", filepath, e)
        _transfer_graph(universal_graph, self._graph_engine)

        # Chunk + embed + store
        chunks = create_chunks(symbols)
        try:
            if chunks:
                batch_size = self._settings.embedding_batch_size
                for i in range(0, len(chunks), batch_size):
                    batch = chunks[i : i + batch_size]
                    texts = [c.text for c in batch]
                    vectors = self._embedding_service.embed_batch(texts)
                    for chunk, vector in zip(batch, vectors):
                        chunk.vector = vector

                self._vector_engine.add([c.to_dict() for c in chunks])

            # Rebuild FTS index for BM25
            self._bm25_engine.clear()
            self._bm25_engine.ensure_fts_index()

            # Save updated metadata
            self._save_metadata(codebase_path, files)
        finally:
            self._embedding_service.unload()

        graph_stats = self._graph_engine.get_statistics()
        elapsed = time.time() - start

        return IndexResult(
            total_files=len(files),
            total_symbols=len(symbols),
            total_chunks=len(chunks),
            parse_errors=parse_errors,
            graph_nodes=graph_stats["total_nodes"],
            graph_relationships=graph_stats["total_relationships"],
            time_seconds=elapsed,
            files_added=len(new_files),
            files_modified=len(modified_files),
            files_deleted=len(deleted_files),
        )

    def _save_metadata(self, codebase_path: Path, files: List[Path]) -> None:
        """Save file mtimes for incremental reindex."""
        mtimes = {}
        for f in files:
            try:
                mtimes[str(f)] = f.stat().st_mtime
            except OSError:
                continue

        metadata = {
            "codebase_path": str(codebase_path),
            "mtimes": mtimes,
        }
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_path.write_text(json.dumps(metadata))

    def _load_metadata(self) -> Optional[Dict[str, float]]:
        """Load stored file mtimes, or None if no metadata exists."""
        if not self._metadata_path.exists():
            return None
        try:
            data = json.loads(self._metadata_path.read_text())
            return data.get("mtimes", {})
        except (json.JSONDecodeError, OSError):
            return None
