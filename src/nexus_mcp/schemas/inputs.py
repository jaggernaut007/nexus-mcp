"""Pydantic v2 input validation models for Nexus-MCP tools.

These models encode the validation rules currently in server.py's _validate_*
helpers. They are used internally for validation, NOT as tool function signatures
(FastMCP needs simple params for LLM invocation).
"""

import re

from pydantic import BaseModel, field_validator


class IndexInput(BaseModel):
    """Validated input for the index tool."""

    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Path contains null bytes.")
        if not v.strip():
            raise ValueError("Path must not be empty.")
        return v


class SearchInput(BaseModel):
    """Validated input for the search tool."""

    query: str
    limit: int = 10
    language: str = ""
    symbol_type: str = ""
    mode: str = "hybrid"
    rerank: bool = True

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Query contains null bytes.")
        if len(v) > 10000:
            raise ValueError("Query too long (max 10,000 characters).")
        if not v.strip():
            raise ValueError("Query must not be empty.")
        return v

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        return max(1, min(v, 100))

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("hybrid", "vector", "bm25"):
            raise ValueError(f"Invalid mode: {v}. Must be 'hybrid', 'vector', or 'bm25'.")
        return v


class SymbolNameInput(BaseModel):
    """Validated input for tools that take a symbol name."""

    name: str
    exact: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Symbol name contains null bytes.")
        if len(v) > 500:
            raise ValueError("Symbol name too long (max 500 characters).")
        if not v or not re.search(r"\w", v):
            raise ValueError("Symbol name must contain at least one alphanumeric character.")
        return v


class AnalyzeInput(BaseModel):
    """Validated input for the analyze tool."""

    path: str = ""


class ImpactInput(BaseModel):
    """Validated input for the impact tool."""

    symbol_name: str
    max_depth: int = 10

    @field_validator("symbol_name")
    @classmethod
    def validate_symbol_name(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Symbol name contains null bytes.")
        if len(v) > 500:
            raise ValueError("Symbol name too long (max 500 characters).")
        if not v or not re.search(r"\w", v):
            raise ValueError("Symbol name must contain at least one alphanumeric character.")
        return v

    @field_validator("max_depth")
    @classmethod
    def validate_max_depth(cls, v: int) -> int:
        return max(1, min(v, 50))


class RememberInput(BaseModel):
    """Validated input for the remember tool."""

    content: str
    memory_type: str = "note"
    tags: str = ""
    ttl: str = "permanent"
    project: str = "default"

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Content must not be empty.")
        return v


class RecallInput(BaseModel):
    """Validated input for the recall tool."""

    query: str
    limit: int = 5
    memory_type: str = ""
    tags: str = ""

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Query contains null bytes.")
        if len(v) > 10000:
            raise ValueError("Query too long (max 10,000 characters).")
        if not v.strip():
            raise ValueError("Query must not be empty.")
        return v

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: int) -> int:
        return max(1, min(v, 50))


class ForgetInput(BaseModel):
    """Validated input for the forget tool."""

    memory_id: str = ""
    tags: str = ""
    memory_type: str = ""
