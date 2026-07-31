# Pico Runtime Policy, Context, and MCP PRD

## Product decision

Pico becomes a useful personal productivity agent by making its runtime
choices visible, bounded, and durable: which model is serving a session, what
context is supplied, when context is compacted, and which external tools are
allowed to connect. This is a control layer for a local web agent, not a
desktop clone of Hermes or a remote autonomy platform.

This PRD complements, but does not replace:

- `PICO_WEB_APP_PRD.md` — web-first personal workbench;
- `PICO_OPERATING_LAYER_PRD.md` — capability profiles, browser sharing, and
  approval ledger; and
- `PICO_MISSION_HARNESS_PRD.md` — durable owner-directed work state.

## What Pico adopts from Hermes

Hermes was examined as an implementation reference, not a UI template. The
parts that are useful to Pico are real runtime mechanisms:

| Hermes mechanism | Pico adaptation |
| --- | --- |
| Main model plus auxiliary task assignments | A small policy with a primary model and only proven auxiliary roles. |
| Bounded context compression with a reference handoff | Durable, inspectable Pico compaction records that preserve source evidence. |
| Managed memory lifecycle and background work | Owner-scoped retrieval plus reviewable learning proposals; no silent promotion. |
| MCP inventory, test connection, tool filtering, and reload | A guided server inventory with profile-level permissions and activity evidence. |
| Gateway health and diagnostics | Local-only runtime diagnostics, not cloud/SSH gateway controls. |

## Explicit exclusions

Pico will **not** copy Hermes's billing, cloud account picker, remote gateway,
SSH executor, desktop shell, broad messaging matrix, unrestricted host tools,
or web-based secret editor. Provider secrets remain profile-local environment
values and are never returned to the browser.

Pico will not expose hidden model reasoning. It may show model identity,
latency/usage when actually available, tool outcomes, and compaction status.

## Principles

1. **Truthful controls.** A setting exists only when Pico can persist, apply,
   and test it.
2. **Local first.** The web workbench remains bound to the local machine. A
   future remote deployment is a separate authenticated product boundary.
3. **Session decisions are durable.** A session records its applicable model
   and capability profile so work can be resumed and audited coherently.
4. **Context is evidence, not magic.** Pico shows what it recalled and records
   compaction as a handoff; it does not silently erase history.
5. **MCP is least-privilege.** A configured server is not automatically an
   enabled tool. Connection, profile permission, and per-action approval are
   distinct decisions.
6. **One change at a time.** Each slice adds a backend contract, tests, then a
   restrained web surface. No settings-only mockups.

## Runtime policy model

### Global policy

Stored in Pico's non-secret profile configuration:

| Field | Initial behaviour |
| --- | --- |
| `primary_model` | Selected from known configured providers/models only. |
| `reasoning_effort` | `default`, `low`, `medium`, or `high`, only passed when the provider supports it. |
| `response_mode` | `concise`, `balanced`, or `detailed`; a Pico communication preference, not hidden reasoning. |
| `timezone` | IANA timezone used for schedules and dated output. |
| `fallback_status` | Read-only status of the configured fallback path; it is not a browser-side override. |

### Session policy

A session may use the global default or have an explicit owner-selected model
and capability profile. The policy is recorded with session metadata. A model
change affects future turns only and must be visible in the session evidence.

### Auxiliary roles

Auxiliary assignment is deliberately small. An unassigned role means “use the
primary model”; it does not create a second hidden agent.

| Role | First availability |
| --- | --- |
| `context_compression` | Implement in this PRD after a safe compaction engine exists. |
| `session_title` | Implement only when title generation is actually introduced; manual titles remain valid. |
| `page_summary` | Defer until browser reading has a repeatable owner test. |
| `mcp_planning`, `approval`, `vision` | Defer. They add cost and complexity without a proven Pico workflow. |

## Memory and context model

Pico already has owner-scoped explicit memory, proposal/confirmation states,
and a visible latest-turn context record. This PRD extends that model without
changing its safety boundary.

```text
confirmed, unexpired owner memory
  -> bounded relevance selection for one turn
  -> visible context record (memory references + history count)
  -> model turn
  -> optional learning proposal
  -> owner confirms, rejects, or forgets it
```

### Context compaction

When a session approaches its configured context budget, Pico may compact old
conversation into a `CompactionRecord`:

