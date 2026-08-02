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
3. Improve thinking-partner response quality and context explanations without
   expanding the integration boundary.

The learning proof workflow is now a first-class surface. The remaining
learning work is measurement of approved skill use; Pico must add that only
when it can record truthful skill-load evidence, not by guessing from chat.

The next implementation slice should improve those surfaces without adding a
new integration.
