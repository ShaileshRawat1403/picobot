# Pico Core Harness — Statement of Work

## Product decision

Pico is a local-first personal productivity agent. It should be able to take
real work from intake to a useful, reviewable result, while making the
effective model, context, tools, actions, evidence, and approvals visible.

The target is **not** a smaller clone of Hermes, OpenClaw, or a desktop agent
platform. Pico adopts the durable mechanisms that make an agent dependable and
keeps integrations modular. A new tool is added only when a real personal
workflow proves the need for it.

This SOW is the feature-level source of truth. It complements:

- `PICO_WEB_APP_PRD.md` — web-first workbench;
- `PICO_OPERATING_LAYER_SOW.md` — capability profiles, approvals, and the
  single-tab browser bridge;
- `PICO_MISSION_HARNESS_SOW.md` — owner-directed mission state; and
- `PICO_RUNTIME_POLICY_PRD.md` — provider, model, context, and MCP policy.

## Current baseline

Pico already has a strong local base: owner-scoped memory, artifacts,
missions, reviewable skill proposals, capability profiles, approval records,
tool-activity records, local cron, a one-tab browser bridge, and provider
setup. These are useful components, but they do not yet form one inspectable
agent runtime.

| Capability | Current state | Gap to close |
| --- | --- | --- |
| Model/provider setup | Implemented boundary | API providers are limited to OpenAI, Gemini, Anthropic, Ollama, and one custom OpenAI-compatible endpoint; subscriptions use official provider-owned CLIs with redacted status. |
| Chat/tool execution | Implemented foundation | Durable runs, safe receipts, run detail evidence, tool activity, approvals, and artifact/task/mission linkage are inspectable without private payloads. |
| Memory | Implemented foundation | Explicit recall, confirmation, forgetting, usage evidence, and reviewable learning candidates are live; external memory adapters remain intentionally deferred. |
| Context | Implemented foundation | Token-bounded planning, durable compaction handoffs, protected tails, failure evidence, and a Context view are live. |
| Skills and MCP | Implemented foundation | Governed inventory, readiness, profile filtering, safe diagnostics, and reviewable skill proposals are live; a first real MCP server remains optional. |
| Missions/delegation | Implemented bounded | Missions, depth-zero tasks, cancellation, retry, approvals, run linkage, and evidence are live; remote workers remain excluded. |
| Artifacts/schedules | Implemented foundation | Versioned artifacts, structured formats, links, HTML/bundle exports, provenance, and reversible local schedules are live. |

## What Pico adopts

The following are implementation patterns worth borrowing from mature agent
products and Hermes's public implementation. They are product mechanics, not
an instruction to copy its UI or its wide integration catalogue.

| Mechanism | Pico adaptation |
| --- | --- |
| Primary model plus a few named auxiliary roles | Versioned runtime policy; only add compression first, then a title role if it proves useful. |
| Bounded context compression | Protected recent turns, source references, a durable compaction record, and a fail-closed replacement path. |
| Memory lifecycle with background prefetch | Preserve explicit owner confirmation. Allow at most one optional external memory adapter and never silently promote model output to personal memory. |
| Subagent lifecycle | A small local task-run record with budgets, state, cancellation, and result evidence—not a remote worker fleet. |
| Verification evidence | Mark a claimed result as verified, stale, or unverified based on the actual tool/result/artifact evidence. |
| Turn summary | A concise receipt after a tool-using turn: outcome, tools used, artifacts changed, approval state, duration/usage when measured. No hidden reasoning. |
| MCP inventory and tool filtering | A server registry with probe status, discovered tool list, capability-profile allow-list, and safe diagnostic text. |
| Searchable sessions and artifacts | Owner-scoped local full-text search with source links before any semantic-memory graph. |

## Explicit exclusions

Pico does not add these merely because Hermes or another agent product offers
them:

- desktop shell, cloud gateway, SSH, remote-browser control, or a worker fleet;
- billing, subscriptions, account-marketplace UI, or a large provider matrix;
- dozens of messenger integrations, generic social integrations, or a tool
  marketplace;
- automatic skill installation, automatic memory promotion, or unrestricted
  shell/browser authority;
- visible chain-of-thought, private tool arguments, credentials, cookies,
  browser history, passwords, or raw internal logs;
- multi-agent graphs, training trajectories, or “self-improvement” claims
  before Pico can measure a repeatable improvement on a real task.

## Provider accounts and OAuth

Pico needs a small, honest account-connection framework in addition to
write-only API-key setup. OAuth proves that a provider authorised Pico for a
specific scope; it does **not** mean that a consumer chat subscription can be
used as a general model API.

