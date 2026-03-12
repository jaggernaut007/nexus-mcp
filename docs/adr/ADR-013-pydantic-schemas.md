# ADR-013: Pydantic v2 Input/Output Schemas

## Status: Accepted
## Date: 2026-03-12

## Context

Nexus-MCP tools accept user input via MCP and return structured JSON responses. Phase 5 added inline validation helpers (`_validate_path`, `_validate_query`, `_validate_symbol_name`), but these are procedural and not reusable. Tool responses were built as ad-hoc dictionaries, making it hard to guarantee consistent output shapes across tools.

FastMCP requires tool functions to have simple parameter signatures (strings, ints, bools) for LLM invocation compatibility. Pydantic models cannot be used directly as function parameters because FastMCP would not know how to present them to the LLM as tool schemas.

## Decision

Introduce two sets of Pydantic v2 models in `schemas/`:

### Input Models (`schemas/inputs.py`)

- `IndexInput`, `SearchInput`, `SymbolNameInput`, `AnalyzeInput`, `ImpactInput`, `RememberInput`, `RecallInput`, `ForgetInput`
- Encode the same validation rules as the existing `_validate_*` helpers: null byte rejection, length limits, allowed value checks, range clamping
- Used **internally** at the top of each tool function: construct the model from the raw params, let Pydantic raise `ValidationError` on invalid input
- Tool function signatures remain simple params (FastMCP compatible)

### Response Models (`schemas/responses.py`)

- One model per tool: `StatusResponse`, `HealthResponse`, `IndexResponse`, `SearchResponse`, `FindSymbolResponse`, `CallersResponse`, `CalleesResponse`, `AnalyzeResponse`, `ImpactResponse`, `ExplainResponse`, `MemoryResponse`, `RecallResponse`, `ForgetResponse`
- Plus a shared `ErrorResponse` for error cases
- Tools construct the model, then return `.model_dump()` as the final response dict
- This guarantees consistent field names and types across all responses

### Pattern

```python
@mcp.tool()
def search(query: str, limit: int = 10, mode: str = "hybrid") -> str:
    validated = SearchInput(query=query, limit=limit, mode=mode)
    # ... business logic ...
    return json.dumps(SearchResponse(...).model_dump())
```

## Consequences

- All tool inputs are validated through Pydantic with clear error messages
- All tool outputs have guaranteed shapes, making them reliably parsable by LLMs
- Adding a new tool requires adding input and response models alongside the tool function
- Pydantic v2 is already a transitive dependency (via FastMCP), so no new dependency is added
- The inline `_validate_*` helpers in `server.py` can be deprecated in favor of the schema models

## Alternatives Considered

- **Pydantic models as function params**: FastMCP does not support this; it needs flat params for LLM tool schema generation.
- **dataclasses for responses**: No built-in validation, no `.model_dump()`, would require manual serialization logic.
- **TypedDict for responses**: Provides type hints but no runtime validation. Pydantic catches type mismatches at construction time.
- **msgspec**: Faster serialization, but Pydantic is already a dependency and provides richer validation (field_validator decorators).
