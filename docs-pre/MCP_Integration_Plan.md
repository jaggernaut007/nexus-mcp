# MCP Integration Plan for CR8

**Date:** 2026-03-03
**Status:** Ready to implement
**Author:** Claude Code session

---

## Why MCP Servers?

MCP (Model Context Protocol) servers are bridges that connect Claude Code to external tools, APIs, and data sources. They let the AI assistant directly interact with GitHub, browser automation, live documentation, error monitoring, and more — without leaving the terminal.

CR8 already has a mature agentic setup (8 subagents, 4 skills, 2 rules, hooks), but no MCP servers beyond Notion (connected via claude.ai). Key gaps:

- **Hallucination risk**: `docs/research/INDEX.md` lists 7 libraries with no research notes (LangGraph, ChromaDB, OpenAI SDK, Tavily, HeyGen, fpdf2, python-pptx). Claude guesses at API methods.
- **No browser testing**: `CLAUDE.md` says "use browser automation to test as a real user would" but no browser tool is connected.
- **Manual GitHub workflow**: PR reviews, issue tracking, CI status all require switching to browser or running `gh` CLI commands individually.

---

## Complete MCP Server Landscape

### All MCPs Researched

| # | MCP Server | What It Does | Cost | API Key? | Verdict |
|---|-----------|-------------|------|----------|---------|
| 1 | **Context7** | Injects version-specific library docs into prompts | Free | Optional (higher rate limits) | **IMPLEMENT — Phase 1** |
| 2 | **GitHub MCP** | PR management, issues, CI status, code search, Dependabot | Free | OAuth (browser login) | **IMPLEMENT — Phase 1** |
| 3 | **Playwright MCP** | Browser automation, E2E testing, screenshots | Free | None (local) | **IMPLEMENT — Phase 1** |
| 4 | **Sequential Thinking** | Structured step-by-step reasoning with branching & revision | Free | None (local) | **IMPLEMENT — Phase 2** |
| 5 | **FastAPI-MCP** | Expose CR8's own API endpoints as MCP tools | Free | None (local) | **IMPLEMENT — Phase 2** |
| 6 | **Sentry MCP** | Pull error traces, stack traces, issue triage into Claude | Free tier | OAuth | **IMPLEMENT — Phase 3** |
| 7 | **Notion MCP** | Read/write Notion pages, databases, comments | Included | claude.ai connected | **ALREADY ACTIVE** |
| 8 | Knowledge Graph Memory | Persistent entity/relation memory across sessions | Free | None | SKIP — PROGRESS.md + MEMORY.md sufficient |
| 9 | Linear MCP | Issue tracking from terminal | Free | OAuth | SKIP — project uses GitHub Issues |
| 10 | Docker MCP | Container management, logs, inspection | Free | None | SKIP — Makefile handles Docker |
| 11 | Desktop Commander | Enhanced terminal control, file operations | Free | None | SKIP — redundant with Claude Code built-ins |
| 12 | GitMCP | Auto-MCP server for any GitHub repo's docs | Free | None | SKIP — Context7 covers this need |
| 13 | Apidog MCP | API spec access, testing, documentation | Free tier | API key | SKIP — FastAPI-MCP is more relevant |
| 14 | Slack MCP | Read/send Slack messages, search channels | Free | OAuth | SKIP — not using Slack for CR8 |
| 15 | AWS MCP | AWS service operations via Claude | Free | IAM credentials | SKIP — using GCP, not AWS |

---

## Phase 1 — Implement Now (config-only, no code changes)

### 1. Context7 — Hallucination Prevention

**Problem it solves:** Claude hallucinates API methods for LangGraph, ChromaDB, FastAPI, and OpenAI because training data is stale. The existing `docs/research/` pattern requires manually writing research notes before every implementation.

**What it does:** Pulls version-specific documentation and code examples from a curated database of 1000+ libraries. Two tools:
- `resolve-library-id` — takes "langgraph" and returns the Context7 library ID
- `get-library-docs` — returns relevant docs + code examples (configurable token limit, default 5000)

**How it fits CR8:** Becomes the fast-path in the research-assistant agent workflow. Check Context7 first (instant), fall back to web search + research note only if Context7 doesn't cover it.

**Libraries it covers that CR8 uses:**
- LangGraph, LangChain, langchain-openai
- FastAPI, Uvicorn, Pydantic
- ChromaDB
- python-pptx
- fpdf2
- MoviePy
- PyMuPDF

**Setup:**
```bash
claude mcp add context7 -- npx -y @upstash/context7-mcp
```
- Scope: `local` (this project only)
- Requires: Node.js >= v18
- No API key needed for basic use

**Test it:** "Using context7, look up the LangGraph StateGraph API for conditional edges"

---

### 2. GitHub MCP — PR & Issue Workflow