- retain a protected recent tail and the active user request;
- summarize only the older eligible history;
- record source message IDs, model/role used, timestamp, token estimate, and
  resulting handoff text;
- keep original session messages and artifacts intact; compaction changes the
  *next model request*, not the source transcript;
- show a concise “context compacted” event, never the model's hidden chain of
  thought;
- fail closed: if compaction fails, do not replace the current context with an
  incomplete summary.

The owner can inspect the latest compaction record in Context. Automatic
learning remains a proposal; compaction never creates personal memory.

## MCP and skills governance

Pico's existing MCP client becomes manageable through a server-owned registry.

| State | Meaning |
| --- | --- |
| `not_configured` | No connection definition exists. |
| `configured` | Definition is valid but not yet tested or enabled. |
| `ready` | Pico successfully tested the server and discovered tools. |
| `error` | The last probe failed; safe diagnostic text is retained. |
| `disabled` | Kept in configuration but unavailable to the agent. |

Each MCP server has a transport, timeout, non-secret display metadata, enabled
state, discovered tool list, and allowed Pico capability profiles. Credentials
are environment references only; the browser can see “configured” but never a
header, token, or value.

The first web surface supports a guided add/import, enable/disable, probe, and
per-profile tool allow-list. A raw `mcp.json` editor is an Advanced diagnostic
feature for a later slice, after import/export validation exists.

## Delivery plan / SOW

### RP1 — Runtime policy foundation

**Outcome:** one durable source of truth for global and session runtime policy.

- Define versioned, non-secret policy records and validation.
- Add server-owned APIs for read/update of global policy and session override.
- Resolve configured provider/model availability server-side.
- Record the served model and effective policy for each completed turn when
  trusted provider metadata is available.
- Add unit and browser-channel tests for invalid model rejection, owner/session
  isolation, and no secret leakage.

### RP2 — Context budget and compaction evidence

**Outcome:** long personal sessions remain usable without losing their source
history or obscuring what Pico used.

- Add a context budget resolver using provider capability when known and a
  conservative configured fallback otherwise.
- Implement `CompactionRecord` storage and protected-tail compaction.
- Introduce the `context_compression` auxiliary role only once RP1 can resolve
  it to a configured model.
- Extend Context with a human-readable compaction timeline and source links.
- Test threshold, retry/failure, owner isolation, no source-message deletion,
  and no automatic memory write.

### RP3 — MCP registry and governed connection

**Outcome:** Pico can safely use one real MCP server at a time in a personal
workflow.

- Create a validated MCP registry over the existing connection configuration.
- Add probe/discovery, safe error summary, timeout, enable/disable, and
  profile-scoped tool filtering.
- Record server/tool/outcome in the existing activity ledger.
- Require an existing profile/approval policy for any mutating MCP tool.
- Test malformed configuration, failed probe, disabled-server denial,
  profile denial, timeout, and credential redaction.

### RP4 — Minimal web control surfaces

**Outcome:** the owner can inspect and change the preceding real behaviour.

Add these areas to the Pico web workbench only after RP1–RP3 APIs pass:

```text
Settings
├── Model & response       primary model, supported reasoning, response mode
├── Memory & context       context budget, latest compaction, memory policy
├── Skills & MCP           server health, enabled tools, profile permissions
├── Workspace              local path/status, export and backup later
└── Safety & diagnostics   profile, browser share, safe errors, version
```

No remote gateway, SSH, billing, or secret-input panel is included.

## Order of execution

Start with **RP1**, then **RP2**. This directly improves every individual Pico
session and makes the self-improvement claim inspectable. Start **RP3** only
after a real personal workflow names the first MCP server Pico must use.
RP4 follows each backend slice incrementally; it is not a separate cosmetic
dashboard project.

## Acceptance criteria

1. Pico never presents a model, auxiliary role, MCP server, or tool as ready
   unless the active runtime has validated it.
2. A browser client cannot read or mutate another owner's policy, context
   record, or MCP activity.
3. Model and response choices apply only to later turns and leave a durable,
   non-secret evidence record.
4. Context compaction preserves original messages, artifacts, and mission
   evidence; its output is clearly marked as a reference handoff.
5. No compaction or learning flow silently creates confirmed memory.
6. Disabled, untested, unavailable, or profile-forbidden MCP tools are never
   added to the model's callable tool surface.
7. Full Pico tests and focused negative tests for each new API pass before the
   corresponding web control ships.
