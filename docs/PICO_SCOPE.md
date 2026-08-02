# Pico Scope Contract

Status: agreed planning checkpoint  
Date: 2026-08-02

## Product boundary

Pico is a local personal thinking and workbench agent that helps its owner
reason, create durable work products, and execute only through explicitly
chosen, bounded integrations.

Pico should have broad agent mechanics without reproducing every provider,
messaging channel, or business vertical. Breadth belongs in the core
capability model; integrations remain small, opt-in adapters.

The product is optimized for personal-work usefulness, inspectability, and
maintenance cost—not catalogue size.

## Scope rules

1. **Mechanics before adapters.** Memory, context, skills, missions, tasks,
   evidence, approvals, artifacts, scheduling, and run visibility are part of
   Pico's core. A new integration is not a substitute for a missing mechanic.
2. **One capability, one narrow adapter.** An adapter should expose the
   smallest read path that proves a real workflow. Writes require a proposal,
   explicit approval, and an audit record.
3. **Opt-in means opt-in.** An installed module, provider package, or skill is
   not active merely because its code exists. The active profile and readiness
   state must make it visible and callable.
4. **No catalogue surface.** Pico should not present dozens of providers or
   channels as a product feature. Deferred adapters may remain in the source
   tree temporarily, but they are not part of the supported default experience.
5. **Every adapter has a cost.** New integrations need an owner workflow,
   authentication path, readiness check, bounded failure behavior, fixtures or
   mocks, owner-isolation tests, and a clear removal/defer decision.
6. **Learning stays owner-controlled.** Feedback may produce a reviewable
   memory or skill candidate with provenance. It must not silently alter
   memory, skills, permissions, provider policy, or the tool surface.
7. **Performance is a feature.** The default session should load a small tool
   surface, avoid probing unused adapters, and keep context and startup work
   bounded.

## Core capability envelope

These are Pico mechanics, independent of any particular external platform:

- durable sessions, runs, tasks, and missions;
- token-bounded context with compaction evidence;
- explicit personal memory, usage evidence, and reviewable learning;
- progressive-disclosure skills with reviewable installation;
- capability profiles, readiness, least authority, approvals, and audit trails;
- bounded delegation and cancellation;
- durable artifacts, revisions, verification, source run/mission provenance,
  format validation, and local search;
- local schedules and bounded heartbeat checks;
- one local web workbench and a direct CLI;
- evidence-first workspace and browser operations.

The core must remain useful with no external integration enabled beyond the
selected model provider.

## Integration envelope

### First-class baseline

These are the current personal-work surfaces Pico is allowed to optimize:

- local workspace files, directories, diagnostics, Git, and bounded changes;
- one explicitly shared browser tab through the Pico Browser Bridge;
- GitHub read/review workflows;
- read-only calendar workflows;
- the local web workbench and CLI as the primary interfaces;
- the provider families selected by the owner in the provider decision below.

### Optional, one at a time

These may be added only when a weekly personal workflow proves the need:

- one messaging channel;
- email read/draft workflows;
- Google Drive/Docs read workflows;
- one additional focused data or task system.

Each optional adapter starts read-first. A write path is a separate slice.

### Deferred from the default product

The following are deliberately not Pico's default scope:

- broad messaging matrices and channel parity;
- provider gateways and regional/provider variants without a demonstrated
  owner workflow;
- voice, wake words, mobile nodes, desktop control, and media generation;
- CRM, commerce, marketing, lead capture, and generic business verticals;
- remote terminals, worker fleets, and marketplace/catalogue behavior;
- subscription-cookie reuse or private provider endpoint emulation.

Existing deferred modules are not automatically deleted in this checkpoint.
They should be quarantined from the default registry and removed only after a
separate compatibility review confirms that no supported workflow depends on
them.

## Provider decision

Pico's official provider surface is intentionally small and matches the
owner's real working setup:

### API connections

1. **OpenAI API** — primary hosted API.
2. **Gemini API** — secondary hosted API.
3. **Anthropic API** — supported hosted API; no Anthropic subscription OAuth.
4. **One local/OpenAI-compatible endpoint** — Ollama is the first-class local
   preset; a single custom OpenAI-compatible endpoint may be used instead.

The local slot is one active endpoint, not a catalogue of local runtimes.
The connection lifecycle and readiness contract are documented in
[`PICO_PROVIDER_CONNECTIONS.md`](PICO_PROVIDER_CONNECTIONS.md).

### Subscription connections

1. **OpenAI Codex subscription** — ChatGPT/Codex access through the official CLI.
2. **Gemini CLI subscription** — the owner's supported Gemini subscription connection through the official CLI.

Subscription connections are distinct from API keys. Pico delegates sign-in,
credential storage, refresh, and disconnect to the provider-owned CLI and
observes only redacted readiness metadata. Pico must never capture browser
cookies, reuse private web endpoints, or present a subscription as a general
API credential. Anthropic subscription OAuth is outside Pico's supported
boundary.

Fallback routing is a runtime mechanic, not a reason to advertise every
provider. An unselected provider must not be probed or shown as ready by
default. The existing registry entries outside this set are compatibility
inventory until they are quarantined or removed in a separate review.

## Adapter admission checklist

An adapter enters supported scope only when all answers are yes:

- Is there a real personal workflow that will be used repeatedly?
- Is the narrowest useful read path clear?
- Is the owner authentication and disconnect path explicit?
- Are writes separately approval-gated?
- Can results be bounded, audited, and linked to a run or artifact?
- Are owner isolation, timeout, setup failure, and forbidden-use tests present?
- Can the adapter be disabled without changing Pico core behavior?
- Does its maintenance cost justify its personal value?

If the answer is no, the adapter stays optional, experimental, or deferred.

## Code organization boundary

Core code owns durable state, context, memory, learning, tasks, missions,
profiles, approvals, evidence, and execution policy. Adapter code owns
provider/channel-specific authentication, transport, normalization, and
readiness checks.

An adapter must not add provider-specific branches throughout the agent loop,
memory model, task lifecycle, or approval state machine. New integrations
should implement a narrow contract behind the existing capability registry.

## Next planning decisions

Before more feature coding:

1. Build the API connection slice for OpenAI, Gemini, Anthropic, and the one
   local/OpenAI-compatible endpoint.
2. Build the subscription connection slice for OpenAI Codex OAuth and Gemini
   OAuth, including secure lifecycle and readiness behavior.
3. Mark the current channel/provider inventory as supported, optional, or
   deferred in the runtime registry.
4. Select whether one messaging channel is needed in addition to Web/CLI.
5. Learning proof workflow is implemented: response review → correction →
   candidate → explicit approval, with run provenance and memory-use evidence.
   Keep skill-use effectiveness evidence as a later, evidence-backed extension
   rather than inferring it from proposal state.
6. Perform a maintainability pass without changing the product boundary.

This contract is the gate for future Pico slices. A feature can be broad in
mechanics while remaining narrow in integrations.
