# ADR-012: Tool Permission Model

## Status: Accepted
## Date: 2026-03-12

## Context

The MCP specification recommends security-first defaults for tool access control. As Nexus-MCP exposes 13 tools with varying side effects — from read-only queries (`search`, `status`) to disk-writing operations (`index`, `remember`) — a permission model is needed to let operators restrict which tools a client can invoke.

The primary transport today is stdio (local process), where full trust is reasonable. However, future HTTP/SSE transport will expose the server to untrusted clients, requiring tighter defaults.

## Decision

Classify all tools into three categories with a static registry:

- **READ** — Query-only, no side effects: `status`, `search`, `find_symbol`, `find_callers`, `find_callees`, `explain`, `recall`, `health`
- **MUTATE** — Triggers computation and disk writes to index/analysis data: `index`, `analyze`, `impact`
- **WRITE** — Modifies user-facing state (memory store): `remember`, `forget`

Enforcement uses a `PermissionPolicy` dataclass with three resolution layers:
1. Explicit `denied_tools` overrides everything (deny list wins)
2. Explicit `allowed_tools` overrides category check (per-tool allowlist)
3. Category-based check against `allowed_categories`
4. Unknown tools are denied by default

Two preset policies are provided:
- `DEFAULT_POLICY` — read-only (only READ category allowed)
- `FULL_ACCESS_POLICY` — all categories allowed

The active policy is selected via `NEXUS_PERMISSION_LEVEL` env var:
- `full` (default) — backward compatible, all tools accessible
- `read` — restricted to read-only tools

The default is `full` rather than `read` because all current deployments use stdio transport where the client is the local user. Switching the default would break existing setups. When HTTP transport is added, the default should be reconsidered.

## Consequences

- Existing stdio users are unaffected (default is `full`)
- Operators can restrict access via a single env var for shared/remote deployments
- The deny/allow/category resolution order allows fine-grained overrides without complex configuration
- Adding a new tool requires adding it to `TOOL_PERMISSIONS` in `security/permissions.py`
- OAuth 2.1 scope mapping (deferred to Phase 7) can map scopes to categories directly

## Alternatives Considered

- **Per-tool env vars** (e.g., `NEXUS_ALLOW_INDEX=true`): Does not scale. 13 tools means 13 env vars.
- **Role-based access (RBAC)**: Overengineered for the current single-user, stdio-only deployment. Categories achieve the same grouping without a role abstraction layer.
- **Default to read-only**: Would break backward compatibility for all existing users who rely on `index` and `remember` tools working out of the box.
