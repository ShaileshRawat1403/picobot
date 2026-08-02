# Pico Operating Layer PRD

## Product decision

Pico will become a personal productivity agent by adding a small, visible
operating layer before adding broad autonomy. The layer decides which
capabilities a session may use, whether they are actually ready, and whether a
proposed external action requires owner approval.

This is intentionally inspired by Hermes's useful mechanics—toolsets,
configuration-aware availability, a concise capability catalog, and clear
mutating-tool guardrails—but is designed for Pico's local web workbench.
Pico will not copy Hermes's desktop shell, unrestricted host access, or broad
always-on tool surface.

## Problem

Pico can chat, retain explicit memory, produce artifacts, and install
owner-approved skills. It cannot yet reliably *do* a personal workflow because
the owner cannot see:

- which tools a session may use;
- whether a tool is configured and healthy;
- what an external tool action would change; or
- which actions Pico stopped for approval.

## Principles

1. **Least capability per session.** A session starts with a named profile,
   not every installed tool.
2. **Configured is not available.** A tool is available only when its local
   dependency and required account/credential setup have passed a safe health
   check. No secret values are returned to the browser.
3. **Read is different from change.** Read-only operations can run inside a
   suitable profile. Mutating operations must be staged and approved unless a
   narrowly defined owner rule explicitly permits them.
4. **Browser access is shared, not seized.** Pico works only with a browser
   tab the owner explicitly shares. It never reads browser history, cookies,
   password stores, or unrelated tabs.
5. **Every visible control is real.** A profile, capability state, proposed
   action, approval, or failure is backed by a durable record and tests.
6. **No hidden self-expansion.** Skills and memory remain owner-controlled;
   Pico cannot broaden its own profile or install an integration while working.

## Capability model

Every capability has the following stable fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable internal identifier, e.g. `browser.read_page` |
| `toolset` | Small functional group, e.g. `browser`, `research`, `calendar` |
| `risk` | `read`, `draft`, or `external_write` |
| `configured` | Required local setup exists; secret values are never exposed |
| `available` | The capability can run in the current runtime |
| `approval` | `none`, `per_action`, or a later explicit owner rule |
| `profile` | Profiles in which it is permitted |

Initial profiles:

| Profile | Intended use | Capability boundary |
| --- | --- | --- |
| `personal-work` | Chat, memory, artifacts, approved skills | No external side effect; no mission action tools |
| `research` | Research and synthesis | Search, fetch, browser reading |
| `browser-review` | Owner-supervised browser work | Shared-tab reading plus staged browser actions |
| `mission-work` | Governed mission execution | Active mission, approved blueprint, staged draft actions, explicit approval |
| `github-review` | Read-first pull-request review | GitHub PR overview, checks, and bounded diff through local `gh`; no comments, approvals, merges, or pushes |
| `calendar-read` | Personal schedule review | Read upcoming Google Calendar events through the local token; no event writes |
| `workspace-inspect` | Local workspace inspection | Read files and list directories in the configured workspace; no writes or shell |
| `workspace-run` | Local workspace diagnostics | Bounded read-only diagnostic commands; no writes, network, installs, or shell mutation |
| `workspace-build` | Governed local development | Draft file/command changes for explicit approval; never execute directly from chat |
| `productivity` | Later calendar/task/note work | Connector-specific reads; writes require approval |
| `governed-handoff` | Future Soothsayer handoff | No direct external execution in Pico |

Profiles are server-owned defaults. The browser may request a profile but may
not manufacture a tool or broaden the session's capability set.

An attached mission provides bounded context only. It never changes the
session profile automatically; governed mission action proposals require an
explicit switch to `mission-work`.

## Chrome bridge v1

### Connection model

Pico Browser Bridge is a local Manifest V3 Chrome extension. The owner chooses
one current tab and presses **Share with Pico**. The extension communicates
only with Pico's local service. No remote URL, browser profile scraper,
remote-debug port, or unattended tab discovery is in scope.

### Allowed v1 operations

- Inspect accessible page structure and visible text from the shared tab.
- Read the current URL/title and capture a user-visible snapshot.
- Navigate the shared tab after Pico states the destination.
- Stage click, type, select, and scroll actions as a proposed action.

### Prohibited v1 operations

- Read cookies, saved passwords, browser history, downloads, or another tab.
- Click browser permission prompts, authentication challenges, payment flows,
  account recovery, or CAPTCHA/security controls.
- Store text that resembles a credential in an activity record or UI trace.
- Submit a form, publish, purchase, send a message, or delete data without a
  specific owner approval.

