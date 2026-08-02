# Pico personal workflow proof

This is a practical release check for Pico as a personal thinking/workbench
agent. It favors real weekly work over synthetic feature demos and keeps the
integration boundary narrow.

## Before a run

- Use one supported API, local endpoint, or provider-owned subscription CLI.
- Start a fresh session for each workflow and choose the matching stance:
  Explore, Decide, Make, or Review.
- Select the smallest capability profile that can complete the work.
- Share only one public browser tab when research or a browser action is
  genuinely needed.
- Never paste API keys, OAuth tokens, passwords, recovery codes, payment data,
  or other secrets into a prompt or artifact.
- Record the run ID and inspect the evidence after the work, not just the final
  answer.

## Workflow 1: research to a durable artifact

1. Ask Pico to research a real decision or topic using a public source.
2. Have it compare the evidence and produce a Markdown artifact with source
   links; optionally request a JSON companion for structured facts.
3. Revise the artifact once, then inspect its source-run link and revision
   history.
4. Review context evidence: supplied skills, recalled memory references,
   compaction outcome, and token estimates.
5. Approve a memory only if it is genuinely useful beyond this one task.

Pass when the artifact is understandable without the chat, sources are linked,
the run is resumable, and no private payload or hidden reasoning is exposed.

## Workflow 2: objective to bounded mission/task

1. Create a small personal objective with one or two explicit steps.
2. Start a depth-zero task and let it produce a proposed action or work product.
3. Inspect the task, linked run, mission state, and any artifact before acting.
4. Cancel, retry, or resume once so the lifecycle is exercised deliberately.

Pass when there is one clear owner/session boundary, state transitions are
explainable, and the next step is obvious after reopening the session.

## Workflow 3: governed browser action

1. Begin with a read-only browser review of the shared tab.
2. Stage one bounded write: navigate to a user-visible URL, click a bounded
   target, or type non-sensitive text into a bounded target.
3. Inspect the target summary and payload fingerprint, then explicitly approve.
4. Dispatch and inspect the safe result summary and audit entry.
5. If practical, verify that a stale tab, changed payload, expired approval, or
   sensitive control is refused.

Pass when no write occurs before approval and every refusal is understandable.
Do not use login, authentication, password, payment, recovery, billing, or
secret-like inputs for this test.

## Workflow 4: a small recurring review

1. Schedule one local review, such as a weekly planning prompt or artifact
   check.
2. Run it once, inspect the resulting run and artifact, then pause or delete
   the schedule when finished.

Pass when the schedule is owner-scoped, its next action is visible, and the
  resulting work can be found from the session evidence.

## Workflow 5: thinking partner and learning proof

1. Bring a real question to the stance that fits it; do not ask for a generic
   FAQ answer.
2. Mark the response Useful, Not useful, or Correction and explain the smallest
   meaningful adjustment.
3. Review any proposed memory or skill change; approve only an explicit,
   reusable improvement.
4. Return to a later session and verify that the approved learning is visible
   as provenance, not as an unexplained change in behavior.

Pass when Pico helps you think more clearly, makes uncertainty visible, and
never silently turns feedback into durable memory.

## Workflow record

Keep one short record per real attempt:

```text
Date:
Workflow:
Intent:
Profile / stance:
Provider / model:
Run ID:
Artifact or mission:
What worked:
Friction or missing evidence:
Smallest next adjustment:
```

Do not add a connector because a test feels interesting. Add one only after a
workflow repeats weekly, the missing read path is concrete, and its approval,
evidence, and failure behavior can be specified before implementation.
