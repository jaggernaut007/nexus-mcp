# ADR-021: One Source for Published Results, and Opt-in Sharing

## Status: Accepted
## Date: 2026-11-03

## Context

Benchmark numbers sat in five hand-edited places (README, site, FAQ, llms.txt, docs) and
drifted. Earlier commits fixed stale counts by hand. Adopters who run `eval` have results that
would help other people decide, but the project has no telemetry and must not get one.

## Decision

- `benchmarks/published/results.json` (schema v1) is the only place a published number is
  written by hand.
- Schema v2 (2026-11-05) adds an optional `languages` list: one entry for each language, with
  a `before` run and an optional `after` run. A v1 file still works. `publish_results.py`
  refuses an unknown `schema_version`.
- `scripts/publish_results.py` generates the README glance table, the site results rows, the
  llms.txt section and `site/benchmarks.{html,json,csv}`. Generated blocks sit between
  `<!-- results:start -->` and `<!-- results:end -->`. CI runs it with `--check`.
- `nexus-mcp-ci eval share <id>` builds an allow-listed payload (versions, size band,
  languages, numbers), prints it, and posts a GitHub issue only after a yes. A repo URL is
  included only for a public github.com repo. No data goes in a URL.
- A maintainer label (`eval-accepted`) starts `community-results.yml`. It validates the payload
  with a strict allow-list (ASCII full matches, typed objects, numeric ranges), regenerates the
  pages and opens a PR whose body holds the validated entry. The issue body reaches the script
  only through an environment variable. Actions are pinned to commit SHAs.

## Consequences

- A hand edit to a generated block fails CI.
- Community data is reviewed twice: the label, then the PR.
- Sharing needs a GitHub account. We accept fewer submissions for no telemetry.
- The owner must create the labels and allow Actions to open PRs.
