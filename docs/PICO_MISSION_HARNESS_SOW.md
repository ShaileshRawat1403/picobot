# Pico Mission Harness — Statement of Work

## Objective

Deliver the first durable coordination layer for Pico personal work without
adding new execution authority.

## Work packages

| Package | Deliverable | Verification |
| --- | --- | --- |
| MH1 | Owner-scoped local MissionStore and strict state machine | Persistence, isolation, transition, terminal-state tests |
| MH2 | Session linkage and safe mission API | Browser identity is server-derived; invalid input is rejected |
| MH3 | Missions workbench view | Manual local create/update/block/complete walkthrough |
| MH4 | Evidence links | Artifact and tool-activity counts only; no copied private payloads |
| MH5 | Regression and review | Focused tests, full Pico tests, Ruff, diff check |

## Implementation rules

- Use local SQLite under the configured Pico workspace.
- Derive owner and session identity on the server from Pico's browser identity.
- Keep title, objective, steps, blockers, and checkpoint summaries bounded.
- Never let a model change mission state directly in this slice.
- Do not make mission state an instruction source for the agent loop.
- Do not make a mission a substitute for a Flowright governed workflow.

## Completion gate

The slice is complete only after the state-machine, persistence, owner-isolation,
and web API tests pass, the local UI has been manually exercised, and the full
Pico suite remains green.
