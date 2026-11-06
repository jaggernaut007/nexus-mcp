# Submission texts

Ready-to-paste text for each listing. Use it **after** the 2.3.0 release is on PyPI. Every
install route pins `nexus-mcp-ci==2.3.0`, and `uvx nexus-mcp-ci setup` exists only from 2.3.0.
A reviewer who tests an install before the release sees a failure. See
[DISTRIBUTION.md](DISTRIBUTION.md) for the steps and the order.

## One-line blurb

Local code search, call graph and project memory for AI coding agents. Runs on your machine,
needs no API key, and makes no network call of its own. MIT license.

## Short description (about 300 characters)

Nexus-MCP is a local MCP server that gives an AI coding agent hybrid code search, a static call
graph, the code of any symbol and memory that lasts across sessions. The agent reads fewer
files, so it uses fewer tokens. It runs on your machine. No API key and no telemetry. Install
with one command: `uvx nexus-mcp-ci setup`.

## Links to give

| Item | Value |
|---|---|
| Repository | `https://github.com/jaggernaut007/Nexus-MCP` |
| Website | `https://jaggernaut007.github.io/nexus-mcp/` |
| Package | `https://pypi.org/project/nexus-mcp-ci/` |
| Logo (400x400) | `site/logo-400.png` (also `plugin/assets/logo.png`) |
| Install instructions for an agent | `llms-install.md` |
| License | MIT |
| MCP Registry name | `io.github.jaggernaut007/nexus-mcp` |

## Cline (`cline/mcp-marketplace`, a GitHub issue)

Title: `Add Nexus-MCP (local code search, call graph and memory)`

Body: use the short description above, then: "Repository: <repo URL>. Logo attached (400x400).
`llms-install.md` in the repository tells an agent how to install it. I tested the install with
Cline." Test with Cline first. Cline asks for that test.

## Docker MCP Catalog (`docker/mcp-registry`, a pull request)

See the steps in [DISTRIBUTION.md](DISTRIBUTION.md). Category: `devops` or `developer-tools`
(read the current category list in the registry first). PR title: `Add nexus-mcp`. Use the
image `ghcr.io/jaggernaut007/nexus-mcp:2.3.0`, and a `tools.json` with the 8 tools.

## Cursor Marketplace (`cursor.com/marketplace/publish`)

Give the repository URL. The repository has `.cursor-plugin/marketplace.json` at the root and
`plugin/.cursor-plugin/plugin.json`. Review is manual and has no set time.

## Anthropic plugin directory (`claude.ai/directory/manage`)

Needs a paid Claude plan. Give the repository URL, the short description, the logo and the
license. The plugin is `plugin/` (manifest `plugin/.claude-plugin/plugin.json`).

## Codex, GitHub Copilot, Gemini CLI

- **Gemini CLI:** `gemini-extension.json` is at the repository root. Users install with
  `gemini extensions install https://github.com/jaggernaut007/Nexus-MCP`. A gallery may list
  repositories that have the topic `gemini-cli-extension`: check the current Gemini CLI docs,
  then add the topic in the repository settings.
- **Codex and Copilot:** not added yet. Read the current plugin format of each tool first. Both
  can reuse `plugin/skills/` and `plugin/.mcp.json`.
- **Antigravity:** `setup` does not know this client yet. Add it after you check its MCP config
  path and skill folder.

## MCP directories

| Directory | How |
|---|---|
| Official MCP Registry | automatic on release (`publish.yml`) |
| Glama, awesome-mcp-servers (`punkpeye`) | already listed |
| mcp.so | web form or issue. Use the short description. |
| mcpservers.org | web form. Use the short description. |
