# Picobot

<p align="center">
  <img src="assets/mascot-pico-hero.svg" alt="Picobot" width="280"/>
</p>

<p align="center">
  <strong>The personal and multi-channel front door to the DAX Suite.</strong>
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

## What is Picobot?

Picobot is a dual-mode AI assistant that works where you work—Telegram, Discord, WhatsApp, or web. It serves as both a powerful standalone personal assistant and the **governed ingress edge** for the DAX Suite.

### The DAX Suite Model
Picobot is part of a three-tier architecture designed for safety, privacy, and deterministic control:

1.  **Picobot receives**: Multi-channel ingress and personal assistance.
2.  **DAX executes**: Governed execution, approvals, and audit-grade control.
3.  **Soothsayer supervises**: The operator plane for oversight and replay.

---

## Dual-Mode Operation

Picobot adapts its behavior based on the complexity and risk of your request:

### Mode 1: Standalone Personal Assistant
Perfect for daily coordination and personal productivity.
- **Personal**: Reminders, calendar queries, scheduling, and routines.
- **Workspace**: Context retrieval, file lookups, and repository status.
- **Research**: Quick web searches and documentation summaries.

### Mode 2: DAX-Backed Governed Ingress
When tasks involve significant system changes or code generation, Picobot routes them to DAX for governed execution.
- **SDLC**: Writing, modifying, or refactoring code.
- **Operations**: System setup, deployments, and migrations.
- **Governance**: Every "risky" action requires your explicit approval via Picobot.

---

## How It Works

You chat with Picobot naturally. Behind the scenes, it maintains a **Capability Ladder**:

- **Band 1 (Local)**: Fast, non-governed assistant tasks (Reminders, Search).
- **Band 2 (Local)**: Workspace and repository context retrieval.
- **Band 3 (DAX)**: Governed execution—actions are drafted by DAX, approved by you, and audited by Soothsayer.

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

This creates `~/.picobot/config.json`. Edit it to add your channels:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  },
  "agents": {
    "defaults": {
      "model": "gemini-2.5-pro",
      "provider": "gemini_oauth"
    }
  }
}
```

### Run

```bash
picobot gateway
```

Now chat with your bot on Telegram!

---

## Commands

| Command                  | What it does            |
| ------------------------ | ----------------------- |
| `picobot onboard`        | First-time setup        |
| `picobot agent -m "..."` | Quick question          |
| `picobot gateway`        | Start with all channels |
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
format-aware filename. See [`docs/PICO_ARTIFACTS.md`](docs/PICO_ARTIFACTS.md)
for the validation and sharing contract.

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
own server-bound chat session. The launcher intentionally does not stop an
existing Pico process; press `Ctrl+C` in its terminal before starting a new one.

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

## Setting Up Telegram

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
| **Codex**    | Official OAuth  | ChatGPT/Codex subscription           |
| **Gemini CLI** | Official OAuth | Gemini subscription                  |
| **Ollama**   | Local           | Local endpoint                       |
| **Custom**   | API Key + URL   | One OpenAI-compatible endpoint       |

### Using a Gemini subscription

Pico uses the official Gemini CLI for subscription sign-in and transport. It
does not read or reuse Gemini CLI tokens:

```bash
# Sign in with Google in the official CLI
gemini

# Or launch Pico's setup wrapper
picobot provider login gemini_oauth
```

Use the Gemini API connection for Pico tool execution and governed actions.

---

## What Picobot Can Do

- **Answer questions** - Search the web, read documentation
- **Manage files** - Read, write, organize your workspace
- **Git operations** - Status, commit, push, PR summaries
- **Code tasks** - Write, review, debug code
- **Schedule tasks** - Set up recurring reminders
- **Calendar & email** - Manage your time and communications
- **And more** - Skills can be added to extend capabilities

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Picobot                              │
│                (Personal Assistant Edge)                    │
├─────────────────────────────────────────────────────────────┤
│  Channels     │   Agent     │   Bus      │   Providers    │
│  ─────────    │   ────────  │   ───     │   ──────────   │
│  Telegram     │   Context   │   Queue   │   Gemini      │
│  Discord      │   Memory    │   Events  │   OpenAI      │
│  WhatsApp     │   Skills    │           │   Claude       │
│  Web          │   Tools     │           │   Ollama       │
└─────────────────────────────────────────────────────────────┘
                │                           ▲
                ▼                           │
   ┌──────────────────────────┐    ┌──────────────────────────┐
   │          DAX             │    │       Soothsayer         │
   │  (Execution Authority)   │────▶   (Operator Plane)       │
   └──────────────────────────┘    └──────────────────────────┘
```

---

## Security

Picobot is designed for responsible automation:

- **Shell allowlist** - Only approved commands run
- **Workspace bounds** - File ops stay in your workspace
- **DAX approval** - Sensitive actions need your approval
- **User allowlists** - Only whitelisted users can interact

---

## Configuration

```json
{
  "channels": {
    "telegram": { "enabled": true, "token": "..." }
  },
  "agents": {
    "defaults": {
      "model": "gemini-2.5-pro",
      "provider": "gemini_oauth",
      "temperature": 0.7
    }
  },
  "dax": {
    "url": "http://localhost:3000",
    "workspaceId": "your-workspace-id"
  }
}
```

---

## API

Picobot exposes endpoints for integration:

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

**Token expired?**

```bash
picobot provider login openai_codex
# or
picobot provider login gemini_oauth
```

---

## License

MIT License - see [LICENSE](LICENSE)
