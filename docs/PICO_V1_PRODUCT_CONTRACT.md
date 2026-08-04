# Pico V1 Product Contract

Status: product intent checkpoint  
Date: 2026-08-03

## Product identity

**Pico** is a local-first personal AI work partner for solo builders and
multi-hat knowledge workers. It helps its owner think, remember, research,
create, organise, and safely act through a deliberately small set of
controlled capabilities.

`picobot` remains the repository and package name. Pico is the user-facing
product. It must be useful with one configured model provider and its local
workspace; DAX, Flowright, PaneTera, Soothsayer, and other ecosystem systems
are optional compatibility adapters, never prerequisites.

## The daily loop

```text
Orient
→ discuss and challenge
→ compose a bounded plan or workflow
→ inspect and approve consequential work
→ retain an artifact, decision, and reviewed memory
→ resume with evidence
```

Pico has four visible ways of working: **Research**, **Build**, **Write**, and
**Organise**. They are intent lenses and starter workflows, not integration
catalogues or isolated product silos.

## Pico Home and connected projects

V1 is single-owner and local-first:

```text
One Pico Home
→ many connected projects
→ one active focus
→ optional explicit comparison context
```

Pico Home contains owner-approved personal orientation and reviewed personal
memory. A connected project is a durable context record, not merely a folder
or repository. It may be a software project, research domain, writing/content
practice, business/client project, or personal project.

A project can begin without a repository or any connected source. Initial
source types are optional and explicit: an allowlisted local folder, an
explicitly enabled GitHub repository, a selected URL, or a Pico-owned artifact
reference. A project with no inspected source reports that honestly rather than
claiming current knowledge.

Knowledge scope and action scope stay distinct:

```text
Knowledge scope: the active project plus owner-selected comparison projects.
Action scope: one named project, exact resource, capability, proposal, and
approval boundary.
```

Cross-project context is always disclosed with its reason and inspection
freshness. Comparison context does not authorize multi-project execution.

## Core mechanics and bounded capabilities

Pico owns conversation, context planning, reviewed memory and learning,
artifacts, tasks, missions, schedules, lightweight workflows, capability
profiles, approvals, audit evidence, local search, and the local web workbench
plus CLI.

Capabilities are broad in mechanics but narrow in integrations:

| Capability | V1 approach |
| --- | --- |
| Read | Explicit browser tab, bounded web retrieval, selected local/project sources |
| Remember | Owner-reviewed personal and project memory with provenance |
| Create | Versioned local artifacts, drafts, and bounded patches |
| Organise | Tasks, missions, schedules, workflow runs, and review queues |
| Communicate | Drafts and artifacts; no autonomous sending or publishing |
| Execute | Existing least-authority profiles, exact proposals, approvals, and evidence |
| Extend | Optional adapter or governed MCP contract, not a marketplace |

Every node or proposal can reduce the permissions inherited from its project or
Pico Home. It must never expand them. Product disagreement is not execution
authority: Pico may challenge a direction with evidence and a smaller path, but
only an existing policy or safety boundary can block execution.

## Supported V1 surface

Required:

- local web workbench and secondary CLI;
- one chosen model connection;
- personal orientation, reviewed memory, explicit thinking stances, and clear
  context evidence;
- research-to-artifact, versioned artifacts, task/mission/workflow support,
  visible run state, explicit approvals, search, and resumption;
- an explicitly shared browser tab through the Pico Browser Bridge;
- versioned export of Pico-owned artifacts and local data residency.

Explicitly enabled, not part of the default daily surface:

- GitHub read and pull-request review;
- read-only calendar workflows;
- governed MCP servers;
- approved bounded workspace changes;
- one messaging channel only after repeated personal use proves the need.

Deferred from V1:

- email and Drive adapters; broad messaging and connector catalogues;
- autonomous external sending, publishing, payments, or production changes;
- desktop automation, cloud workers, mobile, voice, multi-tenancy, and
  enterprise deployment;
- private provider endpoints, cookie reuse, or Pico-managed OAuth until a
  separate security design establishes a public and keychain-backed flow.

## Release and implementation rules

Pico V1 is complete when a new owner can install Pico, configure one model,
open the local workbench, establish a task, produce an evidence-linked artifact
or action proposal, approve deliberate writes, and resume the work later
without knowing the surrounding ecosystem. A release-ready V1 also needs an
owner-controlled backup/export path for Pico Home state that excludes provider
credentials.

The backup boundary is implemented through `picobot backup`: it produces a
portable archive of Pico-owned durable state using consistent SQLite snapshots,
and excludes configuration, environment files, runtime tokens, and known
credential filenames.

Connected local Git and GitHub sources can also be explicitly refreshed into
bounded project-awareness snapshots: branch/change signals and recent history
are retained as freshness-labelled project evidence, not as an implicit full
repository index. See [`PICO_PROJECT_BRAIN.md`](PICO_PROJECT_BRAIN.md).

Before a new integration enters scope, a real recurring workflow must prove a
missing capability, including its narrow read path, readiness, approval,
evidence, failure, and removal contracts. Legacy adapters may remain for
compatibility, but they must not be default, auto-probed, or advertised as
required.

## Next product slices

1. Project Registry: owner-created project records, explicit resources,
   capabilities, relationships, freshness, and archival.
2. Project Orientation and Context Resolver: active project, objective, role
   lens, stance, challenge policy, temporary constraints, and a receipt for
   what actually entered each turn.
3. Conversation-to-Workflow composition over the existing durable workflow
   contract, with readable work verbs and owner acceptance.
4. Maintenance Cockpit and real weekly workflow proof before another connector
   is added.
