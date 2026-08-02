# Pico workflow DAG contract

Pico workflows are small, owner-controlled directed acyclic graphs. The graph
is a durable program description, not a permission to execute arbitrary code.

## Node contract

Each node has:

- a stable `id`, bounded `kind`, title, description, and canvas position;
- a typed harness in `config.harness`;
- a safe node configuration with no credentials or secret-like keys.

The harness records the authority that the node may request:

```json
{
  "profile_id": "research",
  "toolset": "research",
  "approval_required": true,
  "retry_limit": 1,
  "timeout_seconds": 180
}
```

The harness is checked again by the runtime adapter before any provider,
browser, task, or artifact operation. A visual node never becomes an escape
hatch around Pico's existing capability profiles.

## Transition contract

Each transition has:

- `source` and `target` node identifiers;
- an event condition such as `success`, `error`, `approved`, `rejected`,
  `timeout`, `true`, or `false`;
- an optional owner-facing note.

Transitions are editable objects. They are not merely lines on the canvas.
The runtime selects a transition from the persisted event result and records
the chosen path in the run evidence.

## Editor behavior

- Drag a node body to move it. Positions are draft data and do not affect
  execution order.
- Drag an output port to an input port to create a transition.
- Select a node to edit its harness and configuration.
- Select a transition to edit its event and guard note.
- Pan the empty canvas; zoom and fit without changing the graph contract.
- Save a draft, approve it explicitly, then run the approved version.

## Bounded surface

The first full workflow surface supports manual and schedule triggers, agent
tasks, browser reads and approval-gated browser actions, approval gates,
conditions, waits, artifact creation, and end nodes. Integrations remain
behind the existing Pico provider and capability contracts.

Cycles, arbitrary code nodes, secret-bearing configuration, and unbounded
remote execution remain out of scope until a real personal workflow justifies
an explicit contract for them.

## Editor affordances

The editor borrows proven interaction patterns from React Flow, Rete, Flowise,
Activepieces, and Langflow without importing their integration catalogues:

- undo and redo keep graph edits reversible before a draft is saved;
- Arrange gives a quick readable left-to-right layout, while Fit resets the
  viewport and zoom controls preserve canvas space for larger graphs;
- a minimap and node context menu make a graph navigable without hiding the
  bounded inspector and approval model;
- selecting a node exposes its execution harness and a local preview that
  validates input shape without dispatching a provider, browser, or action;
- an evidence drawer mirrors the durable run timeline and highlights node
  states, approval pauses, and safe event summaries on the graph.

These affordances improve graph literacy and debugging. They do not add
arbitrary code execution, automatic approvals, cycles, or new integrations.
