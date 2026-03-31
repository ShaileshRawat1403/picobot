# Picobot

A lightweight, privacy-focused AI agent framework with multi-channel support.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

## What is Picobot?

Picobot is your personal AI assistant that works across multiple messaging platforms. It combines the power of Large Language Models with a flexible tool ecosystem, allowing you to automate tasks, search the web, manage files, and more - all from chat.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Channel** | Telegram, WhatsApp, Discord, Slack, Web, Email, and more |
| **15+ LLM Providers** | OpenAI, Anthropic, Gemini, DeepSeek, Ollama, and custom endpoints |
| **Tool Ecosystem** | Web search, file operations, calendar, cron jobs |
| **Memory** | Persistent sessions with automatic memory consolidation |
| **Skills** | Extensible skill system for custom capabilities |
| **DAX Integration** | Supervised automation with approval workflows |

## Installation

```bash
pip install picobot
```

Or install from source:

```bash
git clone https://github.com/picobot-ai/picobot.git
cd picobot
pip install -e .
```

## Quick Start

### 1. Initialize

```bash
picobot onboard
```

This creates the configuration file at `~/.picobot/config.json`.

### 2. Configure

Edit `~/.picobot/config.json` with your settings:

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

### 3. Run

**CLI Mode** (single message):
```bash
picobot agent -m "Hello, help me write a Python script"
```

**Gateway Mode** (with Telegram/other channels):
```bash
picobot gateway
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `picobot onboard` | Initialize configuration |
| `picobot agent -m "..."` | Single message to the agent |
| `picobot gateway` | Start with all channels |
| `picobot web` | Start web interface only |
| `picobot status` | Show current status |
| `picobot doctor` | Run health diagnostics |
| `picobot channels status` | Show channel status |
| `picobot provider login` | OAuth login for providers |

## Setting Up Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your bot token
3. Get your user ID (send `/start` to [@userinfobot](https://t.me/userinfobot))
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

## Environment Variables

Picobot supports configuration via environment variables:

| Variable | Description |
|----------|-------------|
| `PICOBOT_CHANNELS__TELEGRAM__TOKEN` | Telegram bot token |
| `PICOBOT_AGENTS__DEFAULTS__MODEL` | Default LLM model |
| `PICOBOT_AGENTS__DEFAULTS__PROVIDER` | Default provider |
| `PICOBOT_TOOLS__WEB__SEARCH__API_KEY` | Search API key |

## Security

Picobot includes several security features:

- **Shell Allowlist**: Only pre-approved commands can execute
- **Workspace Restriction**: File operations can be limited to workspace
- **DAX Approval**: File modifications require approval workflow
- **Channel Allowlists**: Whitelist users per channel

## Supported LLM Providers

| Provider | Auth |
|----------|------|
| OpenAI | API Key |
| Anthropic | API Key |
| Gemini | API Key / OAuth |
| DeepSeek | API Key |
| Ollama | Local |
| OpenRouter | API Key |
| Azure OpenAI | API Key |
| Custom OpenAI-compatible | API Key / URL |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                     Picobot                      │
├─────────────────────────────────────────────────┤
│  Channels    │  Agent    │  Bus     │ Providers │
│  ─────────   │  ────────  │  ───    │ ────────  │
│  Telegram    │  Context  │  Queue  │  OpenAI   │
│  WhatsApp   │  Memory   │  Events │  Gemini   │
│  Discord    │  Skills   │         │  DeepSeek │
│  Web       │  Tools    │         │  Custom   │
└─────────────────────────────────────────────────┘
                      │
                DAX Engine
```

## Documentation

For detailed documentation, see the [docs](docs/) directory.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.
