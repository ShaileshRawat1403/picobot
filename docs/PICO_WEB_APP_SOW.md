# Pico Personal Workbench — Statement of Work

## Objective

Deliver W1 from the Pico Web App PRD as a standalone Picobot enhancement. It
must make local personal use genuinely productive while retaining boundaries
needed for a later enterprise deployment.

## In scope

- Picobot web channel, local HTTP API, session persistence, personal-memory
  views/actions, skill discovery, browser UI, tests, and documentation.
- Localhost-only development and real user testing against Pico's configured
  provider.

## Out of scope

- `active/flowright`, Soothsayer API/web changes, Telegram changes, deployment,
  SSO, multi-tenant enforcement, public exposure, and automatic skill writing.

## Work packages

| Package | Deliverable | Evidence |
| --- | --- | --- |
| W1.1 | Browser identity and durable web-session protocol | Unit tests for identity/routing and session isolation |
| W1.2 | Local read APIs for owned sessions, transcript, memory, skills, health | API-level tests; no credential fields returned |
| W1.3 | Local artifact store with revision files and owner/session provenance | Store tests for isolation and revision history |
| W1.4 | Web Workbench UI: Sessions, Chat, Artifacts, Memory, Skills | Manual localhost walkthrough |
| W1.5 | Explicit memory create/forget actions and provenance display | Store-level and channel/API tests |
| W1.6 | Quality gate and handoff | Full tests, lint, diff check, documented run command |
| W2.1 | Reviewable skill learning from an owned completed session | Store tests for ownership, editing, rejection, explicit approval, and no overwrite |

## Guardrails

- Browser identity is a local development convenience, not enterprise auth.
- HTTP APIs must return only data scoped to that identity and must not return
  provider keys, raw environment data, or hidden model reasoning.
- Irreversible work remains outside Pico W1.
- Memory deletion is lifecycle-based (`forgotten`), preserving provenance.
- UI controls must have a backend effect; no decorative capability switches.
- A learning proposal may create only a draft. It becomes a workspace skill
  only after its owner explicitly approves it; an existing skill name is
  refused rather than overwritten.

## Completion definition

W1 is complete once all PRD acceptance criteria pass, the local web app is
usable end-to-end by the owner, and the work is ready for review as a single
Picobot branch/PR. A separate user decision is required before any Flowright or
Soothsayer integration work begins.
