# Pico

<p align="center">
  <img src="assets/mascot-pico-hero.svg" alt="Pico" width="280"/>
</p>

<p align="center">
  <strong>A local-first personal AI work partner.</strong>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"/>
  </a>
  <a href="https://python.org">
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python"/>
  </a>
</p>

---

## What is Pico?

Pico is a local-first personal AI work partner for solo builders and multi-hat
knowledge workers. It helps you think, remember, research, create, organise,
and safely act through a deliberately small set of controlled capabilities.

Its daily loop is:

```text
Orient → discuss → compose → inspect → approve → retain → resume
```

Pico is useful with one configured model provider and its local workspace. It
does not require DAX, Flowright, PaneTera, Soothsayer, or another ecosystem
service to install or use. Those systems may later be enabled as optional
adapters; they do not define Pico's everyday experience.

### Four ways of working

- **Research** — examine sources, preserve evidence, and produce source-backed notes.
- **Build** — inspect a local project, diagnose its state, and prepare bounded changes.
- **Write** — turn ideas and evidence into versioned Markdown-first work products.
- **Organise** — maintain tasks, missions, schedules, reviews, and the next useful step.

The interface is workflow-first, not connector-first. Browser, workspace,
GitHub, calendar, and future integrations remain bounded capabilities beneath
these ways of working.

Read the full [Pico V1 Product Contract](docs/PICO_V1_PRODUCT_CONTRACT.md) and
[supported surface](docs/PICO_SUPPORTED_SURFACE.md) before enabling optional
integrations.

---

## Quick Start

### Install

```bash
pip install picobot
```

### Configure

```bash
picobot onboard
```

This creates `~/.picobot/config.json` and a local Pico workspace. Choose one
supported model connection, then add only that provider's credential to the
profile-local `.env` beside `config.json`:

```json
{
  "agents": {
    "defaults": {
      "model": "openai/gpt-4.1-mini",
      "provider": "openai"
    }
  }
}
```

### Run

```bash
picobot web
```

