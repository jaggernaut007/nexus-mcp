# ADR-023: Commercial Licence Key Check

## Status: Superseded by ADR-024 (2026-11-06). The code was removed.
## Date: 2026-11-05

## Context

Nexus-MCP is source-available under PolyForm Noncommercial 1.0.0. A company that wants to use
it at work could only "contact the author". There was no price and no way to pay. The owner
wants commercial users to pay a small monthly amount. The owner also wants free noncommercial
use to stay free and unchanged.

Constraints from the project: it has no backend server, no telemetry (ADR-021), and no
network call by default. The north star is fewer tokens for the same accuracy, so the check
must not add tokens.

## Decision

- **Provider: Polar.sh.** It is the merchant of record, so it handles tax. It issues licence
  keys and has a public activate, validate and deactivate API. We need no server of our own.
- **Price: $5 per developer per month, or $50 per year.** One key covers one developer on up
  to three machines (the activation limit in the Polar benefit).
- **Soft mode.** The code never blocks a tool. A key can be `none`, `active`, `invalid`,
  `revoked` or `unverified`. That word is the only effect.
- **Opt-in network use.** With no key, the code opens no socket. With a key, it sends only the
  key, the organization id and a random machine label to `api.polar.sh`, at most once a day,
  in a background thread. `NEXUS_OFFLINE=1` blocks it. A test enforces the no-key rule.
- **Grace period.** If the server is not reachable, a key that passed a check in the last seven
  days stays `active`. After that the state is `unverified`. A server error is not a revoke.
- **No extra tokens.** `status()` gets a one-word `license` field only when a key is set. The
  "no key" notice goes to stderr, which is not part of the agent context.
- **No new dependency.** The client uses `urllib.request`, as `eval share` does.
- **The licence is the control, the check is a convenience.** The source is public, so a user
  can remove the check. The terms in COMMERCIAL-LICENSE.md bind the user either way. The check
  helps an honest user to prove that they are licensed and tells them when a key ends.

## Consequences

- A user who sets a key makes one network call a day. `docs/PRIVACY.md` names it.
- The Polar organization id is a public value in the source. Until the owner sets it, the code
  makes no call and a key stays `unverified` with the note "commercial licensing is not open yet".
- COMMERCIAL-LICENSE.md is a draft. A lawyer must review it before the first sale.
- We cannot stop a determined user from using the software commercially without a key. We
  accept this, in the same way that ADR-021 accepts fewer submissions for no telemetry.

## Alternatives considered

- **Lemon Squeezy.** The same fit as Polar. We chose Polar for its focus on open-source and
  source-available projects.
- **Stripe with our own backend.** Lowest fee, but Stripe issues no licence keys. We would
  need to run a server, a webhook and tax handling.
- **Declare-or-key gate, or a hard gate.** A stronger signal, but it breaks every current free
  install and adds a setup step for each free user.
- **Offline signed keys.** No network call, but a key cannot be revoked when a subscription
  ends, and we would need a crypto dependency.
