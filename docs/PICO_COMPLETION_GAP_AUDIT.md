# Pico completion gap audit

This checkpoint separates missing mechanics from intentional product boundaries.
It is the input to the next UI/UX slice; it is not a request to grow Pico into
an integration catalogue.

## Complete in the current local-first phase

- Durable runs, tasks, missions, approvals, tool activity, artifacts, and safe
  evidence linkage.
- Bounded provider inventory and official CLI subscription boundary.
- Server-owned capability profiles, including the separate `browser-action`
  profile.
- Owner/session/tab-bound browser commands with approval, fingerprint, expiry,
  sensitive-path, and extension receiver checks.
- Token-bounded context planning and durable compaction evidence.
- Reviewable memory and skill learning, local search, schedules, and artifact
  revision/export workflows.
- Owner-visible learning proof: response reviews, correction candidates,
  explicit memory/skill approval, run evidence links, and memory-use counts.
- Session cockpit and evidence spine: latest run state, approval state, mission
  and task state, and source-run links from artifacts are visible from the
  daily workbench.
- Resume-oriented session navigation: the session rail now shows a safe latest
  run receipt, including active, waiting, failed, and provider state, without
  exposing private run payloads.
- Governed Operations clarity: the workbench makes the Propose → Inspect →
  Approve → Execute → Audit flow explicit and summarizes proposed versus
  approved actions without changing the underlying authorization checks.
- Provider boundary clarity: setup cards distinguish API connections, local
  endpoints, and provider-owned subscriptions while showing redacted lifecycle
  states and setup guidance.
- Run-linked context evidence: each completed turn records bounded history,
  recalled memory references, actually supplied skill names, context planning
  outcome, token estimates, and compaction references without storing prompt
  text or hidden reasoning.
- Memory provenance inspector: the owner can review a memory's source,
  confidence, usage signal, and lifecycle history from the daily workbench.
- Governed thinking stances: each session can explicitly choose Explore,
  Decide, Make, or Review; the selected intent shapes the prompt and is linked
  to context evidence without changing capability or approval boundaries.

## Missing but intentionally deferred

- Pico-managed OAuth adapters. Official Codex and Gemini CLIs remain the
  supported subscription transport. A future adapter requires public provider
  endpoints, PKCE/state/loopback checks, OS-keychain-only storage, refresh,
  disconnect, and negative tests before it enters the product surface.
- Messaging, email, Drive, Notion, Slack, and other connector adapters. Add one
  only after a real weekly personal workflow justifies its read path, readiness
  check, approval path, and evidence contract.
- PDF, DOCX, XLSX, and ICS derived exports.
- Remote browser control, desktop automation, cloud workers, SSO, and
  multi-user enterprise deployment. These require a separate security and
  tenancy project.

## Remaining product-quality work

1. Make provider setup honest and low-friction: supported API providers first,
   subscription CLI setup clearly separated, and disconnect/status actions
   visible without exposing credentials.
2. Reduce Operations density by separating profile selection, browser sharing,
   approvals, and recent activity into clear sections with state-specific copy.
3. Continue improving thinking-partner response quality and context
   explanations without expanding the integration boundary; the first governed
   stance slice is now complete.

The learning proof workflow is now a first-class surface. Approved skill use
is measured only when skill instructions were actually supplied to a model
turn; mere installation or mention does not count as use.

The next implementation slice should improve those surfaces without adding a
new integration.
