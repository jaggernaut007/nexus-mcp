# ADR-024: MIT Licence, and the Commercial Licence Key Removed

## Status: Accepted
## Date: 2026-11-06
## Supersedes: ADR-023

## Context

Versions 2.0.1 to 2.2.3 used PolyForm Noncommercial 1.0.0. ADR-023 added a paid commercial
tier ($5 per developer per month, sold through Polar, with a licence key check).

The owner wants more adoption first. PolyForm is not an OSI open-source licence. Some
companies ban such licences, and some directories list only OSI licences. The owner will
decide on a commercial model when the project has enough users.

## Decision

- **Licence: MIT**, for every new release. Copyright Shreyas Jagannath.
- **Old versions keep their terms.** Anyone who got 2.0.1 to 2.2.3 keeps the PolyForm terms
  they received. Versions 0.1.0, 0.1.1 and 2.0.0 were already MIT.
- **The licence key feature is removed**: `licensing.py`, the `license` CLI command, the
  `license` word in `status()`, the `NEXUS_LICENSE_*` variables, the optional key fields in
  the MCPB, MCP Registry and Glama files, and the `license-key` input of the Action.
  With no key feature, the server again makes no network call of its own (see PRIVACY.md).
- **`COMMERCIAL-LICENSE.md` is removed.** It was only a draft.

## Why the code is removed and not kept dormant

- Dead code adds tests, docs and a privacy surface for nobody.
- Git keeps it. The last commit that has the feature is `22f9d5b` on the branch
  `worktree-commercial-licence-and-channels`. To restore it, check out the files from that
  commit: `src/nexus_mcp/licensing.py`, `tests/test_licensing.py`, `COMMERCIAL-LICENSE.md`.

## What the sandbox test taught us (keep this when the feature comes back)

- Polar answers **404** on `validate` for a key that was revoked or whose subscription ended.
  It does not return a 200 with a "revoked" status.
- `license status` must force a server check. A cached answer hid the revocation.
- The Polar organization and the product were made and tested in the sandbox. The live
  organization slug is `shreyas-jagannath`.

## Why this is safe to reverse later

A later move from MIT to a stricter licence applies only to new releases. Versions that
people already have stay MIT for them. All commits are by the owner, so the owner can
relicense. Add a contributor licence agreement before the first outside contribution, so that
a later change stays possible.

## Consequences

- No revenue from licence keys. The owner may add GitHub Sponsors or Polar donations.
- A company can use, fork and sell the code. The commercial path is now support, a custom
  enterprise version or a hosted service, and not the licence.
