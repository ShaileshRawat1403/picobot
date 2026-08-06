# Pico architecture

This document describes the system as it actually exists in `picobot/`. For
product scope and boundaries, see [`PICO_V1_PRODUCT_CONTRACT.md`](PICO_V1_PRODUCT_CONTRACT.md)
and [`PICO_SUPPORTED_SURFACE.md`](PICO_SUPPORTED_SURFACE.md). For the daily
workbench's information architecture, see
[`PICO_WEB_APP_PRD.md`](PICO_WEB_APP_PRD.md).

## System overview

Pico is a local-first personal AI work partner. It runs entirely against a
local SQLite-backed workspace (`~/.picobot` by default, or a profile-specific
path) and one configured model provider. It does not require any external
service to install or use.

```text
┌─────────────────────────────────────────────────────────────────┐
│                         Interfaces                               │
│   Local web workbench (picobot/web)   │   CLI (picobot/cli)      │
│   Optional channels: Telegram, Discord, WhatsApp, Slack, Matrix, │
│   DingTalk, Feishu, WeCom, QQ, Mochat, Email (picobot/channels)  │
└──────────────────────────────┬────────────────────────────────────┘
                                │
                         picobot/bus
                  (MessageBus: inbound/outbound queues,
                   analytics, webhooks, optional DAX bridge)
                                │
                        picobot/agent
              (AgentLoop: the core per-turn processing engine)
        ┌───────────────┬──────────────┬──────────────┬───────────┐
        │ Context        │ Memory       │ Skills       │ Tools     │
        │ (context/)     │ (memory/,    │ (skills/,    │ (agent/   │
        │                │  learning/)  │  agent/skills)│  tools/) │
        └───────────────┴──────────────┴──────────────┴───────────┘
                                │
        ┌───────────────┬──────────────┬──────────────┬───────────┐
        │ Missions       │ Tasks        │ Runs         │ Artifacts │
        │ (missions/)    │ (tasks/)     │ (runs/)      │(artifacts/)│
        └───────────────┴──────────────┴──────────────┴───────────┘
                                │
                       picobot/providers
        (OpenAI, Gemini, Anthropic, Ollama, custom endpoint, and the
         official Codex/Gemini subscription CLIs — via LiteLLM)
```

Everything below the interfaces layer runs locally in the same process.
There is no required backend service, queue, or database server — durable
state is a set of SQLite files under the Pico workspace (see
`picobot/config/paths.py` for the layout).

## Core components

### Channels (`picobot/channels/`)

Each channel implements `BaseChannel` (`picobot/channels/base.py`) and
translates one messaging surface into `InboundMessage`/`OutboundMessage`
objects on the shared `MessageBus`. The local web workbench
(`picobot/channels/web.py`) and the CLI are the primary, always-available
interfaces; every other channel (Telegram, Discord, WhatsApp, Slack, Matrix,
DingTalk, Feishu, WeCom, QQ, Mochat, email) is an explicitly enabled
compatibility surface, not part of the default setup. `picobot/channels/manager.py`
owns channel lifecycle; `picobot/channels/registry.py` auto-discovers channel
modules rather than hardcoding a registry.

### Bus (`picobot/bus/`)

`MessageBus` (`bus/queue.py`) is an in-process async queue connecting
channels to the agent loop. `bus/analytics.py` tracks local usage counters.
`bus/webhooks.py` exposes the optional local HTTP endpoints listed in the
README. `bus/dax_queue.py`, `bus/dax_auth.py`, `bus/dax_service.py`, and
`bus/soothsayer_service.py` implement the *optional* DAX/Soothsayer bridge —
disabled unless explicitly configured, and never required for Pico's daily
workbench or CLI use.

### Agent engine (`picobot/agent/`)

`AgentLoop` (`agent/loop.py`) is the per-turn processing engine: it builds
context, resolves the effective runtime policy, calls the configured
provider, executes any requested tool calls, and persists run/evidence
records. Its main sub-components:

- **Context** (`picobot/context/`) — token-bounded context planning
  (`planner.py`), durable compaction with evidence (`compactor.py`), and
  session-scoped evidence storage (`evidence.py`, `store.py`).
- **Memory** (`picobot/memory/`, `picobot/learning/`) — a local SQLite store
  of confirmed personal facts with explicit lifecycle states (`proposed`,
  `confirmed`, `rejected`, `forgotten`), lexical FTS5 recall, and a
  reviewable learning/feedback path. No embedding fallback and no silent
  promotion of chat history into durable memory.
- **Skills** (`picobot/skills/`, `agent/skills.py`) — Markdown-defined,
  dynamically discovered capabilities with declared prerequisites.