**Problem it solves:** Managing PRs, issues, and CI status requires switching to browser or running individual `gh` CLI commands. No way for Claude to proactively check CI status or search issues during coding.

**What it does:** Full GitHub API access:
- Browse repos, search code, analyse commits
- Create/update/review issues and PRs
- Monitor GitHub Actions workflow runs, analyse build failures
- Check Dependabot security alerts
- Manage releases

**How it fits CR8:** The project is on GitHub. `settings.local.json` already whitelists `gh pr diff`, `gh pr checks`, etc. GitHub MCP gives deeper, more natural access.

**Setup:**
```bash
claude mcp add --scope user --transport http github https://api.githubcopilot.com/mcp/
```
Then authenticate inside Claude Code:
```
/mcp → select GitHub → follow browser OAuth flow
```
- Scope: `user` (works across all your projects)
- No PAT needed — uses OAuth

**Alternative (Personal Access Token):**
```bash
claude mcp add --scope user --env GITHUB_PERSONAL_ACCESS_TOKEN=ghp_your_token github -- npx -y @github/mcp-server
```
Create token at `github.com/settings/tokens` with scopes: `repo`, `read:org`, `read:project`.

**Test it:** "Show me open issues on this repo"

---

### 3. Playwright MCP — Browser Testing

**Problem it solves:** `CLAUDE.md` says "use browser automation to test as a real user would" but there's no browser tool connected. Testing the web UI at `localhost:8080` requires manual verification.

**What it does:** Full browser automation via accessibility snapshots:
- Navigate to URLs, click elements, fill forms
- Take screenshots, read page content
- Run end-to-end test flows
- Cross-browser support (Chrome, Firefox, Safari)
- 143 device emulations (iPhone, iPad, Pixel, desktop)

**How it fits CR8:** The frontend runs at `localhost:8080` (FastAPI + Jinja2). Claude can now:
- Navigate to login page, enter credentials, verify auth
- Upload a PDF, select output formats, trigger pipeline
- Monitor progress via SSE, verify completion
- Download and verify output files

**Setup:**
```bash
claude mcp add playwright -- npx -y @playwright/mcp@latest
```
- Scope: `local` (this project only)
- Requires: Node.js >= v18
- No API key needed

**Test it:** Start `make dev`, then: "Navigate to http://localhost:8080 and take a screenshot"

---

## Phase 2 — This Week (Sequential Thinking + FastAPI-MCP)

### 4. Sequential Thinking — Architecture Planning

**Problem it solves:** Complex architecture decisions (ADR-001 LangGraph architecture, v0.5 React frontend, quiz platform design) benefit from structured, revisable reasoning rather than stream-of-consciousness.

**What it does:** Provides a single `sequentialthinking` tool that manages structured thought chains:
- Breaks complex problems into numbered steps
- Supports branching to explore alternative solutions
- Allows revising earlier steps as understanding evolves
- Dynamically adjusts the number of steps needed

**How it fits CR8:** Complements the `adr-writer` and `Plan` subagent patterns. Use for:
- ADR-001: LangGraph pipeline architecture decision
- v0.5: React frontend vs. other framework decision
- Quiz platform: Separate LangGraph workflow design
- Adaptive assessment: PPO + DKVMN architecture

**Setup:**
```bash
claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
```
- Scope: `user` (useful across all projects)
- No API key needed

**Test it:** "Use sequential thinking to plan the architecture for CR8's quiz agent"

---

### 5. FastAPI-MCP — CR8 API as MCP Tools (requires ADR-001)

**Problem it solves:** During development, testing the CR8 API requires manual `curl` commands or running the web UI. Claude can't directly call the pipeline endpoints.

**What it does:** Auto-exposes all FastAPI endpoints as MCP tools. Claude can:
- Trigger pipeline runs via `POST /api/jobs`
- Check job status via `GET /api/jobs/{job_id}`
- Download outputs via `GET /api/jobs/{job_id}/download`
- Test authentication flow

**How it fits CR8:** Lets Claude eat its own dog food — call the CR8 API during development to verify features work end-to-end.

**Setup requires code changes:**

**Step 1 — ADR-001** (write before implementing):
```
docs/adr/ADR-001-mcp-integration.md
```
Documents: why we're adding fastapi-mcp, alternatives considered, consequences.

**Step 2 — Add dependency** to `pyproject.toml`:
```
"fastapi-mcp>=0.1",
```

**Step 3 — Mount in `frontend/app.py`** (gated by env var):
```python
import os
if os.getenv("ENABLE_MCP", "false").lower() == "true":
    from fastapi_mcp import FastApiMCP
    mcp = FastApiMCP(app, name="cr8-pipeline")
    mcp.mount()
```

