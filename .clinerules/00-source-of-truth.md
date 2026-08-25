# Source of truth — do not fork context

This repo is driven by multiple agent harnesses (Claude Code, Codex, Cline, Antigravity).
`AGENTS.md` at the repo root is the single source of truth for every tool.

- **Do not generate a Memory Bank.** The project context already exists as versioned artifacts —
  treat these as your Memory Bank:
  - `AGENTS.md` — stack, commands, Definition of Done, code standards. Authoritative; read first.
  - `PROGRESS.md` — session state. Read at session start, update at end.
  - `docs/ARCHITECTURE.md` / `docs/DEVELOPER_GUIDE.md` / `docs/IMPLEMENTATION_PLAN.md` /
    `docs/ROADMAP-2026.md` — system structure and plan.
  - `docs/adr/` — architectural decisions; check before changing structure.
- Path-scoped rules live in `.claude/rules/` (Claude Code loads them automatically on path match;
  read them yourself when touching those paths):
  - `.claude/rules/test-standards.md` — before writing/modifying tests.
- Same mistake made twice → add a one-line rule to `AGENTS.md` (portable), not to a
  tool-specific file.
- Output dense, direct text. Omit pleasantries.
