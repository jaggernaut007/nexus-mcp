# Submission texts

Ready-to-paste text for each listing. Release 2.4.1 is live on every automatic channel
(checked 2026-11-08): PyPI, the official MCP Registry, the GHCR image and the `.mcpb` file on the
GitHub release. Every install route pins `nexus-mcp-ci==2.4.1`. See
[DISTRIBUTION.md](DISTRIBUTION.md) for the steps and the order.

## Status of each listing

| Listing | Status |
|---|---|
| PyPI (`nexus-mcp-ci`) | 2.4.1, automatic on release |
| Official MCP Registry (`io.github.jaggernaut007/nexus-mcp`) | 2.4.1 is the latest, automatic on release |
| GHCR image `ghcr.io/jaggernaut007/nexus-mcp` | tags `2.4.1`, `2.3.0` and `latest`, public |
| Claude Desktop extension (`nexus-mcp.mcpb`) | attached to the GitHub release |
| Glama, awesome-mcp-servers (`punkpeye`), Context7 | listed |
| Cline marketplace | to do (test with Cline first) |
| Docker MCP Catalog | to do |
| Cursor Marketplace | submitted 2026-11-08, in review |
| Anthropic plugin directory | refused 2026-11-08: local MCP servers only from a Verified organization (see below) |
| mcp.so, mcpservers.org | to do (web forms) |

## One-line blurb

Local code search and call graph for AI coding agents, in 10 languages. Runs on your machine,
needs no API key, and makes no network call of its own. MIT license.

## Short description (about 300 characters)

Nexus-MCP is a local MCP server that gives an AI coding agent hybrid code search, a static call
graph and the code of any symbol. The agent reads fewer files, so it uses fewer tokens. Call
graphs for 10 languages. It runs on your machine. No API key and no telemetry. Install with one
command: `uvx nexus-mcp-ci setup`.

## What it does (for a longer form)

- **5 tools by default:** `index`, `search`, `find_symbol`, `graph` and `map`. Set
  `NEXUS_EXTRA_TOOLS=all` to also show `status`, `analyze` and `memory`. Fewer tools means less
  fixed text on each turn of the agent.
- **Search:** hybrid vector and keyword search. A name in the query brings its definition first.
- **Call graph:** callers, callees and the blast radius of a change, for Python, JavaScript,
  TypeScript, Go, Java, Rust, C, C++, C# and PHP. `graph` accepts `Class.method` and lists the
  overrides in subclasses (Python, JavaScript, TypeScript). Large results have a size cap, so
  they fit the MCP result limit of the client.
- **Symbols:** tree-sitter symbols and embeddings for 25+ languages.
- **Local and private:** no API key, no telemetry, no network call of its own.
- **Fresh index:** a file watcher reindexes after each edit. A new session attaches the last
  index by itself.

## Results to quote (2.4.1)

Live benchmark: Claude Code (`claude -p`), Claude Sonnet at medium effort, 6 tasks × 3 runs per
project. Nexus-MCP is compared with the built-in Grep and Read tools. Source: `CHANGELOG.md`,
section 2.4.1.

| Project | Tokens, sum of 18 runs | Cost | Correct answers |
|---|---|---|---|
| Home Assistant (about 26,000 Python files) | 49% fewer | 32% lower | 18 of 18 (Grep 17) |
| Django | 27% fewer | 27% lower | 17 of 18 (Grep 16) |

Release 2.4.0, median tokens per task: 25% fewer than Grep on Vite (TypeScript) and 26% fewer
on webpack (JavaScript), with 6 of 6 correct on both.

Give these limits with the numbers:
- The Grep totals change much from run to run, so the token gaps are less certain than the
  accuracy results.
- On Django, Nexus-MCP used fewer tokens on 2 of the 6 tasks. The win comes from the large
  impact tasks.
- The README results table is older (version 2.1.0). Do not mix its numbers with the numbers
  above.

## Links to give

| Item | Value |
|---|---|
| Repository | `https://github.com/jaggernaut007/Nexus-MCP` |
| Website | `https://jaggernaut007.github.io/nexus-mcp/` |
| Package | `https://pypi.org/project/nexus-mcp-ci/` |
| Benchmarks | `https://jaggernaut007.github.io/nexus-mcp/benchmarks.html` |
| Logo (400x400) | `site/logo-400.png` (also `plugin/assets/logo.png`) |
| Install instructions for an agent | `llms-install.md` |
| License | MIT |
| MCP Registry name | `io.github.jaggernaut007/nexus-mcp` |