Open the printed local URL (normally <http://127.0.0.1:18792>) and begin a
session. Use `picobot doctor` when setup is incomplete.

---

## Commands

| Command                  | What it does            |
| ------------------------ | ----------------------- |
| `picobot onboard`        | First-time setup        |
| `picobot agent -m "..."` | Quick question          |
| `picobot web`            | Start the local web workbench |
| `picobot backup`         | Export credential-free Pico Home state |
| `picobot gateway`        | Start explicitly enabled compatibility channels |
| `picobot status`         | Check what's running    |
| `picobot doctor`         | Diagnose issues         |

## Personal Memory

Pico's active personal memory is local to its workspace in SQLite. It stores
confirmed facts with their source, lifecycle, and owner scope; it does not use
random embedding fallbacks or treat raw chat history as durable truth.

Use the channel controls to manage it directly:

```text
/remember I prefer short status updates
/memory list
/memory search status updates
/memory why <memory-id>
/memory forget <memory-id>
```

Only confirmed, unexpired memories can be recalled. Inferred information is
reserved for a later review workflow and will not silently change your profile.
Legacy `MEMORY.md`, `HISTORY.md`, and `vectors.pkl` files are left untouched,
but are no longer used by Pico's active recall path.

### Durable artifacts

Pico can save versioned owner-scoped notes, briefs, plans, reports, checklists,
structured data, code/config text, and source-link bundles. Supported formats
include Markdown, plain text, JSON, YAML, CSV, TSV, JSONL, and URI lists. Each
revision remains inside the configured workspace and can be downloaded with a
format-aware filename or exported as a safe standalone HTML share view. See
[`docs/PICO_ARTIFACTS.md`](docs/PICO_ARTIFACTS.md) for the validation and
sharing contract, including portable ZIP bundles with an integrity manifest.
For a practical release check using real weekly work, see
[`docs/PICO_PERSONAL_WORKFLOW_TESTS.md`](docs/PICO_PERSONAL_WORKFLOW_TESTS.md).

### Credentials

Keep provider secrets in a profile-local `.env` beside Pico's `config.json`,
not in the JSON config. For example:

```text
OPENAI_API_KEY=...
```

The profile directory is ignored by Git. `config.json` retains only the chosen
model, provider, workspace, and other non-secret settings.

### Local browser UI

The browser UI is a real Pico channel: it uses the same agent loop and local
personal memory as the CLI. Keep it bound to localhost for personal use:

```bash
cd /Users/Shailesh/MYAIAGENTS/picobot
./scripts/pico-web
```

Then open <http://127.0.0.1:18792>. The WebSocket listens on `18791`; the page
is served on `18792`. Each browser connection receives only replies for its
own server-bound chat session. If Pico is already listening, the launcher
reports the existing URL instead of creating a duplicate. If another local
service owns the pair, Pico selects the next available WebSocket/HTTP pair and
prints the new URL. The launcher uses `.venv` automatically when it exists.
This is a Python service; `npm run dev` is not a supported command.

### Pico Home backup

Create a portable archive of Pico's durable sessions, artifacts, memory,
projects, runs, tasks, missions, workflows, and review state:

```bash
picobot backup --config .picobot/config.json
```

By default the archive is written beside the Pico workspace in `pico-backups/`.
It deliberately excludes Pico configuration, `.env` files, runtime tokens, and
known credential filenames. Choose an output directory outside the workspace
with `--output /path/to/backups`.

### Isolated Pico test

Test Pico without using your normal workspace or Soothsayer connection:

```bash
cd /Users/Shailesh/MYAIAGENTS/picobot
python3 -m picobot onboard \
  --config /private/tmp/pico-test/config.json \
  --workspace /private/tmp/pico-test/workspace
```

Add a test-only provider key to that newly created config, then use the same
two options with `agent` and `memory`. This is a standalone Pico test; it does
not launch a DAX or Soothsayer connection.

---

## Optional messaging channels

The local web workbench and CLI are Pico's primary interfaces. A messaging
channel is an explicitly enabled compatibility capability, not part of the
default setup or a required product surface. Enable one only when a recurring
workflow proves it reduces meaningful friction.

### Telegram setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the token
4. Send any message to your new bot, then check [@userinfobot](https://t.me/userinfobot) to get your user ID
5. Add both to your config

---

## Supported Models

| Provider     | Auth            | Notes                                |
| ------------ | --------------- | ------------------------------------ |
| **OpenAI**   | API Key         | Primary hosted API                   |
| **Gemini**   | API Key         | Secondary hosted API                 |
| **Anthropic**| API Key         | Hosted API                           |
| **Codex CLI**    | Official provider-owned CLI login  | ChatGPT/Codex subscription; no token readback |
| **Gemini CLI** | Official provider-owned CLI login | Gemini subscription; no token readback |
| **Ollama**   | Local           | Local endpoint                       |
| **Custom**   | API Key + URL   | One OpenAI-compatible endpoint       |

### Using a Gemini subscription

Pico uses the official Gemini CLI for subscription sign-in and transport. It
does not read or reuse Gemini CLI tokens:

```bash
# Sign in with Google in the official CLI
gemini

# Or launch Pico's provider-owned setup wrapper
picobot provider login gemini-oauth
```

Use the Gemini API connection for Pico tool execution and governed actions.

---

## What Pico Can Do

- **Answer questions** - Search the web, read documentation
- **Create durable work** - Notes, briefs, plans, data, links, and code artifacts
- **Research** - Bounded web search, page fetches, and one explicitly shared browser tab
- **Governed execution** - Missions, bounded tasks, approvals, and evidence-linked runs
- **Workspace help** - Read, diagnose, or propose bounded changes inside the configured workspace
- **Optional skills** - GitHub review, calendar read, MCP, and delegated research stay opt-in

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                           Pico                              │
│             (Local-first personal work partner)             │
├─────────────────────────────────────────────────────────────┤
│  Web + CLI    │   Context   │   Runs    │   OpenAI      │
│  Optional     │   Memory    │   Audit   │   Gemini      │
│  channels     │   Skills    │           │   Anthropic   │
│               │   Workflows │           │   Local       │
└─────────────────────────────────────────────────────────────┘
                │
                ▼
   ┌─────────────────────────────────────────────────────────┐
   │ Optional, explicitly enabled adapters                    │
   │ Browser Bridge · GitHub review · Calendar · MCP · DAX    │
   └─────────────────────────────────────────────────────────┘
```

---

## Security

Pico is designed for responsible automation:

- **Shell allowlist** - Only approved commands run
- **Workspace bounds** - File ops stay in your workspace
- **Exact approvals** - Sensitive actions bind to a reviewed proposal
- **User allowlists** - Only whitelisted users can interact

---

## Configuration

The smallest useful configuration is a model selection and local workspace.
Channels and ecosystem adapters are optional and are not required for Pico's
daily workbench.

```json
{
  "agents": {
    "defaults": {
      "model": "openai/gpt-4.1-mini",
      "provider": "openai",
      "temperature": 0.7
    }
  }
}
```

---

## Local integration API

`picobot` retains local technical endpoints for explicitly enabled
compatibility integrations. They are not required to use Pico's web workbench
or CLI:

```
POST /api/picobot/webhook/health      # Health checks
POST /api/picobot/webhook/activity    # Activity sync
GET  /api/picobot/stats              # Get stats
POST /api/picobot/send              # Send message
GET  /api/picobot/commands/pending   # Poll commands
```

---

## Troubleshooting

**Bot not responding?**

```bash
picobot doctor
picobot channels status
```

**Subscription setup?** Use the provider-owned CLI (`codex login` or the
official Gemini CLI sign-in). Pico only records redacted readiness metadata;
it never reads or stores those CLI tokens.

---

## License

MIT License - see [LICENSE](LICENSE)
