# ADR-016: Remove Unused Pydantic Schemas (Supersedes ADR-013)

## Status: Accepted
## Date: 2026-07-02

## Context

[ADR-013](ADR-013-pydantic-schemas.md) introduced `schemas/inputs.py` and
`schemas/responses.py`: Pydantic v2 models intended to be constructed inside each tool
function (`SearchInput(query=query, ...)`) and returned via `.model_dump()`, as the
single source of validation and response-shape consistency.

That pattern was never actually adopted. Every tool in `server.py` still validates
through the original inline helpers (`_validate_path`, `_validate_query`,
`_validate_symbol_name`) and returns hand-built dicts. A repo-wide search confirmed
`schemas/` was imported nowhere outside its own test file
(`tests/test_schemas.py`) — it was tested in isolation but never wired into a single
running code path.

This was flagged in `todo.md` as drift-prone dead weight: the models documented a
validation contract the code didn't actually follow, which is worse than no models at
all for anyone reading them to understand current behavior.

## Decision

Delete `src/nexus_mcp/schemas/` (`__init__.py`, `inputs.py`, `responses.py`) and
`tests/test_schemas.py`. Inline validation (`_validate_path`, `_validate_query`,
`_validate_symbol_name` in `server.py`) remains the only validation layer, as it has
been in practice since Phase 5.

Wiring the schemas in as originally intended (ADR-013's alternative) was considered and
rejected for this pass: it would touch every tool function for a pure internal-quality
change unrelated to the daily-driver trust work this change is otherwise scoped to, and
nothing currently depends on the schemas existing.

## Consequences

- One less package to keep in sync with actual tool signatures; removes a source of
  "the docs/models say X but the code does Y" drift.
- If structured I/O validation is wanted later, it should be (re-)introduced alongside
  the tools that actually call it, in the same change — not built ahead of adoption.
- `CLAUDE.md` gotcha #15 updated to reflect that inline validation is the only layer.

## Alternatives Considered

- **Wire the schemas in now**: Rejected as out of scope for this change; see Decision.
- **Leave them in place, unused**: Rejected — `todo.md` already identified this as
  actively misleading (a reader would reasonably assume the models reflect the real
  validation contract).