- **Tools** (`agent/tools/`) — the concrete capabilities a turn can invoke:
  filesystem read/write/edit, shell exec (allowlisted, see below), web
  search/fetch, browser read/action, calendar, cron, GitHub PR review, MCP,
  subagent spawn, and the optional DAX handoff tool. `agent/tools/registry.py`
  and `operations/registry.py`/`operations/governed_registry.py` gate which
  tools are actually exposed to a given session/profile.

### Work governance (`picobot/missions/`, `picobot/tasks/`, `picobot/runs/`, `picobot/operations/`)

Missions and bounded tasks track multi-step work; runs and
`operations/activity.py` record an evidence trail for what actually
executed. `operations/actions.py` and `operations/executor.py` implement the
propose → inspect → approve → execute → audit flow for workspace-changing
actions: a proposal is durable and inert until an owner approves it by
fingerprint, at which point `operations/workspace_executor.py` or
`operations/browser_executor.py` performs the bounded, already-reviewed
change. `policy/runtime.py` resolves which provider/model/response-mode
applies to a given turn from global config plus any session override,
re-validating against the live provider setup on every resolution.

### Providers (`picobot/providers/`)

`LiteLLMProvider` (`providers/litellm_provider.py`) is the default path to
OpenAI, Gemini, Anthropic, and other LiteLLM-supported APIs.
`azure_openai_provider.py` and `custom_provider.py` cover Azure OpenAI and a
single named OpenAI-compatible endpoint. The official Codex CLI and Gemini
CLI are supported as provider-owned subscription transports: Pico invokes
them and records redacted readiness metadata, but never reads or stores
their tokens (`providers/setup.py`, `providers/connections.py`).
`providers/registry.py` holds the per-provider spec (env vars, model
prefixes, prompt-caching support) that `LiteLLMProvider` reads instead of
hardcoding provider-specific branches.

### Workflows (`picobot/workflows/`)

A small local workflow engine (`workflows/engine.py`,
`workflows/compiler.py`, `workflows/store.py`) for step-based durable work
that isn't a single chat turn — distinct from, and not dependent on, the
optional DAX/Soothsayer bridge.

## Security model

Pico's actual controls, as implemented:

- **Shell allowlist** (`agent/tools/shell.py`) — the `exec` tool tokenizes
  the full command with shell-aware punctuation splitting (respecting
  quoting) and checks every resulting command segment's base name against
  `SAFE_COMMANDS_ALLOWLIST`, not just the first token, so chaining via
  `;`/`&&`/`||`/`|`/`(...)`  cannot smuggle a disallowed command past an
  allowed one. Command/process substitution (`` ` ``, `$(`, `<(`, `>(`) is
  rejected outright. A separate deny-pattern list blocks specific
  destructive commands (`rm -rf`, `dd`, `mkfs`, fork bombs, etc.) as
  defense in depth.
- **Workspace bounds** — filesystem tools resolve every path against the
  configured workspace/allowed directory and reject anything that escapes
  it (`agent/tools/filesystem.py::_resolve_path`).
- **Exact approvals** — sensitive workspace and browser actions are
  proposed, fingerprinted, and only executed after an explicit owner
  approval that binds to that exact fingerprint (`operations/actions.py`,
  `operations/executor.py`).
- **Capability/profile gating** — a session only sees the tools its active
  capability profile grants (`operations/registry.py`,
  `operations/governed_registry.py`); MCP servers must pass a readiness
  check before their tools are ever offered.
- **User allowlists** — each messaging channel checks configured user
  allowlists before responding (`channels/base.py` and per-channel config).
- **Credential handling** — provider secrets live in a profile-local `.env`
  next to `config.json`, never in the JSON config itself, and
  `picobot backup` explicitly excludes `.env` files, runtime tokens, and
  known credential filenames from its export.

## Extension points

### Custom channels

Implement `BaseChannel` (`picobot/channels/base.py`) and register it in
`picobot/channels/registry.py`.

### Custom skills

Add a `SKILL.md` (optionally with supporting scripts) under
`picobot/skills/<name>/`. See `picobot/skills/README.md` for the format.

### Custom tools

Implement `Tool` (`picobot/agent/tools/base.py`) and register it with the
`ToolRegistry` used by `AgentLoop._register_default_tools`
(`picobot/agent/loop.py`).

### Custom providers

Implement `LLMProvider` (`picobot/providers/base.py`), or add a spec to
`picobot/providers/registry.py` if the provider is already reachable through
LiteLLM.

## Optional DAX/Soothsayer bridge

DAX and Soothsayer are optional adapters, not a required part of Pico's
architecture. When explicitly configured, Pico can hand off a bounded task
brief to DAX as a governed execution plane and sync activity to Soothsayer
for supervision. Neither is initialized, polled, or required unless the
corresponding config is present — see `bus/dax_service.py` and
`bus/soothsayer_service.py`. Do not read this section as describing Pico's
default or primary execution path; `PICO_V1_PRODUCT_CONTRACT.md` and the
README are the source of truth for that.
