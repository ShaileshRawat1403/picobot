# Pico Operating Layer — Statement of Work

## Objective

Make Pico safely useful for real personal productivity workflows by creating a
small capability, approval, and browser-operating layer. It must be testable
locally before calendar, email, or wider automation is added.

## In scope

- Picobot capability registry, profile resolver, readiness checks, tool
  activity records, approval ledger, browser bridge, web workbench, tests, and
  documentation.
- Local Chrome extension development and only owner-shared current-tab access.
- Existing Pico web/session/memory/artifact/skill data stores where needed for
  session and owner linkage.

## Out of scope

- Soothsayer or Flowright changes.
- Public hosting, SSO, multi-user authorization, cloud queues, and remote
  browser control.
- Chrome cookies/history/passwords, unrestricted filesystem or shell access,
  payment/auth/CAPTCHA automation, and unattended publishing.

## Work packages

| Package | Deliverable | Evidence |
| --- | --- | --- |
| OL1.1 | Capability registry and stable risk/approval metadata | Unit tests: unknown capability rejected; no credential values in API response |
| OL1.2 | Server-resolved `personal-work` and `research` session profiles | Tests: profile cannot broaden client-side; provider sees only allowed tool definitions |
| OL1.3 | Operations UI and safe readiness endpoint | Browser walkthrough: ready/needs-setup states agree with runtime checks |
| OL1.4 | Tool activity ledger | Tests: owner/session scope, outcome, timestamps, redaction |
| OL2.1 | Proposed-action database and approval state machine | Tests: state transitions, expiry, no execute before exact approval |
| OL2.2 | Pending-approval UI | Browser walkthrough: approve/reject cards update durable action state |
| OL3.1 | Local Chrome extension and explicit shared-tab handshake | Extension test: unshared/stale tab cannot be operated |
| OL3.2 | Browser read/snapshot operations | Owner test against a non-sensitive public page with activity evidence |
| OL3.3 | Staged browser navigate/click/type | Tests and manual test: action remains staged until owner approval |
| OL3.4 | Sensitive-control blocklist and trace redaction | Tests: password/payment/auth patterns block and typed secret-like values redact |
| OL4.x | Individual productivity connectors | One separate SOW/acceptance run per connector |

## Current delivery status

OL1.1–OL3.2 are implemented and verified locally. The registry is intentionally
limited to the existing safe skills and research tools; it does not present
unimplemented browser writes or connector capabilities as ready. The action ledger
stores a bounded human-readable proposal, requires an exact owner/session
decision, expires unused proposals, and exposes pending actions in Operations.
Pico's local Chrome bridge now requires a one-time pairing code and one
explicitly shared active tab before it stores a redacted visible-text snapshot.
It does not yet stage or run browser writes; OL3.3 is the next implementation
boundary.

## Delivery order

Implement and test OL1 fully before any Chrome code. Implement and test OL2
before enabling browser mutation. Ship OL3 read-only operations before staged
browser actions. No connector write path begins until its read path, readiness
check, and approval behavior have been proven.

## Hermes-derived decisions

- Adopt the idea of named toolsets and profile-scoped resolved tool lists.
- Adopt configuration-aware status: enabled and configured are distinct.
- Adopt concise quick-reference information for an available toolset, not a
  long list of hypothetical features.
- Adopt read/mutating classification and runaway-loop guardrails.
- Do not adopt Hermes's broad desktop/host surface or automatic skill writing;
  Pico retains its explicit review-and-approve skill flow.

## Completion definition

OL3 is complete when the owner can begin a browser-review session, explicitly
share one Chrome tab, ask Pico to inspect a public page, see the resulting
activity evidence, stage a safe browser action, and approve or reject it—with
tests proving that Pico cannot access an unshared tab or execute a staged write
without the exact approval.
