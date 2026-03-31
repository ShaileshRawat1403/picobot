# Picobot

<p align="center">
  <img src="assets/mascot-pico-hero.svg" alt="Pico - Your AI Assistant" width="300"/>
</p>

<p align="center">
  <strong>Your personal AI agent that works everywhere.</strong>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"/>
  </a>
  <a href="https://python.org">
    <img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="Python"/>
  </a>
  <a href="https://github.com/picobot-ai/picobot/stargazers">
    <img src="https://img.shields.io/github/stars/picobot-ai/picobot" alt="Stars"/>
  </a>
  <a href="https://github.com/picobot-ai/picobot/releases">
    <img src="https://img.shields.io/github/v/release/picobot-ai/picobot" alt="Version"/>
  </a>
</p>

---

## What is Picobot?

Picobot is a **privacy-focused, multi-channel AI agent framework** that brings the power of Large Language Models to your favorite messaging platforms. Whether you're on Telegram, Discord, or building custom integrations, Picobot serves as your intelligent assistant that never sleeps.

### Key Principles

- **Privacy First**: Your data stays on your infrastructure
- **Multi-Platform**: Works where you work
- **Extensible**: Build custom skills and tools
- **Supervised**: DAX integration for approval workflows

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Channel** | Telegram, WhatsApp, Discord, Slack, Web, Email |
| **LLM Providers** | 15+ providers including Gemini, Claude, GPT-4, Ollama |
| **Tool Ecosystem** | Web search, file ops, calendar, cron jobs, GitHub |
| **Memory** | Persistent sessions with automatic memory consolidation |
| **Skills** | Extensible skill system for custom capabilities |
| **DAX Integration** | Supervised automation with approval workflows |

---

## Quick Start

### Installation

```bash
# From PyPI
pip install picobot

# Or from source
git clone https://github.com/picobot-ai/picobot.git
cd picobot
pip install -e .
```

### Initialize

```bash
picobot onboard
```

This creates the configuration file at `~/.picobot/config.json`.

### Configure

Edit `~/.picobot/config.json`:

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
# Gateway mode (all channels)
picobot gateway

# Single agent message
picobot agent -m "Hello, help me debug this code"

# Web interface only
picobot web
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `picobot onboard` | Initialize configuration |
| `picobot agent -m "..."` | Single message to the agent |
| `picobot gateway` | Start with all channels |
| `picobot web` | Start web interface |
| `picobot status` | Show current status |
| `picobot doctor` | Run health diagnostics |
| `picobot channels status` | Show channel status |
| `picobot provider login` | OAuth login for providers |

---

## Channel Setup

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your bot token
3. Get your user ID: send `/start` to [@userinfobot](https://t.me/userinfobot)
4. Add to config:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "123456:ABC-DEF...",
      "allowFrom": ["123456789"]
    }
  }
}
```

### Discord

1. Create application at [Discord Developer Portal](https://discord.com/developers)
2. Add bot to your server
3. Get bot token and guild ID
4. Configure:

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "guildId": "YOUR_GUILD_ID"
    }
  }
}
```

### WhatsApp

1. Scan QR code with `picobot channels whatsapp scan`
2. Link persists in config

---

## LLM Providers

### Supported Providers

| Provider | Auth Method | Models |
|----------|-------------|--------|
| **Google Gemini** | API Key / OAuth (DAX) | Gemini 1.5 Pro, 2.0, Flash |
| **OpenAI** | API Key | GPT-4o, GPT-4 Turbo, GPT-3.5 |
| **Anthropic** | API Key | Claude 3.5 Sonnet, Opus, Haiku |
| **DeepSeek** | API Key | DeepSeek Chat, Coder |
| **Ollama** | Local | All local models |
| **Groq** | API Key | Llama, Mixtral |
| **OpenRouter** | API Key | 100+ models |
| **Azure OpenAI** | API Key | GPT-4, Codex |
| **Custom** | API Key + URL | OpenAI-compatible APIs |

### OAuth Setup (Gemini via DAX)

Picobot supports OAuth authentication for Gemini through DAX:

```bash
# Start DAX OAuth server
cd /Users/Shared/MYAIAGENTS/dax
python -m dax run

# In another terminal
picobot provider login gemini_oauth
```

This opens browser for OAuth flow, storing tokens securely.

---

## Skills

Picobot includes built-in skills:

