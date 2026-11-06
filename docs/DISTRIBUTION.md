# Distribution channels

How a user gets Nexus-MCP, and what the owner must do for each channel. The default path for a
new user is in the [README](../README.md#quickstart-60-seconds): ask the agent to install it, or
run `uvx nexus-mcp-ci setup`.

| Channel | Who it serves | Files in this repo | Published by |
|---|---|---|---|
| `uvx nexus-mcp-ci setup` | every client | `src/nexus_mcp/setup_clients.py`, `llms-install.md` | PyPI release |
| PyPI | everyone | `pyproject.toml` | `publish.yml` on a release |
| Claude Code plugin | Claude Code | `.claude-plugin/`, `plugin/` | the repository itself |
| MCP Registry | registry clients | `server.json` | `publish.yml` on a release |
| Desktop Extension (`.mcpb`) | Claude Desktop | `mcpb/` | `publish.yml` attaches it to the release |
| Gemini CLI extension | Gemini CLI | `gemini-extension.json`, `gemini-context.md` | the repository itself |
| VS Code and Cursor buttons | VS Code, Cursor | links in the README and the site | the repository itself |
| Docker image (GHCR) | container users | `docker/Dockerfile.stdio` | `publish.yml` on a release |
| Glama | Glama users | `glama.json`, `Dockerfile` | Glama rebuilds from the repository |
| GitHub Action | CI | `action.yml`, [GITHUB-ACTION.md](GITHUB-ACTION.md) | owner: tick "Publish to Marketplace" |
| Cline marketplace | Cline | `llms-install.md`, `site/logo-400.png` | owner: open an issue (see below) |
| Docker MCP Catalog | Docker Desktop users | `docker/Dockerfile.stdio` | owner: pull request (see below) |
| Context7 | docs lookup | `context7.json` | done: the repository is listed |

Smithery is not a channel any more. On 31 October 2026 it shut down its CLI, API and accounts, and
it stays only as a curated public directory (<https://www.smithery.ai/future>). There is nothing to
publish there.

## What the release does by itself

`publish.yml` runs when you publish a GitHub release. It builds and publishes the wheel to PyPI,
publishes `server.json` to the MCP Registry, builds `nexus-mcp.mcpb` and attaches it to the
release, and pushes `ghcr.io/jaggernaut007/nexus-mcp:<version>` and `:latest`.

## Owner steps

Nothing below runs by itself. Each one needs your account.

1. **GitHub social preview.** Upload `site/social-preview.png` (1280x640) under the repository
   Settings, "Social preview". GitHub has no API for it.
2. **GitHub Marketplace.** On the release page, tick "Publish this Action to the GitHub
   Marketplace". The action needs a unique name there.
3. **Cline.** Open an issue at `cline/mcp-marketplace` with the repository URL, the logo
   `site/logo-400.png` (400x400) and the reason. Before you do, give Cline the `README.md` and
   `llms-install.md` and watch it install the server. Cline asks for that test.
4. **Docker MCP Catalog (optional).** The registry takes a pull request that adds
   `servers/nexus-mcp/server.yaml`.
   - The registry builds the image from the **root** `Dockerfile`. Ours wraps the server in
     `mcp-proxy` for Glama, so use the second route: point the entry at the GHCR image that
     `publish.yml` pushes. That route skips Docker's signing and SBOM benefits.
   - Prerequisites: Docker Desktop, Go 1.24 or newer, and [Task](https://taskfile.dev/).
   - Fork `docker/mcp-registry`, then from the clone run
     `task create -- --category <category> --image ghcr.io/jaggernaut007/nexus-mcp:<version> https://github.com/jaggernaut007/Nexus-MCP`.
   - Add a `tools.json` next to `server.yaml` with the 8 tools, so that the build does not have
     to start the server. Test with `task catalog -- nexus-mcp`, then
     `docker mcp catalog import $PWD/catalogs/nexus-mcp/catalog.yaml`.
   - Open the pull request. The entry is live in 24 hours after the Docker team approves it.
   - Read the current `CONTRIBUTING.md` of `docker/mcp-registry` first. It changes.

## Plugin and skill routes still open

Checked on 2026-11-06 from public docs and third-party guides. Rules change, so read the current
page before you submit. The skill is one `SKILL.md`, and it is shipped in the wheel for `setup`.

| Route | What to add | Where to submit |
|---|---|---|
| Anthropic plugin directory | nothing: `plugin/` and `.claude-plugin/` already fit | `claude.ai/directory/manage` (needs a paid Claude plan). `claude-plugins-official` does not take portal submissions. Reviews can be slow. |
| Cursor Marketplace | done: `.cursor-plugin/marketplace.json`, `plugin/.cursor-plugin/plugin.json` | owner: `cursor.com/marketplace/publish` after the release. Manual review, no set time. MIT meets the licence rule. |
| Codex plugin marketplace | a `.codex-plugin/plugin.json` (from a third-party guide: verify in the OpenAI Codex docs) | the Codex marketplace flow |
| GitHub Copilot and VS Code plugins | a `.github/plugin/plugin.json` (from a third-party guide: verify) | a `marketplace.json` in the repository |
| Skill directories | nothing: `SKILL.md` already follows the shared format | `npx skills add jaggernaut007/Nexus-MCP` (untested); skill lists that take a GitHub URL; Agensi.io (a paid-skill shop, optional) |

Also after the merge: GitHub detects the license of the repository as MIT, and the Sponsor
button already works.

## Pins

Every launch command pins the version: the plugin, `gemini-extension.json`, `mcpb/` and
`server.json`. `python scripts/bump_version.py <new>` changes all of them at once, and
`python scripts/check_docs.py` fails when one is out of step. See [RELEASING.md](RELEASING.md).