Every future Pico-managed account connection must have a provider-owned
authorisation flow, explicit scopes, OS-keychain-only refresh-token storage, a
disconnect/revoke operation, bounded refresh, and a redacted lifecycle status.
There is no plaintext token-file fallback. Browser code never receives a
refresh token. A connection is usable only after Pico validates that the
provider actually permits the selected capability.

### Initial provider policy

| Provider route | Pico decision |
| --- | --- |
| OpenAI API | Keep the existing write-only API-key route. It is the supported route for model inference from Pico. |
| ChatGPT Plus/Pro/Business subscription | Do not treat this as a Pico model-provider credential. ChatGPT subscription and API billing are separate. Pico must not capture browser session cookies, reuse a Codex/ChatGPT login token, or emulate private web endpoints. |
| OpenAI account OAuth | Add only if OpenAI publishes a supported OAuth/account-authorisation flow that grants Pico the exact inference capability it needs. Until then the UI states the API-key route plainly rather than displaying a misleading “Connect ChatGPT” button. |
| Gemini API | Add a Google OAuth connection only for the official Gemini API OAuth path, with an owner-selected Cloud project and minimal scopes. It is an API-authorisation route; it does not claim to consume a Gemini consumer subscription. |
| Gemini consumer subscription / CLI entitlement | Use the official Gemini CLI transport only; do not extract or reuse CLI credentials. A Pico-owned adapter remains deferred. |
| Custom OpenAI-compatible endpoint | Continue to support one named endpoint with API key or no key, never a generic OAuth proxy. |

This preserves a future extension point without encoding any provider-specific
workaround. If a provider authorises a supported local account flow later, it
becomes an adapter behind the same connection contract rather than a new Pico
platform feature.

## Delivery sequence

Each package is a vertical slice: storage and API contract, agent integration,
tests, then the smallest truthful web visibility. No settings-only mockups.

### CH0 — Provider-account connection framework

**Outcome:** Pico can connect a provider account where the provider grants a
real, supported authorisation flow—without retaining browser sessions or
misrepresenting subscription entitlement.

Create a provider-account registry and encrypted local credential store
separate from API keys. Start with the official Gemini API OAuth flow only
after its local redirect, token storage, disconnect, refresh, and negative
tests are designed. Retain API-key setup for OpenAI. No ChatGPT-subscription
adapter ships in this package.

**Acceptance gate:** state/PKCE and redirect validation, encrypted token
storage, disconnect, expired-token handling, scope denial, owner isolation,
and no-token-readback tests pass. The UI never calls a subscription usable
until a provider capability probe confirms it.

### CH1 — Turn and run ledger

**Outcome:** every submitted message has one durable execution record, so
Pico never appears silently stuck.

Add an owner/session-scoped `RunRecord` with:

- immutable run ID, session and optional mission link;
- effective provider, model, capability profile, and policy revision;
- `queued`, `running`, `waiting_for_approval`, `completed`, `failed`, or
  `cancelled` state;
- start/end timestamps, elapsed time, trusted usage where the provider returns
  it, safe error summary, and final-result reference;
- linked tool activity, approvals, and artifact references; and
- a user-facing turn receipt that reports observable work only.

The web chat must show live run state and a reliable Stop control. Stopping a
run cancels the active task safely; it never kills unrelated sessions.

**Acceptance gate:** tests prove owner/session isolation, normal completion,
tool failure, cancellation, approval waiting, and that a receipt contains no
hidden reasoning, private tool arguments, or secret material.

### CH2 — Durable runtime and session policy

**Outcome:** Pico can explain and reproduce which model served a turn.

Complete the runtime-policy work with a non-secret global default and an
explicit per-session override. Validate provider/model availability on the
server, apply changes only to later turns, and attach the effective policy to
each `RunRecord`.

Keep provider setup deliberately short: current provider support plus one
named OpenAI-compatible endpoint. A provider is only shown as ready after a
bounded probe. Finish the remaining RP0 gaps—key removal, safe retained probe
status, and model discovery where supported—before adding more providers.

**Acceptance gate:** an invalid/unready provider-model pair cannot be selected;
the browser cannot retrieve a credential; a resumed session retains its policy;
and the run ledger identifies the actual serving model when trusted metadata
exists.

### CH3 — Context budget and compaction evidence

**Outcome:** long-running work remains coherent without silently deleting the
source conversation.

Replace fixed message-count trimming with a conservative token budget. Preserve
the active request and a protected recent tail. Compact only eligible older
history into a durable `CompactionRecord` containing source IDs, token estimate,
model/role, timestamp, handoff summary, and outcome.

Original messages and artifacts remain unchanged. If compression fails, Pico
continues with the original bounded context or stops with a clear error; it
never substitutes a partial summary. Compression does not create personal
memory.