### Approval experience

For an `external_write`, Pico creates a durable action card containing the
shared-tab URL/domain, plain-language proposed effect, tool name, and time.
The owner can approve once, reject, or let it expire. Approval is bound to the
action ID and cannot be reused for an altered target or payload.

## Workbench UX

Add an **Operations** area, kept smaller than a Hermes-style dashboard:

```text
Operations
├── Current profile          profile and concise capability count
├── Ready now                enabled + configured capabilities
├── Needs setup              unavailable capability and safe next step
├── Shared browser tab       explicit tab state or “not shared”
├── Needs your approval      pending staged external actions
└── Recent activity          tool/action outcome; never hidden reasoning
```

The Chat composer shows the active profile as a compact chip. A user can open
Operations for detail; it does not need a constantly visible tool control
panel.

## Scope sequence

### OL1 — Capability registry and session profiles

- Server-owned registry for existing Pico tools.
- `personal-work` and `research` profiles.
- Safe health/readiness checks and Operations view.
- Truthful tool activity record for each tool call.

**Implementation status: complete for the initial local surface.** Pico now
filters provider tool definitions and runtime execution by a server-resolved
session profile. The Operations view shows the current profile, capability
state, and owner/session-scoped tool outcome records. The first two profiles
are deliberately small: approved skills only, then read-only web research.
Calendar, browser, shell, messaging, and connector tools remain outside these
profiles until their dedicated slices and approval rules exist.

The `github-review` profile is the first focused integration slice. It requires
the owner's local authenticated GitHub CLI and exposes only a dedicated
read-only PR tool. Pico reports setup state without exposing `gh` credentials or
authentication output.

### OL2 — Approval and action ledger

- Durable owner/session-scoped proposed-action store.
- State machine: `proposed → approved | rejected | expired → executed | failed`.
- Approval required for each external-write action.
- A testable policy resolver; no browser-originated bypass.

**Implementation status: complete for the local ledger and approval surface.**
Pico now persists owner-and-session-scoped proposals, expires them safely, and
allows exactly one approve or reject decision. Operations shows only the
current session's pending proposals. There is intentionally no browser UI
route to stage arbitrary actions and no external executor yet; OL3 will be the
first consumer of this ledger.

### OL3 — Chrome bridge, read-first plus governed typed actions

- Local extension with explicit active-tab sharing.
- Read/snapshot/current-page operations in `research` and `browser-review`.
- Staged click/type/navigation, durable command ledger, result audit, and approval interlock.
- No general computer use and no automation of sensitive browser controls.

**Implementation status: OL3.1–OL3.4 are complete locally.** Pico now has
a minimal Manifest V3 extension with only `activeTab`, `scripting`, and
session-only storage permissions. An owner creates a five-minute, one-time
session code in Operations, then explicitly shares the current Chrome tab.
Pico persists a bounded, redacted visible-text snapshot for that exact owner
session and can expose it only through the `browser-review` profile. It cannot
discover other tabs, read cookies/history/passwords, or access sensitive
authentication and payment paths. Browser writes are exposed only through the
separate `browser-action` profile and cannot run without the exact owner
approval, active share, tab binding, and payload fingerprint.

### OL4 — Personal productivity connectors

- One integration at a time: task store, calendar, notes, then email.
- Read-only health and read path before any write path.
- Real end-to-end owner test and rollback/review behavior per connector.

## Acceptance criteria for OL1–OL3

- A new session receives a server-resolved profile; its provider tool list is
  a subset of that profile's capabilities.
- Operations shows each available/unavailable capability with truthful state
  and never reveals environment or credential values.
- An activity record identifies profile, session, tool, risk class, outcome,
  and timestamp without exposing hidden reasoning or secrets.
- A shared browser tab is explicit and owner-scoped. An unshared or stale tab
  cannot receive a Pico browser operation.
- A staged browser write cannot execute before a specific approval; rejected or
  expired actions cannot execute afterward.
- Pico does not submit authentication, payment, recovery, billing, or other
  sensitive browser forms in OL3; typed actions are limited to non-sensitive
  text in a bounded selector.
- Unit tests cover policy resolution, owner/session isolation, action state
  transitions, stale-tab rejection, redaction, and no-bypass invariants.

## Explicit non-goals

- Remote or public Pico hosting.
- Headless harvesting of personal browser data.
- A general shell/desktop controller.
- Background autonomy, automatic approval, or automatic connector setup.
- Flowright or Soothsayer implementation changes.