**Step 4 — Create `.mcp.json`** at project root:
```json
{
  "mcpServers": {
    "cr8-api": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**Step 5 — Add to `.env.example`:**
```
ENABLE_MCP=false
```

- `.mcp.json` is safe to commit (no secrets, just localhost URL)
- Gated behind `ENABLE_MCP=true` — off by default, off in Docker/production
- Only works when `make dev` is running

**Test it:** Set `ENABLE_MCP=true` in `.env`, start `make dev`, then: "Call the CR8 API to check its health endpoint"

---

## Phase 3 — When Deploying to Production

### 6. Sentry MCP — Error Monitoring

**When to add:** After Sentry is set up for the GCP Cloud Run deployment.

**What it does:** Pulls error traces, stack traces, and issue data from Sentry directly into Claude. Claude can:
- Retrieve detailed issue info (title, status, level, timestamps, event count, stack traces)
- Query projects for application health overview
- Analyse errors with Sentry's AI-powered Seer analysis

**Setup:**
```bash
claude mcp add --scope user --transport http sentry https://mcp.sentry.dev/mcp
```
Then authenticate via `/mcp` OAuth flow. Free tier available.

---

## What We're NOT Adding (with reasoning)

| MCP Server | Why Skip |
|-----------|---------|
| **Knowledge Graph Memory** | CR8 already has a 3-layer memory system: `PROGRESS.md` (cross-session state), `session-handoff` skill (structured handoff), `.claude/memory/MEMORY.md` (auto-memory). Adding a 4th layer adds complexity without clear benefit. Revisit if a specific pain point emerges. |
| **Linear MCP** | CR8 tracks work via GitHub Issues and PM-Docs/. No Linear account in use. If we adopt Linear, add this then. |
| **Docker MCP** | `Makefile` has `docker-build` and `docker-run`. `deploy.sh` handles GCP. The existing Bash-based workflow is sufficient. |
| **Desktop Commander** | Fully redundant with Claude Code's built-in `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep` tools. Would add a dependency for zero new capability. |
| **GitMCP** | Context7 already injects library docs. GitMCP would provide overlapping coverage. Better to have one authoritative docs source than two competing ones. |
| **Apidog MCP** | FastAPI-MCP is more relevant — it exposes our own API rather than generic API specs. |
| **Slack MCP** | CR8 development doesn't use Slack. No channel to connect to. |
| **AWS MCP** | CR8 deploys to GCP Cloud Run, not AWS. |

---

## Files That Change During Implementation

### Phase 1 (config-only, no code changes needed)

| File | Change | Why |
|------|--------|-----|
| `CLAUDE.md` | Add `## MCP Servers Available` section; update `### Hallucination Prevention` to reference Context7 first | So every session knows what MCPs are available |
| `AGENTS.md` | Add step 3 to Session Start Protocol: "Run `/mcp` to verify MCP servers"; update research-assistant routing to note Context7 fast-path | Universal agent awareness of MCPs |
| `.claude/agents/research-assistant.md` | Add Step 0: "Check Context7 first — if sufficient, summarise and stop" before current Step 1 | Context7 is the fast path; web search is the fallback |
| `CLAUDE.local.md` | Add `## MCP Servers` section listing configured MCPs with scopes | Personal reference for which MCPs are set up |
| `docs/research/mcp-servers.md` | NEW — research note documenting all MCP setup following RESEARCH-TEMPLATE.md | Follows existing research-first pattern |
| `docs/research/INDEX.md` | Add row for MCP Servers research note | Keep index current |

### Phase 2 (code changes)

| File | Change | Why |
|------|--------|-----|
| `docs/adr/ADR-001-mcp-integration.md` | NEW — Architecture Decision Record for fastapi-mcp dependency | ADR-first pattern before new dependency |
| `pyproject.toml` | Add `"fastapi-mcp>=0.1"` to dependencies | New pip dependency |
| `frontend/app.py` | Mount FastAPI-MCP endpoint (gated by `ENABLE_MCP` env var) | Expose API as MCP tools |
| `.mcp.json` | NEW — project-scoped MCP config for cr8-api server | Shareable team config |
| `.env.example` | Add `ENABLE_MCP=false` | Document the new env var |

---

## Implementation Checklist

### Phase 1 — Do Now

- [ ] Run `claude mcp add context7 -- npx -y @upstash/context7-mcp`
- [ ] Run `claude mcp add --scope user --transport http github https://api.githubcopilot.com/mcp/`
- [ ] Run `claude mcp add playwright -- npx -y @playwright/mcp@latest`
- [ ] Authenticate GitHub MCP via `/mcp` OAuth flow
- [ ] Verify all 3 show green in `/mcp`
- [ ] Update `CLAUDE.md` — add MCP section + update hallucination prevention
- [ ] Update `AGENTS.md` — add `/mcp` to session start + update routing table
- [ ] Update `.claude/agents/research-assistant.md` — add Context7 Step 0
- [ ] Update `CLAUDE.local.md` — add MCP status section
- [ ] Create `docs/research/mcp-servers.md` research note
- [ ] Update `docs/research/INDEX.md` with new row
- [ ] Run `make test` — confirm 362 tests still pass
- [ ] Run `make lint` — confirm ruff clean