## Cline (`cline/mcp-marketplace`, a GitHub issue)

Title: `Add Nexus-MCP (local code search and call graph)`

Body: use the short description above, then: "Repository: <repo URL>. Logo attached (400x400).
`llms-install.md` in the repository tells an agent how to install it. I tested the install with
Cline." Test with Cline first. Cline asks for that test.

## Docker MCP Catalog (`docker/mcp-registry`, a pull request)

See the steps in [DISTRIBUTION.md](DISTRIBUTION.md). Category: `devops` or `developer-tools`
(read the current category list in the registry first). PR title: `Add nexus-mcp`. Use the
image `ghcr.io/jaggernaut007/nexus-mcp:2.4.1`. Put the 5 default tools in `tools.json`
(`index`, `search`, `find_symbol`, `graph`, `map`). Name `NEXUS_EXTRA_TOOLS` as an optional
setting for the other 3.

## Cursor Marketplace (`cursor.com/marketplace/publish`)

Give the repository URL. The repository has `.cursor-plugin/marketplace.json` at the root and
`plugin/.cursor-plugin/plugin.json`. Review is manual and has no set time.

## Anthropic plugin directory (`claude.ai/directory/manage`)

Needs a paid Claude plan. Give the repository URL, the short description, the logo and the
license.

- **Plugin path:** `plugin` (the folder, not the manifest file).
- **Branch or tag:** leave it empty, so the listing follows `main`.
- The listing text comes from `plugin/.claude-plugin/plugin.json` (`description`, `keywords`).

Validation on 2026-11-08 passed with 3 warnings and 2 policy holds:

- **Name hold:** "nexus-mcp" is close to the "Nexus" connector of nexus-fitness. A person
  reviews it. Reviewer note: "Nexus-MCP is a code-search MCP server for developers. It is not
  related to the Nexus fitness connector. The package has been on PyPI as `nexus-mcp-ci` since
  2025 and is in the official MCP Registry as `io.github.jaggernaut007/nexus-mcp`."
- **Verified organizations:** later, the directory will accept local servers only from a
  Verified organization. This is not a refusal today.
- **No lockfile next to the manifest:** the Verified badge needs `pyproject.toml` and `uv.lock`
  in `plugin/`. A lock for `nexus-mcp-ci==2.4.1` on Python 3.12 only is about 205 KB, under the
  256 KiB file limit (all Python versions: 487 KB, over the limit). Add it only together with
  organization verification: it changes the launch command for every plugin user, and Cursor
  shares `plugin/.mcp.json` but does not expand `${CLAUDE_PLUGIN_ROOT}`.
- **Script warning:** it comes from the `uvx nexus-mcp-ci` instructions in the skills and the
  agent. They are instructions, not a script that the plugin runs, so nothing changes.
- **Local (stdio) server:** it runs in Claude Code and Cowork, not on claude.ai.

Submitted on 2026-11-08 from `main` @ `8a4c45a`, with these choices:

- **Warnings:** 7. The 4 more than above are the listing fields (`documentationUrl`,
  `supportUrl`, `privacyPolicyUrl`, `termsOfServiceUrl`). Only the directory reads them, so they
  stay.
- **Listed on:** Claude Code and Cowork. Not the Claude apps: they do not run local MCP servers
  or agents, so only the skills would install.
- **Data handling:** personal data "No"; data to other services "Yes — listed in README"
  (`plugin/README.md`: the opt-in agent tier and share steps); retention "Not retained";
  under 18 "No".
- **Reviewer note:** the name note above, and: "The evaluate skill and the nexus-evaluator agent
  run the same package's CLI (`uvx nexus-mcp-ci eval`) through Bash, only when the user asks.
  They run no other code."
- **New versions:** auto-publish on, GitHub push webhook on. Follow the release order in
  [RELEASING.md](RELEASING.md), so the plugin pin never reaches `main` before PyPI has it.

Result on 2026-11-08: the security scan passed, but the policy check refused the version. The
directory accepts MCP servers that run on the user's computer only from a Verified
organization. It asks to remove every local MCP server, or to use a server at a public URL.
Nexus-MCP must run locally to read the user's code, so a public URL server cannot replace it.
Do not remove the server from `plugin/`: the Claude Code marketplace
(`/plugin marketplace add jaggernaut007/Nexus-MCP`) installs the same folder. Resubmit after the
organization is verified.

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