| Skill | Description |
|-------|-------------|
| `web` | Search the web, fetch pages |
| `file` | Read, write, list files |
| `terminal` | Execute whitelisted commands |
| `calendar` | Manage calendar events |
| `email` | Send and read emails |
| `github` | GitHub API operations |
| `cron` | Schedule recurring tasks |
| `memory` | Persistent memory storage |
| `summarize` | Summarize content |
| `task` | Task management |
| `weather` | Weather information |

### Creating Custom Skills

```markdown
# picobot/skills/my-skill/SKILL.md

## Identity
- name: my-skill
- version: 1.0.0
- description: My custom skill

## Triggers
- "do something with {topic}"

## Actions
- action: my_action
  description: Does something
  params:
    - name: topic
      type: string
      required: true
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                         Picobot                          │
├─────────────────────────────────────────────────────────┤
│  Channels     │   Agent Core   │    Bus     │  Providers│
│  ─────────    │   ──────────   │   ───     │  ──────── │
│  Telegram     │   Context      │   Queue   │   Gemini  │
│  WhatsApp     │   Memory       │   Events  │   OpenAI  │
│  Discord      │   Skills       │           │   Claude  │
│  Slack        │   Tools        │           │   Ollama  │
│  Web          │   DAX Client   │           │   Custom  │
└─────────────────────────────────────────────────────────┘
                           │
                    DAX Engine (Supervision)
                           │
                    Soothsayer Dashboard
```

### Core Components

- **Channels**: Bridge between messaging platforms and Picobot
- **Agent**: LLM-powered decision making and response generation
- **Bus**: Event queue for async operations
- **Memory**: Session persistence and context management
- **Skills**: Modular capability extensions
- **DAX**: Approval workflows and supervision

---

## Security

Picobot includes multiple security layers:

| Feature | Description |
|---------|-------------|
| **Shell Allowlist** | Only pre-approved commands execute |
| **Workspace Restriction** | File ops limited to designated workspace |
| **DAX Approval** | File modifications require approval |
| **Channel Allowlists** | Whitelist users per channel |
| **Token Storage** | Secure credential management |

### Environment Variables

```bash
# Channel tokens
PICOBOT_CHANNELS__TELEGRAM__TOKEN=xxx

# Agent defaults
PICOBOT_AGENTS__DEFAULTS__MODEL=gemini-2.5-pro
PICOBOT_AGENTS__DEFAULTS__PROVIDER=gemini_oauth

# Tool config
PICOBOT_TOOLS__WEB__SEARCH__API_KEY=xxx
```

---

## Configuration Reference

```json
{
  "channels": {
    "telegram": { ... },
    "discord": { ... },
    "whatsapp": { ... }
  },
  "agents": {
    "defaults": {
      "model": "gemini-2.5-pro",
      "provider": "gemini_oauth",
      "temperature": 0.7,
      "maxTokens": 8192
    }
  },
  "tools": {
    "terminal": {
      "allowed": ["git", "ls", "cat"],
      "workspace": "/path/to/workspace"
    }
  },
  "dax": {
    "url": "http://localhost:3000",
    "workspaceId": "your-workspace-id"
  },
  "memory": {
    "consolidationThreshold": 10,
    "maxHistory": 1000
  }
}
```

---

## API Integration

Picobot can be integrated with external systems via webhooks:

### Health Check
```
POST /api/picobot/webhook/health
```

### Activity Sync
```
POST /api/picobot/webhook/activity
{
  "type": "message_received",
  "channelType": "telegram",
  "userId": "123456",
  "message": "Hello!"
}
```

### Commands
```
GET  /api/picobot/commands/pending  # Poll for pending commands
POST /api/picobot/commands/{id}/acknowledge
POST /api/picobot/commands/{id}/complete
```

---

## Troubleshooting

### Common Issues

**Bot not responding?**
```bash
picobot doctor
picobot channels status
```

**OAuth token expired?**
```bash
picobot provider login gemini_oauth
```

**Database sync issues?**
```bash
# Check Soothsayer connection
curl http://localhost:3000/api/health
```

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
# Development setup
git clone https://github.com/picobot-ai/picobot.git
cd picobot
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
```

---

## License

MIT License - see [LICENSE](LICENSE)

---

<p align="center">
  <strong>Made with care by developers, for developers.</strong>
</p>

<p align="center">
  <img src="assets/logo-icon.svg" alt="Picobot" width="32" height="32"/>
  <br/>
  <sub>Picobot v2.0</sub>
</p>