### Phase 2 — This Week

- [ ] Run `claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking`
- [ ] Write `docs/adr/ADR-001-mcp-integration.md` (use adr-writer agent)
- [ ] Add `fastapi-mcp>=0.1` to `pyproject.toml`
- [ ] Add MCP mount to `frontend/app.py` (gated by env var)
- [ ] Create `.mcp.json` at project root
- [ ] Add `ENABLE_MCP=false` to `.env.example`
- [ ] Run `make install` to install new dependency
- [ ] Run `make test` — confirm all tests still pass

### Phase 3 — When Ready

- [ ] Set up Sentry on GCP Cloud Run
- [ ] Run `claude mcp add --scope user --transport http sentry https://mcp.sentry.dev/mcp`
- [ ] Authenticate via OAuth

---

## How MCPs Integrate with Existing Ecosystem

| Existing Feature | Impact | Conflict? |
|-----------------|--------|-----------|
| Hooks (ruff on Stop, per-file ruff on Edit/Write) | No impact — MCP tools don't trigger file-edit hooks | None |
| Pre-commit docs check hook | No impact — MCP doesn't affect git commit flow | None |
| `settings.local.json` permissions | No change needed — MCP servers run in their own process | None |
| `research-assistant` agent | Enhanced — Context7 becomes fast path before web search | Complementary |
| `code-reviewer` agent | Enhanced — can use GitHub MCP for PR context | Complementary |
| `session-handoff` skill | No impact — MCP is session-level, not state-level | None |
| `init.sh` smoke test | No change — MCP checked via `/mcp`, not init.sh | None |
| `PROGRESS.md` memory | No impact — MCPs are tools, not memory | None |

---

## Security

- **No API keys in project files** — All MCP auth via Claude Code's built-in OAuth or `--env` flag (stored in `~/.claude.json`)
- **`.mcp.json` is safe to commit** — Only contains `http://localhost:8080/mcp`, no secrets
- **FastAPI-MCP gated** — `ENABLE_MCP=false` by default, off in Docker/production
- **Playwright runs locally** — No external network calls, only accesses localhost
- **GitHub OAuth tokens** — Stored in system keychain by Claude Code, not in any project file
- **No new entries in `.env`** — MCP auth is separate from application secrets

---

## Expected Productivity Gains

| MCP | Time Saved Per Use | Frequency | Weekly Impact |
|-----|-------------------|-----------|---------------|
| Context7 | 5-10 min (vs. manual docs search + research note) | 5-10x/week | 25-100 min |
| GitHub MCP | 2-5 min (vs. browser switch for PR/issue management) | 10-20x/week | 20-100 min |
| Playwright | 3-8 min (vs. manual browser testing) | 3-5x/week | 9-40 min |
| Sequential Thinking | 10-20 min (vs. unstructured architecture reasoning) | 1-2x/week | 10-40 min |
| FastAPI-MCP | 2-5 min (vs. manual curl/browser API testing) | 5-10x/week | 10-50 min |
| **Total estimated** | | | **~1.5-5.5 hours/week** |

---

## Quick Reference Card

```
# Phase 1 — Run these 3 commands now:
claude mcp add context7 -- npx -y @upstash/context7-mcp
claude mcp add --scope user --transport http github https://api.githubcopilot.com/mcp/
claude mcp add playwright -- npx -y @playwright/mcp@latest

# Phase 2 — Run this week:
claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking

# Phase 3 — When deploying:
claude mcp add --scope user --transport http sentry https://mcp.sentry.dev/mcp

# Check status anytime:
/mcp
```

---

## Sources

- [Context7 MCP Server (Upstash)](https://github.com/upstash/context7)
- [GitHub MCP Server (Official)](https://github.com/github/github-mcp-server)
- [Playwright MCP Server (Microsoft)](https://github.com/microsoft/playwright-mcp)
- [Sequential Thinking MCP Server (Anthropic)](https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking)
- [FastAPI-MCP (tadata-org)](https://github.com/tadata-org/fastapi_mcp)
- [Sentry MCP Server](https://docs.sentry.io/product/sentry-mcp/)
- [Official MCP Registry](https://registry.modelcontextprotocol.io/)
- [MCP Hub Directory (8000+ servers)](https://mcpdir.dev/)
- [awesome-mcp-servers (GitHub)](https://github.com/punkpeye/awesome-mcp-servers)
