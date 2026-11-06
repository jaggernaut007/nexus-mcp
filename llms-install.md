# Install Nexus-MCP (instructions for an AI agent)

Nexus-MCP is a local code-intelligence MCP server. It gives you search, symbol lookup, a
call graph and project memory. Install it for the client that you run in. Follow these steps.

1. Check that `uv` exists. Run `uv --version`.
   - If it is missing, tell the user to install it: https://docs.astral.sh/uv/getting-started/installation/
   - Do not run a remote install script without the user's yes.
2. Run this command in the project folder. Replace `<client>` with your own client:
   `claude-code`, `claude-desktop`, `cursor`, `vscode`, `windsurf`, `gemini`, `codex` or `cline`.
   ```
   uvx nexus-mcp-ci setup --client <client> --yes
   ```
   The command adds the server entry and the skill text to that client. It starts an index of
   the folder in the background. To see the changes first, add `--dry-run`.
3. Tell the user to reload the client, or to start a new session. New tools appear only after that.
4. In the new session, the tools are `index`, `status`, `search`, `find_symbol`, `graph`,
   `analyze`, `map` and `memory`. If a tool answers "No codebase indexed", call `index` once
   with the absolute project path.

To remove it: `uvx nexus-mcp-ci setup --client <client> --uninstall --yes`.

Licence: MIT.
