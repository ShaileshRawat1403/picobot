# Picobot Architecture

## System Overview

Picobot is a modular AI agent framework designed for multi-channel messaging integration. It consists of several core components that work together to provide a seamless AI assistant experience.

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interfaces                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │Telegram │  │Discord  │  │WhatsApp │  │  Web    │            │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘            │
└───────┼────────────┼────────────┼────────────┼──────────────────┘
        │            │            │            │
        └────────────┴─────┬──────┴────────────┘
                           │
                    ┌──────▼──────┐
                    │   Bridge    │
                    │   Layer     │
                    └──────┬──────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                    Picobot Core                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      Agent Engine                          │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │   │
│  │  │ Context  │  │ Memory   │  │  Skills  │  │  Tools   │  │   │
│  │  │ Manager  │  │ Store    │  │  Loader  │  │ Registry │  │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                       │
│  ┌────────────────────────▼───────────────────────────────┐     │
│  │                         Bus                              │     │
│  │   Event Queue  │  Message Broker  │  Task Scheduler    │     │
│  └──────────────────────────────────────────────────────────┘     │
│                           │                                       │
│  ┌────────────────────────▼───────────────────────────────┐     │
│  │                    Provider Layer                       │     │
│  │   ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────┐ │     │
│  │   │ Gemini │ │ OpenAI │ │Claude  │ │   Ollama       │ │     │
│  │   │  DAX   │ │   API  │ │  API   │ │   (Local)      │ │     │
│  │   └────────┘ └────────┘ └────────┘ └────────────────┘ │     │
│  └──────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │     DAX     │
                    │   Engine    │
                    └──────┬──────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                    Soothsayer                                    │
│                    Dashboard                                      │
└──────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Channel Bridge

The bridge layer handles protocol translation between messaging platforms and Picobot's internal format.

**Supported Channels:**
- Telegram Bot API
- Discord Webhooks & Bot
- WhatsApp (via WhatsApp Web)
- Slack Webhooks
- WebSocket for custom integrations

**Responsibilities:**
- Normalize messages from different platforms
- Handle authentication per channel
- Manage connection state
- Rate limiting and retries

### 2. Agent Engine

The core AI engine that processes messages and generates responses.

**Sub-components:**

#### Context Manager
- Maintains conversation context
- Handles multi-turn dialogues
- Manages system prompts

#### Memory Store
- Persistent session storage
- Memory consolidation
- Context window management

#### Skills Loader
- Dynamic skill discovery
- Skill execution sandbox
- Permission management

#### Tools Registry
- Tool registration and discovery
- Execution environment
- Result formatting

### 3. Bus (Event System)

The asynchronous message bus for decoupled communication.

**Features:**
- Event queue for reliable delivery
- Message batching
- Task scheduling
- Dead letter handling

### 4. Provider Layer

LLM provider abstraction with support for multiple backends.

**Supported Providers:**
- **DAX OAuth**: Google Gemini via OAuth (production)
- **OpenAI**: GPT-4, GPT-3.5 Turbo
- **Anthropic**: Claude 3 Opus, Sonnet, Haiku
- **DeepSeek**: DeepSeek Chat, Coder
- **Ollama**: Local model hosting
- **Custom**: OpenAI-compatible APIs

### 5. DAX Integration

Supervision layer for controlled automation.

**Features:**
- Approval workflows for sensitive operations
- Execution logging
- Rate limiting
- Workspace isolation

## Data Flow

### Message Processing

```
1. User sends message via channel
         ↓
2. Channel bridge receives and normalizes
         ↓
3. Agent Engine creates context
         ↓
4. Memory loaded for conversation
         ↓
5. Skills evaluated for relevance
         ↓
6. LLM provider generates response
         ↓
7. Tools executed if needed
         ↓
8. DAX approval for sensitive ops
         ↓
9. Response formatted for channel
         ↓
10. Bridge sends to user
         ↓
11. Activity logged to Soothsayer
```

### Session Lifecycle

```
Session Start
      ↓
Load Memory/Context
      ↓
Message Loop
  ├─ Process Input
  ├─ Generate Response
  └─ Execute Tools
      ↓
Memory Consolidation (periodic)
      ↓
Session End
      ↓
Archive to Persistent Storage
```

## Database Schema

### PicobotInstance
```sql
- id: String (PK)
- workspaceId: String (FK)
- name: String
- status: String (online/offline)
- config: JSON
- lastSeenAt: DateTime
```

### PicobotChannel
```sql
- id: String (PK)
- picobotId: String (FK)
- channelType: String
- enabled: Boolean
- status: String
```

### PicobotSession
```sql
- id: String (PK)
- picobotId: String (FK)
- channelId: String (FK)
- userId: String
- status: String
- startedAt: DateTime
```

### PicobotActivity
```sql
- id: String (PK)
- picobotId: String (FK)
- type: String
- message: String
- timestamp: DateTime
```

## Security Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Security Layers                     │
├─────────────────────────────────────────────────────┤
│  1. Channel Auth    │ Token validation, OAuth      │
│  2. User Allowlist  │ Per-channel user whitelist   │
│  3. Workspace       │ File access restricted       │
│  4. Shell Allowlist │ Only approved commands       │
│  5. DAX Approval    │ Sensitive ops need approval  │
│  6. Rate Limiting   │ Per-user, per-channel caps   │
└─────────────────────────────────────────────────────┘
```

## Extension Points

### Custom Channels
Implement the `ChannelAdapter` interface:
```python
class ChannelAdapter(Protocol):
    async def connect() -> None: ...
    async def disconnect() -> None: ...
    async def send(message: Message) -> str: ...
    async def receive() -> AsyncIterator[Message]: ...
```

### Custom Skills
Place in `picobot/skills/my-skill/SKILL.md`

### Custom Tools
Register via the Tools Registry API.

### Custom Providers
Implement `LLMProvider` interface.