**Acceptance gate:** threshold, failure, retry/cooldown, protected-tail,
source-preservation, and no-auto-memory-write tests pass. Context shows the
latest handoff and its sources.

### CH4 — Governed skills and MCP

**Outcome:** tools are modular but cannot quietly broaden Pico's authority.

Make installed skills and configured MCP servers visible as a registry:

- enabled/disabled state, safe readiness/probe result, and last checked time;
- discovered tool names and profile-scoped allow-lists;
- explicit distinction between read and mutating tools;
- activity linked to a run; and
- a bounded, owner-approved first real MCP server—not a marketplace.

The agent receives only tools allowed by both the active profile and registry.
Mutating tools still use the existing proposed-action/approval path.

**Acceptance gate:** disabled, failed, untested, or profile-forbidden tools
never enter the callable surface; credentials remain redacted; timeout and
probe failures return safe diagnostics.

### CH5 — Controlled task and delegation lifecycle

**Outcome:** missions can become real, bounded work without pretending that a
free-running agent is reliable.

Link a mission to one or more `RunRecord`s. Add a small local task lifecycle
for direct and delegated work:

```text
draft -> queued -> running -> waiting_for_approval -> completed
                         \-> failed | cancelled
```

Require a title, objective, allowed profile, budget, and parent run. Enforce
small concurrency, depth, iteration, and elapsed-time limits. Provide cancel,
safe retry, checkpoint, and result/evidence references. A task may create a
proposal; it cannot expand its profile or approve its own action.

**Acceptance gate:** lifecycle transition, limit, cancellation, retry, parent
link, and approval-boundary tests pass. The mission page shows the real state,
not an inferred progress percentage.

### CH6 — Personal work retrieval, artifacts, and schedules

**Outcome:** Pico becomes useful across repeated personal workflows without
claiming mysterious self-learning.

Add owner-scoped local full-text search over sessions, saved memory, and
artifacts, returning source links and dates. **Implemented now:** bounded local
lexical search across those three sources with owner-scoped inspectable result
references. Artifacts retain optional source run/mission references and support
versioned notes, briefs, plans, reports, data, code/config text, and validated
HTTP(S) link lists. Structured JSON, YAML, CSV, TSV, and JSONL formats are
validated before storage. Artifacts have an explicit draft/final/archived
lifecycle separate from their compact verification status: `verified`,
`stale`, or `unverified`. The workbench can also produce a safe standalone HTML
share view for a selected artifact revision without exposing internal run or
mission identifiers, or package that revision with its canonical content and
integrity manifest as a portable local bundle.

Expose existing cron jobs as a small personal scheduler: create, pause, run
now, inspect last result, and view delivery target. **Implemented now:** the
local web workbench can create owner/session-scoped schedules, pause/resume, run
now, inspect status, and delete them. One-shot jobs are removed after
execution. Do not add a new messaging matrix; use the web workbench first and
add a single real delivery channel only after its workflow is proven.

“Learning” stays honest: Pico may record an owner-scoped useful/not-useful/
correction signal against a completed run, then turn a correction into a
reviewable memory or skill candidate with provenance. It does not apply that
candidate automatically or claim autonomous self-improvement.

**Acceptance gate:** search respects owner scope; sources remain inspectable;
verification becomes stale after relevant changed inputs; paused jobs do not
run; and a failed job leaves a safe result record.

## Personal-agent proof bundle

After CH1–CH4, prove Pico through a small set of real workflows rather than
adding connectors:

1. Research a user-shared browser tab, save a concise source-linked artifact,
   and remember only an explicitly approved preference.
2. Turn a personal objective into a bounded mission, complete one read-only
   task, and inspect the run receipt and evidence.
3. Use one governed external tool/MCP server in a read path, then exercise one
   proposed action and explicit approval.
4. Run a scheduled personal review or reminder, inspect the result, and pause
   it safely.

Only after these pass should Pico gain a new connector such as calendar or
email. Each connector gets its own short SOW: read path, readiness check,
minimal scopes, proposal/approval for writes, evidence, negative tests, and a
manual owner test.

## Implementation order

1. **CH0 design** defines the provider-account contract, but **CH1** is the
   first runtime implementation. It makes every later capability legible and resolves the
   current “working/stuck” ambiguity.
2. Implement **CH0** when Gemini API OAuth is the actual next provider route;
   it does not block the run ledger or OpenAI API-key use.
3. **CH2** and **CH3** make model selection and context repeatable.
4. **CH4** modularises future tools without platform sprawl.
5. **CH5** turns missions and subagents into governed work.
6. **CH6** makes repeated personal use practical.

UI redesign follows these mechanics. Each slice may add a small honest control
or receipt, but Pico should not imitate Hermes's desktop settings surface or
status bar until the corresponding capability exists.
