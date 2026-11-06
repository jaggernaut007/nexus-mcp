# Releasing

## Where the version lives

`scripts/check_docs.py` fails in CI when these disagree with `pyproject.toml`:

- `pyproject.toml` (`version`)
- `CITATION.cff` (`version`, and set `date-released`)
- `server.json` (`version`, and the `version` of each package)
- `plugin/.claude-plugin/plugin.json` (`version`)
- `plugin/.mcp.json`, `gemini-extension.json` and `mcpb/pyproject.toml` (the pinned
  `nexus-mcp-ci==X.Y.Z` launch), `mcpb/manifest.json` and `mcpb/pyproject.toml` (`version`)

`python scripts/bump_version.py X.Y.Z` sets all of these at once, except `date-released`.

Also update by hand:

- the version in `site/index.html` (the `softwareVersion` JSON-LD, and the hero eyebrow and its
  release link);
- the "New in vX.Y.Z" note above "The 8 Tools" in `README.md` (the PyPI page shows this README);
- a new section in `CHANGELOG.md`.

Do not change `version` in `benchmarks/published/results.json` unless the live benchmark ran
again. It names the version that the benchmark measured.

## Steps

1. Make sure CI is green on `main`.
2. Bump the version in the files above. Run `python scripts/check_docs.py`.
3. Move the `Unreleased` entries of `CHANGELOG.md` under the new version and date.
4. If the benchmark ran again, edit `benchmarks/published/results.json`, then run
   `python scripts/publish_results.py`.
5. Merge to `main`.
6. Create a GitHub release with the tag `vX.Y.Z`. `publish.yml` then:
   - builds the wheel and the sdist and publishes them to PyPI (trusted publishing);
   - publishes `server.json` to the official MCP Registry (GitHub OIDC). The registry checks
     that the PyPI README holds `mcp-name: io.github.jaggernaut007/nexus-mcp`;
   - builds `nexus-mcp.mcpb` (the Claude Desktop extension) after PyPI has the version, and
     attaches it to the release;
   - pushes `ghcr.io/jaggernaut007/nexus-mcp:<version>` and `:latest`.
7. `pages.yml` deploys the site from `main`.
8. Check Glama: it rebuilds from the repository. Re-claim the server if the listing is stale.
9. Do the owner steps in [DISTRIBUTION.md](DISTRIBUTION.md): tick "Publish to Marketplace" for
   the Action, and open the Cline issue.

## After the release

- `pip install -U nexus-mcp-ci` in a clean environment, then `nexus-mcp-ci eval --example`.
- Check the listing at `https://registry.modelcontextprotocol.io`.
