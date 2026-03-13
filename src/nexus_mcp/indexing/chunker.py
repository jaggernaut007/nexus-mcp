"""Symbol-to-CodeChunk conversion for LanceDB storage.

Converts parsed Symbol objects into embeddable CodeChunks with
formatted text, deterministic IDs, and metadata for vector search.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List

from nexus_mcp.config import get_settings
from nexus_mcp.core.models import Symbol


@dataclass(slots=True)
class CodeChunk:
    """A chunk of code ready for embedding and LanceDB storage.

    The vector field is left empty by the chunker and populated
    by the pipeline after embedding.
    """

    id: str
    text: str
    filepath: str
    symbol_name: str
    symbol_type: str
    language: str
    line_start: int
    line_end: int
    signature: str
    parent: str = ""
    docstring: str = ""
    vector: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for LanceDB insertion."""
        return {
            "id": self.id,
            "text": self.text,
            "vector": self.vector,
            "filepath": self.filepath,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "language": self.language,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "signature": self.signature,
            "parent": self.parent,
            "docstring": self.docstring,
        }


def _generate_chunk_id(filepath: str, name: str, line_start: int) -> str:
    """Generate a deterministic chunk ID from filepath, name, and line."""
    key = f"{filepath}:{name}:{line_start}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def create_chunk_text(symbol: Symbol) -> str:
    """Format a Symbol into embeddable text.

    Format:
        # filepath:line_start
        type: qualified_name

        signature

        docstring

        code_snippet

        Imports: ...
        Calls: ...
    """
    settings = get_settings()
    parts = []

    # Header
    parts.append(f"# {symbol.filepath}:{symbol.line_start}")
    parts.append(f"{symbol.type.value}: {symbol.qualified_name}")
    parts.append("")

    # Signature
    if symbol.signature:
        parts.append(symbol.signature)
        parts.append("")

    # Docstring
    if symbol.docstring:
        doc = symbol.docstring[:500]
        parts.append(doc)
        parts.append("")

    # Code snippet (truncated)
    if symbol.code_snippet:
        snippet = symbol.code_snippet[: settings.chunk_max_chars]
        parts.append(snippet)
        parts.append("")

    # Imports
    if symbol.imports:
        parts.append(f"Imports: {', '.join(symbol.imports)}")

    # Calls
    if symbol.calls:
        parts.append(f"Calls: {', '.join(symbol.calls)}")

    return "\n".join(parts).strip()


def create_chunk(symbol: Symbol) -> CodeChunk:
    """Convert a Symbol into a CodeChunk.

    The vector field is left empty — the pipeline fills it after embedding.
    """
    return CodeChunk(
        id=_generate_chunk_id(symbol.filepath, symbol.name, symbol.line_start),
        text=create_chunk_text(symbol),
        filepath=symbol.filepath,
        symbol_name=symbol.qualified_name,
        symbol_type=symbol.type.value,
        language=symbol.language,
        line_start=symbol.line_start,
        line_end=symbol.line_end,
        signature=symbol.signature,
        parent=symbol.parent or "",
        docstring=symbol.docstring[:500] if symbol.docstring else "",
    )


def create_chunks(symbols: List[Symbol]) -> List[CodeChunk]:
    """Convert a list of Symbols into CodeChunks."""
    return [create_chunk(s) for s in symbols]
