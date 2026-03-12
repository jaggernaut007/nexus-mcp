# Agentic Claude Code Setup — Portable Project Template

A complete, reusable guide for making any Python project "agent-ready" with Claude Code.
Replicate the setup used in the CR8 project on any new codebase in under an hour.

---

## Contents

1. [File Package — Copy This to Any Project](#1-file-package--copy-this-to-any-project)
2. [Step-by-Step Setup (10 Steps)](#2-step-by-step-setup-10-steps)
3. [AGENTS.md Template](#3-agentsmd-template)
4. [hooks.json Template](#4-hooksjson-template)
5. [The 5 Daily Workflows](#5-the-5-daily-workflows)
6. [What Cannot Be Automated](#6-what-cannot-be-automated)
7. [Master Checklist](#7-master-checklist)

---

## 1. File Package — Copy This to Any Project

Create this exact directory structure. Each file is tagged with the action required.

```
your-project/
├── AGENTS.md                              # 📝 Fill in placeholders
├── CLAUDE.md                              # ✅ Copy as-is (edit lightly)
├── CLAUDE.local.md                        # 📝 Fill in your machine overrides
├── PROGRESS.md                            # 📝 Fill in current project state
├── CONTRIBUTING.md                        # 📝 Fill in your coding patterns
├── .gitignore                             # 🔧 Add CLAUDE.local.md entry
│
├── scripts/
│   └── init.sh                            # 🔧 Configure commands for your stack
│
├── .claude/
│   ├── hooks.json                         # 🔧 Configure linter command
│   ├── README.md                          # ✅ Copy as-is
│   ├── agents/
│   │   ├── code-reviewer.md               # 📝 Fill in project-specific review rules
│   │   └── research-assistant.md          # 📝 Fill in libraries your project uses
│   ├── rules/
│   │   ├── api-standards.md               # 📝 Fill in your API/backend standards
│   │   └── test-standards.md              # 📝 Fill in your test standards
│   └── skills/
│       └── session-handoff/
│           └── SKILL.md                   # ✅ Copy as-is
│
├── backend/
│   ├── CLAUDE.md                          # 📝 Fill in backend architecture
│   └── README.md                          # 📝 Fill in backend orientation
│
├── frontend/
│   ├── CLAUDE.md                          # 📝 Fill in frontend architecture
│   └── README.md                          # 📝 Fill in frontend orientation
│
├── docs/
│   ├── adr/
│   │   └── ADR-000-template.md            # ✅ Copy as-is
│   └── research/
│       ├── INDEX.md                       # 📝 Fill in priority research areas
│       └── RESEARCH-TEMPLATE.md           # ✅ Copy as-is
│
└── .github/
    └── copilot-instructions.md            # 🔧 Symlink to AGENTS.md
```

**Legend:**

| Symbol | Meaning |
|--------|---------|
| ✅ Copy as-is | Template works without modification |
| 📝 Fill in placeholders | Replace `[YOUR_PROJECT]` sections with real values |
| 🔧 Configure commands | Change the actual commands/paths for your stack |

---

## 2. Step-by-Step Setup (10 Steps)

### Step 1 — Copy the File Package

Create all directories and files from the tree above. The fastest approach:

```bash
# In your project root
mkdir -p .claude/agents .claude/rules .claude/skills/session-handoff
mkdir -p scripts docs/adr docs/research .github

# Copy files from this repo's .claude/ package or create blanks to fill in
touch AGENTS.md CLAUDE.md PROGRESS.md CONTRIBUTING.md
touch .claude/hooks.json
touch .claude/agents/code-reviewer.md .claude/agents/research-assistant.md
touch .claude/rules/api-standards.md .claude/rules/test-standards.md
touch .claude/skills/session-handoff/SKILL.md
touch scripts/init.sh && chmod +x scripts/init.sh
touch docs/adr/ADR-000-template.md
touch docs/research/RESEARCH-TEMPLATE.md docs/research/INDEX.md
```

### Step 2 — Fill in AGENTS.md

This is the most important file. Use the template in Section 3 of this document.
The critical rules:
- Keep it **under 100 lines**. Longer files drop instruction adherence from 92% to 71%.
- Write all rules in **positive voice** ("Do X" not "Don't do Y").
- Include: project overview, tech stack, build commands, code standards, testing requirements, definition of done, session start protocol, and key directories.

### Step 3 — Configure scripts/init.sh

Replace the CR8-specific commands with your stack's commands. At minimum, implement these five checks:

```bash
#!/usr/bin/env bash
set -e

echo "Session Init — $(date)"

# 1. Environment: check .env or config file exists
# 2. Dependencies: import-test your core packages
# 3. Linter: run your linter (ruff / eslint / flake8)
# 4. Tests: run your test suite
# 5. Build (optional): build step if applicable

echo "All checks passed — safe to proceed"
```

Make it executable: `chmod +x scripts/init.sh`

The init script serves as the agent's first action every session. A failing init means the
project is in a broken state — the agent must fix it before starting new work.

### Step 4 — Create CLAUDE.local.md at Project Root

This file lives at the **project root** (not inside `.claude/`). Claude Code will not load it
from any other location.

```markdown
# CLAUDE.local.md
<!-- Personal/machine overrides. Gitignored — never commit. -->

## My Environment
- Local dev URL: http://localhost:3000
- Test database: postgresql://localhost:5432/myapp_test
- My preferred editor: cursor

## Machine-Specific Overrides
- [Any paths or commands that differ on this machine]
```

Add it to `.gitignore`:
```bash
echo "CLAUDE.local.md" >> .gitignore
```

### Step 5 — Fill in .claude/agents/ Subagent Files

Each subagent file has a YAML frontmatter block followed by the agent's instructions.
The key frontmatter fields:

```markdown
---
name: your-agent-name
description: One-sentence description. Include trigger phrases like "triggers on: 'review my code'".
tools: Read, Grep, Glob, Bash
model: sonnet
---
```

**Model values:** `sonnet`, `haiku`, `opus`, `inherit`
Do not use full model IDs — use these shorthand values only.

Subagent roles to create for any Python project:
- `code-reviewer.md` — Architecture compliance, test coverage, linter check, no secrets. Model: `sonnet`.
- `research-assistant.md` — Checks `docs/research/` first, then official docs, creates research note. Model: `haiku` (cheaper for web browsing).

### Step 6 — Configure .claude/hooks.json

Use the template in Section 4. The two hooks to configure:
- **Stop hook:** runs your linter after every agent turn. Fast only — no tests.
- **PostToolUse hook:** runs your linter on a single file after each Edit/Write operation.

Replace `ruff` with your linter (`eslint`, `flake8`, `prettier --check`, etc.).

### Step 7 — Create Subdirectory CLAUDE.md Files

For each major code area (backend, frontend, pipeline, services), create a `CLAUDE.md` file
in that directory. These are lazy-loaded — Claude only reads them when editing files in that
directory. Ideal for monorepos.

Each subdirectory `CLAUDE.md` should contain:
- The architecture of that area (key files and what they do)
- Rules specific to that directory (e.g. "all external API calls live here")
- What to check before making changes

### Step 8 — Set up .github/copilot-instructions.md Symlink

If you use GitHub Copilot alongside Claude Code, symlink `AGENTS.md` so both tools read
the same source of truth:

```bash
mkdir -p .github
ln -sfn ../AGENTS.md .github/copilot-instructions.md
```

For Cursor:
```bash
mkdir -p .cursor/rules
ln -sfn ../../AGENTS.md .cursor/rules/main.mdc
```

### Step 9 — Create PROGRESS.md with Current Project State

Fill in the cross-session state file so the agent can orient itself on the first session:

```markdown
# PROGRESS.md
<!-- Agent cross-session memory. Read at session start. Updated at session end. -->

## Current Status
**Last updated:** [TODAY]
**Overall project phase:** [e.g. Initial setup / Feature development / Stabilisation]

## What's Working
- [List features that are verified working]

## In Progress
- [Current task, including specific file names and what's done vs pending]

## Known Broken / Blocked
- [CRITICAL: anything broken — agent must fix before building on it]

## Next Steps (Prioritised)
1. [Most important]
2. [Second]

## Recent Decisions
| Date | Decision | Rationale | ADR |
|------|----------|-----------|-----|
| [date] | [decision] | [why] | [ADR link or —] |
```

### Step 10 — Verify: Run the Orientation Test

Start a fresh Claude Code session and type this prompt:

```
Read PROGRESS.md, then run ./scripts/init.sh and report any failures.
Then tell me: what is this project, what's the tech stack, and what's the current status?
```

If Claude correctly describes your project from AGENTS.md and PROGRESS.md, and init.sh
passes, the setup is working. Fix any failures before proceeding to actual work.

---

## 3. AGENTS.md Template

Copy this template and replace every `[PLACEHOLDER]` with real values.
The comments explain what each section does. Delete the comments once filled in.

```markdown
# AGENTS.md
<!-- Universal agent memory. Loaded by Claude Code, GitHub Copilot, Cursor, Windsurf, and Codex.
     Keep under 100 lines. Task-specific guidance belongs in skills or .claude/rules/. -->

## Project Overview
[PROJECT_NAME] — [ONE_SENTENCE_DESCRIPTION].
Stack: [LANGUAGE + VERSION], [FRAMEWORK], [KEY_LIBRARIES].

## Tech Stack
- Language: [e.g. Python 3.11+]
- Web server: [e.g. FastAPI + Uvicorn (port 8080)]
- Database: [e.g. PostgreSQL via SQLAlchemy]
- Testing: [e.g. pytest — N tests, zero real API calls]
- Linting: [e.g. Ruff (line-length = 100)]
- Docs: [e.g. MkDocs Material — source in mk-docs/]
- Deployment: [e.g. Docker + GCP Cloud Run]

## Build & Test Commands
```bash
[INSTALL_COMMAND]   # e.g. pip install -e ".[dev]"
[DEV_COMMAND]       # e.g. uvicorn app:app --reload
[TEST_COMMAND]      # e.g. pytest -v
[LINT_COMMAND]      # e.g. ruff check .
[LINT_FIX_COMMAND]  # e.g. ruff check . --fix
```

## Code Standards
- [PROJECT_SPECIFIC_RULE_1]  (e.g. "Place all external API calls in services/ only")
- [PROJECT_SPECIFIC_RULE_2]  (e.g. "Use Pydantic models for all API request/response types")
- [PROJECT_SPECIFIC_RULE_3]  (e.g. "Mock all external API calls in tests")
- Run [LINT_COMMAND] and confirm clean before marking any task complete
- Run [TEST_COMMAND] and confirm all tests pass before marking any task complete

## Testing Requirements
- All new features require tests before the task is marked complete
- Test naming: test_[function]_[scenario]
- Test files mirror source: [e.g. tests/test_services.py for services/]
- Confirm tests pass by reading the test output — never assume they pass

## Definition of Done
A task is complete only when ALL of the following are true:
1. [TEST_COMMAND] passes (all N tests)
2. [LINT_COMMAND] passes (linter clean)
3. Docs updated if any public behaviour changed
4. PROGRESS.md updated with what was done
5. ./scripts/init.sh passes end-to-end

## Session Start Protocol
1. Read PROGRESS.md for current project state
2. Run ./scripts/init.sh to verify the app is healthy
3. Fix any failures BEFORE starting new work

## Key Directories
```
[BACKEND_DIR]/          → [description]
[SERVICES_DIR]/         → [description, e.g. "All external API wrappers"]
[TESTS_DIR]/            → [description]
docs/adr/               → Architecture Decision Records (read before structural decisions)
docs/research/          → Implementation research notes (read before using external APIs)
.claude/                → Agent skills, subagents, rules, and hooks
```

## What the Agent Must Know Before Acting
- External library/API usage → check docs/research/ first; create note if missing
- Architectural decisions → check docs/adr/ before changing structure
- Environment → requires [ENV_FILE] with [REQUIRED_KEYS] (see [EXAMPLE_FILE])
```

**100-line rule:** If your AGENTS.md exceeds 100 lines, move content to:
- `.claude/rules/` for path-scoped standards (with `paths:` frontmatter)
- `.claude/agents/` for subagent-specific knowledge
- `.claude/skills/` for procedural task knowledge

**Positive voice rule:** Every instruction should say what TO DO.

| Instead of... | Write... |
|---------------|----------|
| "Don't put API calls in agent files" | "Place all API calls in services/ only" |
| "Never use raw dicts for API responses" | "Use Pydantic models for all API response types" |
| "Don't skip tests" | "Run the test suite and confirm it passes before marking complete" |

---

## 4. hooks.json Template

Place this file at `.claude/hooks.json`. Replace the linter command for your stack.

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "ruff check . --quiet && echo 'Lint: OK'"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "ruff check \"${CLAUDE_TOOL_INPUT_FILE_PATH}\" --quiet 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

**Replace `ruff check` with your linter:**

| Stack | Linter command |
|-------|---------------|
| Python (Ruff) | `ruff check . --quiet` |
| Python (flake8) | `flake8 . --quiet` |
| JavaScript/TypeScript | `npx eslint . --quiet` |
| Go | `golangci-lint run --quiet` |
| Ruby | `rubocop --format quiet` |

**Why Stop = lint only (not tests):**

The Stop hook runs after every agent turn. Your test suite (e.g. pytest with 300+ tests)
may take 30-60 seconds. Running it on every turn would make the agent 5-10x slower and
cause timeouts on large suites. Reserve full tests for:
- The session init script (`./scripts/init.sh`)
- Pre-commit hooks (`git commit` triggers pytest)
- CI/CD pipelines

The Stop hook linter is fast (< 2 seconds) and catches the most common agent error:
writing syntactically broken or style-violating Python/JS. It feeds linter output back
to the agent so it can self-correct in the same turn.

**PostToolUse per-file linting:**

The `${CLAUDE_TOOL_INPUT_FILE_PATH}` variable is injected by Claude Code with the path
of the file just written or edited. This runs a fast single-file lint rather than
scanning the whole project, making it nearly instant (< 0.5s per file).

---

## 5. The 5 Daily Workflows

Use these exact prompts at the start of each workflow. Copy and paste them.

### Workflow 1 — Session Start

```
Read PROGRESS.md for current project state, then run ./scripts/init.sh and report
any failures before we begin.
```

What this does: Orients the agent from your cross-session memory file, then runs
the smoke test to confirm the project is in a working state. If init.sh fails,
the agent addresses the failure before doing anything else.

### Workflow 2 — Feature Implementation (Plan Mode)

```
Use Plan mode to create an implementation plan for [FEATURE_DESCRIPTION].
Check docs/adr/ for any relevant architectural decisions first.
Do not write any code until I have approved the plan.
```

What this does: Forces the agent into planning before coding. The agent reads
your ADRs to avoid re-making past decisions, produces a written plan for you
to review, and only starts coding after approval.

### Workflow 3 — External Library Research

```
Before implementing [FEATURE], check docs/research/ for existing notes on [LIBRARY].
If no current note exists, use the research-assistant subagent to research the
official docs for [LIBRARY] v[VERSION] and create a research note at
docs/research/[library].md using RESEARCH-TEMPLATE.md.
Then implement based on the research note, not from memory.
```

What this does: Prevents hallucinated API calls. The agent checks your research
knowledge base first, creates a pinned-version research note if one is missing,
and implements only after grounding itself in the actual current API.

### Workflow 4 — Session End (Handoff)

```
Use the session-handoff skill to update PROGRESS.md with what was done this session,
what's still in progress (with specific file names and what's done vs pending),
any known broken or blocked items, and the prioritised next steps.
```

What this does: Writes a structured handoff document so the next session can
orient instantly. The session-handoff skill enforces the format defined in
`.claude/skills/session-handoff/SKILL.md`.

### Workflow 5 — Code Review Before Commit

```
Use the code-reviewer subagent to review my changes before I commit.
Run git diff HEAD and check the output against our standards.
```

What this does: Invokes the `code-reviewer.md` subagent, which reads the diff
and checks it against your project-specific review checklist (architecture,
tests, linting, no secrets, docs updated). Returns pass/warning/blocking
for each item.

---

## 6. What Cannot Be Automated

Some parts of an agentic setup require manual human action. No prompt or hook can do these.

| Item | Why Manual | What to Do Instead |
|------|-----------|-------------------|
| CodeScene / code health scanning | Requires external account + repo connection | Sign up at codescene.io, connect repo manually, aim for score 9.5+ |
| Proxy logging between Claude and the API | Requires modifying `ANTHROPIC_BASE_URL` at the OS level | Set `export ANTHROPIC_BASE_URL=http://localhost:8080` to a local proxy like mitmproxy; inspect traffic to debug why the agent ignores instructions |
| `opusplan` alias for Plan mode | Shell alias must be set in your shell profile | Add `alias opusplan='claude --model claude-opus-4-6 --plan'` to `~/.zshrc` or `~/.bashrc` |
| Full pytest in Stop hook | Test suites taking > 5 seconds cause hook timeouts and break the agent loop | Keep Stop hook to lint only; run tests in init.sh, pre-commit hooks, and CI |
| `.env` file with real API keys | Secret management — cannot be in git | Copy `.env.example` → `.env` and fill in manually; use a password manager to share with team |
| First ADR write | Architecture decisions require human judgment | Write `docs/adr/ADR-001-[topic].md` manually using `ADR-000-template.md`; thereafter the agent will read ADRs before decisions |

---

## 7. Master Checklist

Use this to verify the setup is complete before running your first agent session.

### File Package

| Item | Done? |
|------|-------|
| `AGENTS.md` created and filled in (under 100 lines, positive voice rules) | |
| `CLAUDE.md` created with `@import AGENTS.md` and Claude-specific behaviours | |
| `CLAUDE.local.md` created at project root with local overrides | |
| `CLAUDE.local.md` added to `.gitignore` | |
| `PROGRESS.md` initialised with current project state | |
| `CONTRIBUTING.md` filled in with coding patterns | |

### Scripts

| Item | Done? |
|------|-------|
| `scripts/init.sh` created and made executable (`chmod +x`) | |
| init.sh checks: environment, dependencies, linter, tests | |
| init.sh exits with non-zero on any failure | |

### Claude Configuration

| Item | Done? |
|------|-------|
| `.claude/hooks.json` configured with Stop + PostToolUse linter hooks | |
| `.claude/agents/code-reviewer.md` filled in with project-specific review rules | |
| `.claude/agents/research-assistant.md` filled in with project libraries | |
| `.claude/rules/api-standards.md` filled in with `paths:` frontmatter | |
| `.claude/rules/test-standards.md` filled in with `paths:` frontmatter | |
| `.claude/skills/session-handoff/SKILL.md` copied as-is | |

### Subdirectory Files

| Item | Done? |
|------|-------|
| `backend/CLAUDE.md` or equivalent created with directory architecture | |
| `frontend/CLAUDE.md` or equivalent created with directory architecture | |
| `backend/README.md` and `frontend/README.md` created for orientation | |
| `docs/adr/ADR-000-template.md` copied | |
| `docs/research/RESEARCH-TEMPLATE.md` copied | |
| `docs/research/INDEX.md` created with priority libraries listed | |

### Multi-Tool Integration

| Item | Done? |
|------|-------|
| `.github/copilot-instructions.md` symlinked to `AGENTS.md` (if using Copilot) | |
| `.cursor/rules/main.mdc` symlinked to `AGENTS.md` (if using Cursor) | |

### Verification

| Item | Done? |
|------|-------|
| `./scripts/init.sh` runs clean with exit 0 | |
| Orientation test passed: agent correctly describes project from AGENTS.md + PROGRESS.md | |
| Agent correctly routes to `code-reviewer` subagent on "review my changes" | |
| Agent correctly routes to `research-assistant` subagent on "research [library]" | |
| Hooks verified: linter output appears in agent turn after editing a Python/JS file | |

---

## Key Design Decisions Captured Here

These are the non-obvious findings that make this setup work. Refer back if you start
questioning any of the choices.

**Why AGENTS.md under 100 lines?**
Claude Code wraps CLAUDE.md content in a system reminder that tells the model to skip
content not relevant to the current task. At 400+ lines, instruction adherence drops to
71%. At under 100 lines, it stays at 92%+. Move everything non-essential to rules files,
skill files, or subdirectory CLAUDE.md files (which are lazy-loaded and don't consume
context until needed).

**Why positive voice?**
LLMs follow positive instructions more reliably than negative ones. "Place API calls in
services/ only" has higher adherence than "Never put API calls in agent files". Rewrite
every negative rule as a positive direction.

**Why is CLAUDE.local.md at the project root, not in .claude/?**
Claude Code's file loading logic looks for CLAUDE.local.md at the project root only.
Files placed in subdirectories are not auto-loaded as personal overrides.

**Why lint in Stop hook but not tests?**
The Stop hook runs synchronously after every agent turn. A 60-second test suite on every
turn makes the agent 5-10x slower and causes hook timeouts. Lint runs in under 2 seconds
and catches the most common agent errors. Full tests run in init.sh (session start) and
pre-commit hooks (before commits).

**Why haiku model for the research-assistant subagent?**
Research tasks (web browsing, reading docs, creating notes) involve many tool calls but
minimal complex reasoning. Haiku is 10x cheaper than Sonnet with comparable performance
on retrieval and summarisation tasks. Reserve Sonnet for implementation and code review.

**Why does `paths:` scope rules files (not `globs:`)?**
The Claude Code rules file frontmatter uses `paths:` as the key for path-scoped rules.
Using `globs:` will silently fail — the rules file will be ignored. Always use `paths:`.

**Why subdirectory CLAUDE.md files instead of one big root file?**
Subdirectory CLAUDE.md files (e.g. `backend/CLAUDE.md`) are lazy-loaded: they only enter
context when Claude is editing files in that directory. This keeps the context window clean
for frontend work (backend context not loaded) and backend work (frontend context not
loaded). Ideal for monorepos with distinct codebases in one repo.
