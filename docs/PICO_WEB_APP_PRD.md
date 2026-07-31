# Pico Personal Workbench PRD

## Product decision

Picobot is a web-first personal agent. It is not a desktop clone of Hermes and
it is not an unrestricted autonomous shell. The web app is the place where an
owner can start and revisit work, see what Pico remembers, inspect which skills
are available, and explicitly send governed work to Soothsayer later.

Initial deployment is one authenticated local owner on `127.0.0.1`. The data
model and routing must remain suitable for later authenticated, multi-user
deployment; localhost is not treated as an enterprise security boundary.

## Design principles

1. **Conversation is a workspace.** A session has a name, durable transcript,
   status, and its own context. Starting a new task must not erase old work.
2. **Memory is visible and revocable.** Pico never hides durable facts behind a
   vague “learning” claim. The owner can see, add, confirm, reject, or forget
   memory at any time.
3. **Capabilities are earned, not implied.** The UI shows what skills are
   available and why. A catalog does not silently grant a tool permission.
4. **No fake control plane.** Every visible status or action must be backed by
   Pico's actual process, session store, memory store, or capability registry.
5. **Local first; enterprise-shaped.** Browser identity, session ownership,
   audit fields, explicit approvals, and narrow APIs come before SSO,
   multi-tenancy, remote hosting, and queues.

## Reference influence

Hermes provides useful interaction patterns: session navigation, a focused
canvas, skills discoverability, and legible runtime status. Pico adapts those
patterns for the browser and deliberately excludes Hermes's desktop chrome,
full home-directory browser, and large autonomous tool surface.

## Information architecture

```text
Pico Personal Workbench
├── Sessions        named conversations and durable transcripts
├── Chat            current task, response/progress, explicit stop control
├── Memory           personal facts, proposed facts, provenance, revocation
├── Skills           discoverable local capabilities and prerequisites
├── Activity         local runtime events and later governed handoff evidence
└── Settings         model, workspace profile, local connection health
```

## Delivery slices

### W1 — Useful, durable personal workbench

- Persistent browser identity stored only in the local browser profile.
- Create, list, select, and resume Pico web sessions.
- Reload a transcript from Pico's existing session store.
- Create, preview, revise, and download session-owned local artifacts. Artifact
  files are versioned under the Pico workspace with manifest provenance.
- Memory panel backed by `PersonalMemoryStore`: list, search, explicitly add,
  and forget facts; show lifecycle and provenance.
- Skills panel with source, description, availability, and missing
  prerequisites. It is read-only in W1.
- Browser connection/runtime health and clear empty states.

### W2 — Context and work visibility

- Per-turn activity cards for reasoning-independent progress, tool calls, and
  errors without exposing hidden model reasoning.
- Session titles, search, and archive semantics. **Implemented now:** durable,
  owner-scoped session titles; search and archive remain next.
- A compact “context used” panel: confirmed memory references and skills
  loaded for the current task. **Implemented now:** latest-turn bounded
  history count and exactly recalled personal-memory references. Skill-loading
  evidence remains next because Pico must only display it once it can record it
  truthfully.
- Reviewable learning candidates and memory-use evidence. **Implemented now:**
  an owner can propose a learning candidate, confirm or reject it before
  recall, and inspect how often a confirmed memory was actually supplied to a
  model turn. Pico never auto-promotes conversational inferences.
- Reviewable skill learning. **Implemented now:** an owner can create a
  `SKILL.md` proposal from a completed, owned session, edit it, reject it, or
  explicitly approve installation into their workspace. Pico never creates,
  activates, or overwrites a skill automatically; approved skills apply to a
  new session rather than rewriting a live one.
- Explicit tool/capability profiles per session (personal, research, governed
  handoff), never arbitrary browser-side toggles.

### W3 — Governed handoff

- A narrow “send to Soothsayer” task brief with intended outcome, deliverable,
  and constraints.
- Status and review return path. Pico remains the personal front door;
  Soothsayer/Flowright remain the governed execution plane.

### W4 — Enterprise hardening

- Real authentication and browser-to-user identity binding.
- Tenant/workspace scoping, audit events, rate limits, CSRF/origin policy,
  secure remote deployment, and encrypted secret management.
- Durable outbox and integration delivery only when a supported use case needs
  it.

## Explicit non-goals for W1

- Desktop application.
- Remote/public hosting or a claim of multi-user security.
- Full filesystem browser or unrestricted shell controller.
- Silent memory extraction, automatic skill creation, or automatic skill
  overwrite.
- Broad tool toggles copied from Hermes.
- Modifying Flowright or coupling Pico to Soothsayer before W3.

## Acceptance criteria for W1

- Refreshing the page preserves the current browser-owned session and restores
  its transcript.
- A new session has independent context from an older session.
- An artifact has a source session, owner, local revision history, and a safe
  local download path. Another browser identity cannot read or revise it.
- The Memory page shows only the current owner's records; a forget action
  changes lifecycle state rather than destroying history.
- Skills displayed in the UI come from the active Pico workspace and report
  availability truthfully.
- A second browser identity cannot receive another identity's chat response,
  transcript, or memory via the normal local API.
- The complete Pico test suite passes and the browser UI is manually exercised
  at `http://127.0.0.1:18792`.
