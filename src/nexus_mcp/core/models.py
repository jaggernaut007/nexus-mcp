"""Core data models for Nexus-MCP.

Ported from CodeGrok MCP. Defines Symbol, ParsedFile, CodebaseIndex, and Memory.
All models use frozen dataclasses for immutability and JSON serialization.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class SymbolType(Enum):
    """Code symbol types."""

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, value: str) -> "SymbolType":
        for member in cls:
            if member.value == value.lower():
                return member
        raise ValueError(f"Invalid SymbolType: {value}")


@dataclass(frozen=True)
class Symbol:
    """A single code symbol (function, class, method, or variable).

    Attributes:
        name: Identifier name
        type: Symbol kind
        filepath: Absolute path to file
        line_start: Starting line (1-indexed)
        line_end: Ending line (1-indexed)
        language: Programming language
        signature: Full declaration
        docstring: Documentation string
        parent: Parent class name for methods
        code_snippet: Truncated source code
        imports: Import statements in scope
        calls: Function calls made
        metadata: Extensible dict
    """

    name: str
    type: SymbolType
    filepath: str
    line_start: int
    line_end: int
    language: str
    signature: str
    docstring: str = ""
    parent: Optional[str] = None
    code_snippet: str = ""
    imports: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name:
            raise ValueError("Symbol name cannot be empty")
        if not self.filepath:
            raise ValueError("Symbol filepath cannot be empty")
        if self.line_start < 1:
            raise ValueError(f"line_start must be >= 1, got {self.line_start}")
        if self.line_end < self.line_start:
            raise ValueError(
                f"line_end ({self.line_end}) must be >= line_start ({self.line_start})"
            )
        if not self.language:
            raise ValueError("Symbol language cannot be empty")
        if not isinstance(self.type, SymbolType):
            raise ValueError(f"type must be SymbolType enum, got {type(self.type)}")

    @property
    def qualified_name(self) -> str:
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Symbol":
        if "type" in data and isinstance(data["type"], str):
            data = data.copy()
            data["type"] = SymbolType.from_string(data["type"])
        return cls(**data)


@dataclass(frozen=True)
class ParsedFile:
    """A parsed source code file with extracted symbols."""

    filepath: str
    language: str
    symbols: List[Symbol] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    parse_time: float = 0.0
    error: Optional[str] = None

    def __post_init__(self):
        if not self.filepath:
            raise ValueError("ParsedFile filepath cannot be empty")
        if not self.language:
            raise ValueError("ParsedFile language cannot be empty")
        if self.parse_time < 0:
            raise ValueError(f"parse_time must be >= 0, got {self.parse_time}")

    @property
    def is_successful(self) -> bool:
        return self.error is None

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)

    def get_symbols_by_type(self, symbol_type: SymbolType) -> List[Symbol]:
        return [s for s in self.symbols if s.type == symbol_type]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filepath": self.filepath,
            "language": self.language,
            "symbols": [s.to_dict() for s in self.symbols],
            "imports": self.imports,
            "parse_time": self.parse_time,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParsedFile":
        data = data.copy()
        if "symbols" in data:
            data["symbols"] = [Symbol.from_dict(s) for s in data["symbols"]]
        return cls(**data)


@dataclass(frozen=True)
class CodebaseIndex:
    """Complete index of a codebase."""

    root_path: str
    files: Dict[str, ParsedFile] = field(default_factory=dict)
    total_files: int = 0
    total_symbols: int = 0
    indexed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if not self.root_path:
            raise ValueError("CodebaseIndex root_path cannot be empty")
        if self.total_files < 0:
            raise ValueError(f"total_files must be >= 0, got {self.total_files}")
        if self.total_symbols < 0:
            raise ValueError(f"total_symbols must be >= 0, got {self.total_symbols}")

    @property
    def successful_parses(self) -> int:
        return sum(1 for f in self.files.values() if f.is_successful)

    @property
    def failed_parses(self) -> int:
        return sum(1 for f in self.files.values() if not f.is_successful)

    def get_symbols_by_name(self, name: str) -> List[Symbol]:
        results = []
        for parsed_file in self.files.values():
            results.extend(s for s in parsed_file.symbols if s.name == name)
        return results

    def get_symbols_by_type(self, symbol_type: SymbolType) -> List[Symbol]:
        results = []
        for parsed_file in self.files.values():
            results.extend(parsed_file.get_symbols_by_type(symbol_type))
        return results

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "files": {path: f.to_dict() for path, f in self.files.items()},
            "total_files": self.total_files,
            "total_symbols": self.total_symbols,
            "indexed_at": self.indexed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodebaseIndex":
        data = data.copy()
        if "files" in data:
            data["files"] = {path: ParsedFile.from_dict(f) for path, f in data["files"].items()}
        return cls(**data)


class MemoryType(Enum):
    """Memory entry types."""

    CONVERSATION = "conversation"
    STATUS = "status"
    DECISION = "decision"
    PREFERENCE = "preference"
    DOC = "doc"
    NOTE = "note"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, value: str) -> "MemoryType":
        for member in cls:
            if member.value == value.lower():
                return member
        raise ValueError(f"Invalid MemoryType: {value}")


@dataclass
class Memory:
    """A semantic memory entry. Mutable for accessed_at updates."""

    id: str
    content: str
    memory_type: MemoryType
    project: str
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    accessed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl: str = "permanent"
    source: str = "user"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            raise ValueError("Memory id cannot be empty")
        if not self.content:
            raise ValueError("Memory content cannot be empty")
        if not self.project:
            raise ValueError("Memory project cannot be empty")
        if not isinstance(self.memory_type, MemoryType):
            raise ValueError(f"memory_type must be MemoryType enum, got {type(self.memory_type)}")
        valid_ttls = ("session", "day", "week", "month", "permanent")
        if self.ttl not in valid_ttls:
            raise ValueError(f"ttl must be one of {valid_ttls}, got {self.ttl}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "project": self.project,
            "tags": self.tags,
            "created_at": self.created_at,
            "accessed_at": self.accessed_at,
            "ttl": self.ttl,
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Memory":
        data = data.copy()
        if "memory_type" in data and isinstance(data["memory_type"], str):
            data["memory_type"] = MemoryType.from_string(data["memory_type"])
        return cls(**data)

    def touch(self) -> None:
        self.accessed_at = datetime.now(timezone.utc).isoformat()
