# ADR-014: Token Bucket Rate Limiting

## Status: Accepted
## Date: 2026-03-12

## Context

MCP servers exposed over network transports (HTTP/SSE) need rate limiting to prevent abuse — a single client could flood the server with expensive `index` or `search` requests. Even for stdio, rate limiting provides a safety net against runaway LLM loops that invoke tools in tight cycles.

The rate limiter must be:
- Per-tool, since tools have vastly different costs (`index` is expensive, `status` is cheap)
- Thread-safe, since MCP servers handle concurrent requests
- Zero-overhead when disabled, since stdio deployments do not need it
- Configurable without code changes

## Decision

Implement a token bucket rate limiter in `security/rate_limiter.py`:

### Algorithm

Token bucket: each tool key has a bucket with a `rate` (tokens/second refill) and `burst` (max tokens). Each invocation consumes one token. If the bucket is empty, the request is denied with a `RateLimitError` that includes a `retry_after` value.

### Per-Tool Overrides

A static `TOOL_RATE_OVERRIDES` dict sets tool-specific rates:

| Tool | Rate (req/s) | Burst |
|------|-------------|-------|
| `index` | 0.1 | 2 |
| `search` | 10.0 | 20 |
| `recall` | 10.0 | 20 |
| `explain` | 10.0 | 20 |
| `remember` | 5.0 | 10 |
| `forget` | 5.0 | 10 |
| (others) | default_rate | default_burst |

### Configuration

- `NEXUS_RATE_LIMIT_ENABLED` — `false` by default (off for stdio transport)
- `NEXUS_RATE_LIMIT_DEFAULT_RATE` — default 10.0 requests/second
- `NEXUS_RATE_LIMIT_DEFAULT_BURST` — default 20

### Thread Safety

A single `threading.Lock` protects the bucket dictionary. Bucket creation is lazy (on first access per key). The lock scope is minimal — just the `try_acquire` and `get_retry_after` calls.

### Error Handling

When rate limited, a `RateLimitError` exception is raised (defined in `core/exceptions.py`). The error includes the seconds until the next token is available via `get_retry_after()`.

## Consequences

- Zero performance impact when disabled (the limiter is not instantiated)
- Operators can enable rate limiting for network deployments with a single env var
- Per-tool rates prevent expensive operations from being abused while keeping cheap operations responsive
- Adding a new tool with custom rates requires adding an entry to `TOOL_RATE_OVERRIDES`
- The token bucket algorithm is well-understood and handles bursts naturally (accumulated tokens allow short spikes)

## Alternatives Considered

- **Fixed window counter**: Simpler, but allows burst traffic at window boundaries (2x rate). Token bucket handles bursts more gracefully.
- **Sliding window log**: More accurate, but requires storing timestamps per request. Higher memory overhead for marginal accuracy improvement.
- **External rate limiter (Redis, nginx)**: Adds infrastructure dependency. Nexus-MCP targets single-process deployment; an in-process limiter is sufficient.
- **asyncio-based limiter**: The server uses threading (FastMCP stdio), not asyncio. A threading.Lock-based approach matches the concurrency model.
