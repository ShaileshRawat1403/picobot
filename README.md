# Picobot

A lightweight, privacy-focused AI agent framework with multi-channel support and workflow automation.

![Picobot Logo](assets/logo.svg)

## Features

- **Multi-Channel**: Telegram, WhatsApp, Discord, Slack, Web, and more
- **LLM Providers**: OpenAI, Anthropic, Gemini, DeepSeek, and 15+ providers
- **Workflow Integration**: Connect to DAX for supervised automation
- **Tool Ecosystem**: File operations, web search, calendar, cron scheduling
- **Memory**: Persistent session memory with automatic consolidation
- **Extensible**: MCP server support, custom tools, and skills

## Quick Start

```bash
# Install
pip install picobot

# Initialize
picobot onboard

# Chat
picobot agent -m "Hello!"

# Or start the gateway (with channels)
picobot gateway
```

## Configuration

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

## CLI Commands

| Command | Description |
|---------|-------------|
| `picobot onboard` | Initialize config and workspace |
| `picobot agent -m "..."` | Single message mode |
| `picobot gateway` | Start gateway with channels |
| `picobot web` | Start web interface |
| `picobot doctor` | Health check |
| `picobot status` | Show status |
| `picobot channels status` | Channel status |
| `picobot provider login` | OAuth login |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                      Picobot                          │
├─────────────────────────────────────────────────────┤
│  Channels    │  Agent Loop  │  Bus    │  Providers  │
│  ─────────   │  ──────────   │  ───    │  ─────────  │
│  Telegram    │  Context     │  Queue  │  OpenAI    │
│  WhatsApp    │  Memory      │  Events │  Gemini     │
│  Discord     │  Skills      │         │  Anthropic  │
│  Web         │  Tools       │         │  DeepSeek   │
└─────────────────────────────────────────────────────┘
                           │
                     DAX Engine
```

## Documentation

See [docs/](docs/) for detailed documentation.

## License

MIT
