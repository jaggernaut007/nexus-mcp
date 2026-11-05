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
| Smithery | Smithery users | the same `.mcpb` file | owner: `smithery mcp publish` (see below) |
| Gemini CLI extension | Gemini CLI | `gemini-extension.json`, `gemini-context.md` | the repository itself |
| VS Code and Cursor buttons | VS Code, Cursor | links in the README and the site | the repository itself |
| Docker image (GHCR) | container users | `docker/Dockerfile.stdio` | `publish.yml` on a release |
| Glama | Glama users | `glama.json`, `Dockerfile` | Glama rebuilds from the repository |
| GitHub Action | CI | `action.yml`, [GITHUB-ACTION.md](GITHUB-ACTION.md) | owner: tick "Publish to Marketplace" |
| Cline marketplace | Cline | `llms-install.md` | owner: open an issue (see below) |
| Context7 | docs lookup | `context7.json` | owner: submit once |

## What the release does by itself

`publish.yml` runs when you publish a GitHub release. It builds and publishes the wheel to PyPI,
publishes `server.json` to the MCP Registry, builds `nexus-mcp.mcpb` and attaches it to the
release, and pushes `ghcr.io/jaggernaut007/nexus-mcp:<version>` and `:latest`.

## Owner steps

Nothing below runs by itself. Each one needs your account.

1. **Polar.** Make the organization, the product ($5 per month and $50 per year) and the licence
   key benefit (prefix `NEXUS`, activation limit 3). Put the organization id into `POLAR_ORG_ID`
   in `src/nexus_mcp/licensing.py` and the checkout link into `CHECKOUT_URL`, then test with the
   Polar sandbox: `NEXUS_LICENSE_API=https://sandbox-api.polar.sh`. See
   [ADR-022](adr/ADR-022-commercial-licence-check.md).
2. **Lawyer.** Review [COMMERCIAL-LICENSE.md](../COMMERCIAL-LICENSE.md) before the first sale.
3. **Smithery.** Smithery serves a local stdio server as an MCPB bundle. After a release, download
   `nexus-mcp.mcpb` from it and run `smithery mcp publish ./nexus-mcp.mcpb -n jaggernaut007/nexus-mcp`.
   The old `smithery.yaml` is gone: Smithery now accepts only HTTP servers there, and a hosted
   server cannot see your code.
4. **GitHub Marketplace.** On the release page, tick "Publish this Action to the GitHub
   Marketplace". The action needs a unique name there.
5. **Cline.** Open an issue at `cline/mcp-marketplace` with the repository URL, a 400x400 PNG logo,
   and the reason. Before you do, give Cline the `README.md` and `llms-install.md` and watch it
   install the server. Cline asks for that test.
6. **Docker MCP Catalog (optional).** The catalog builds the image from the root `Dockerfile` of
   the repository and expects a plain stdio entry point. The root `Dockerfile` wraps the server in
   `mcp-proxy` for Glama, so it does not fit. Either point the catalog entry at
   `docker/Dockerfile.stdio`, or publish the GHCR image and let users add it by hand:
   `docker run -i --rm -v "$PWD:/workspace" -v nexus-data:/data ghcr.io/jaggernaut007/nexus-mcp`.
   Read the current `CONTRIBUTING.md` of `docker/mcp-registry` before you open a pull request.
7. **Context7.** Submit the repository once at context7.com.

## Pins

Every launch command pins the version: the plugin, `gemini-extension.json`, `mcpb/` and
`server.json`. `python scripts/bump_version.py <new>` changes all of them at once, and
`python scripts/check_docs.py` fails when one is out of step. See [RELEASING.md](RELEASING.md).
