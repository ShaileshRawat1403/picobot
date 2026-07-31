# Pico Mission Harness PRD

## Product decision

Pico sessions become a useful personal-agent harness only when a person can
see the intended outcome, the current state of the work, the next checkpoint,
and why the work stopped. A **mission** is that durable coordination record.
It links a user-owned session, artifacts, tool activity, and reviewed memory
without creating a hidden planner or granting new authority.

## Goal

Make one owner able to start, resume, block, complete, or cancel repeatable
personal work locally, while preserving Pico's existing capability profiles
and explicit approval boundaries.

## Core model

```text
Mission
  owner + session link + outcome
  ├── current step / blocker
  ├── checkpoints (note, decision, evidence, handoff)
  ├── session conversation
  ├── session-owned artifacts
  └── Operations activity / approved actions
```

A mission is not an autonomous agent run. It does not infer an action plan,
write memory, execute a tool, or alter a capability profile by itself.

## State machine

```text
draft ──> active ──> completed
  │          │
  │          └──> blocked ──> active
  └────────────────────────> cancelled
active ────────────────────> cancelled
blocked ───────────────────> cancelled
```

`completed` and `cancelled` are terminal. A transition to the current state is
idempotent. Only an explicit owner request changes state. Blocking must carry a
short human-readable reason; unblocking clears it.

## First slice

- Durable local, owner-scoped mission store.
- One mission belongs to one Pico session; a session can have more than one
  historical mission but the workbench presents the current mission first.
- Owner-created title, objective, current step, blocker, and checkpoints.
- Web workbench Missions view: create, choose, update status/step, add a
  checkpoint, and see linked artifact/activity counts.
- Mission status is visible in a dedicated workbench view, but is not injected
  as untrusted instructions into the model prompt in this slice.
- Mission lifecycle changes create a minimal local activity record in the
  mission store; they do not expose hidden reasoning or tool payloads.

## Explicit non-goals

- Auto-planning, background retries, cron, delegation, or autonomous loops.
- Automatic memory writes or skill creation.
- New browser, calendar, task, notes, email, filesystem, or shell authority.
- Changes to Flowright, Soothsayer, or PaneTera.
- Remote hosting, multi-user tenancy, or syncing.

## Acceptance criteria

1. A mission cannot be read, changed, or checkpointed by another owner.
2. Invalid and terminal state transitions fail closed.
3. Reloading Pico retains missions and checkpoints.
4. The web UI reflects server state and never submits an arbitrary owner or
   session key.
5. Artifacts and tool activity remain their own sources of truth; mission UI
   only links or summarizes them.
6. Existing web, artifact, memory, Operations, and browser-read tests remain
   green.

## Later slices

- A reviewed model-generated plan draft, never auto-applied.
- Explicit task/connector integrations through existing profile and approval
  policy.
- Mission resume brief assembled from checked, user-visible sources.
- Outcome review that can propose a reusable skill or memory candidate.
