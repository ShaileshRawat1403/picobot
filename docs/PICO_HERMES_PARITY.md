# Pico Hermes Parity Envelope

Pico uses Hermes as a mechanics reference, not as a reason to copy a broad
integration catalogue. Hermes's useful product surface includes durable memory,
searchable sessions, reviewable skills, scheduled work, delegation, context
compression, tool profiles, MCP, provider routing, browser work, and channel
delivery. Pico implements those mechanics with a local owner boundary and adds
an integration only when the personal workflow has a clear read path, approval
path, and evidence.

## Capability map

| Hermes capability | Pico adaptation | State |
| --- | --- | --- |
| Persistent memory and user model | Owner-scoped confirmed memory, provenance, recall evidence, and reviewable learning proposals | Implemented |
| Response learning signal | Owner-scoped useful/not-useful/correction review linked to a run; corrections can become reviewable memory or skill candidates with provenance | Implemented |
| Cross-session search | Local bounded lexical search over sessions, memory, and artifacts | Implemented |
| Skills and skill improvement | Installed skills plus explicit draft/revise/approve/reject workflow | Implemented |
| Context compression | Token-bounded protected-tail compaction with durable source evidence | Implemented |
| Runtime/provider selection | Server-resolved provider/model policy recorded on every run | Implemented |
| Toolsets and profiles | Server-owned capability profiles and governed registry | Implemented |
| Scheduled automations | Local schedules with owner-scoped web delivery, pause/resume, run-now, and delete | Implemented |
| Heartbeat-style background checks | Local heartbeat service, opt-in and bounded | Implemented |
| Delegation and parallel work | Depth-zero bounded tasks plus an explicit read-only delegated-research profile | Implemented, read-only |
| Browser automation | One explicitly shared tab; sensitive paths blocked; writes remain approval-gated | Read-first implemented |
| GitHub workflows | Read-only PR overview, checks, and diff through local `gh` | Implemented |
| MCP | Governed inventory, profile filtering, readiness probes, and safe diagnostics | Implemented foundation |
| Calendar | Read-only `calendar-read` profile over the supported local Google token; no event writes | Implemented, read-only |
| Workspace inspection | Explicit `workspace-inspect` profile over bounded `read_file` and `list_dir`; no writes or shell | Implemented, read-only |
| Workspace diagnostics | Explicit `workspace-run` profile over bounded read-only `exec` commands | Implemented, read-only |
| Governed workspace changes | Explicit `workspace-build` proposals with owner approval, payload fingerprints, workspace bounds, and audited execution | Implemented, bounded |
| Notes and email | Add one adapter at a time behind the same profile/readiness/approval contract | Deliberately staged |
| Remote terminals and worker backends | Not part of Pico's personal local boundary | Excluded |
| Broad messaging matrix, marketplace, billing | Keep existing channels; do not grow a catalogue without a proven workflow | Excluded |

## Integration rule

Every new integration must ship as one vertical slice:

1. owner/session-scoped storage and a narrow adapter;
2. explicit capability profile and readiness state;
3. read path before any write path;
4. proposal and approval for external writes;
5. bounded activity, run, and evidence records;
6. negative tests for owner leakage, setup failure, timeout, and forbidden use.

This leaves Pico capable of the Hermes mechanics that make repeated personal
work useful without turning Pico into a generic agent platform.

Reference: [Hermes Agent](https://github.com/NousResearch/hermes-agent).
