# Picobot Architecture

## System Overview

Picobot is the personal and multi-channel front door to the **DAX Suite**. It operates as a dual-mode assistant, providing seamless personal productivity (Standalone Mode) while serving as the governed ingress edge for high-stakes operations (DAX-Backed Mode).

Picobot's architecture is built on the **"Picobot receives, DAX executes, Soothsayer supervises"** tier model.

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
│            (Personal Assistant Edge Ingress)                      │
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
└──────────────────────────┬──────────────────────────────────────┘
                           │
                ┌──────────▼──────────┐
                │        DAX          │
                │(Execution Authority)│
                └──────────┬──────────┘
                           │
                ┌──────────▼──────────┐
                │     Soothsayer      │
                │  (Operator Plane)   │
                └─────────────────────┘
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

LLM provider abstraction with support for multiple backends. Picobot prefers **DAX-backed providers** for governed workflows to ensure consistency and auditability.

**Supported Providers:**
- **DAX (Primary)**: Governed execution via the DAX engine.
- **Direct Gemini**: Google Gemini via OAuth or API Key.
- **OpenAI**: GPT-4, GPT-3.5 Turbo.
- **Anthropic**: Claude 3 Opus, Sonnet, Haiku.
- **Ollama**: Local model hosting for private, Band 1/2 tasks.
- **Custom**: OpenAI-compatible APIs.

### 5. DAX Integration (Execution Authority)

DAX serves as the **Execution Authority** for the suite. When Picobot identifies a "Band 3" request (governed execution), it hands off the intent to DAX.

**Core Responsibilities:**
- **Governance**: Policy enforcement and risk-based approval workflows.
- **Execution**: Canonical environment for file modification and code generation.
- **Auditability**: Complete audit trail of all approved/denied operations.
- **Recovery**: Replay and recovery of interrupted or failed workflows.

## Data Flow

### Message Processing (The Capability Ladder)

Picobot evaluates every message against a **Capability Ladder** to determine the execution path:

```
1. User sends message via channel
         ↓
2. Channel bridge receives and normalizes
         ↓
3. Agent Engine evaluates Intent (Band 1, 2, or 3)
         ↓
4. Path Selection:
   ├─ Band 1/2 (Local): Picobot executes directly (Search, Reminders, File Reads)
   └─ Band 3 (DAX): Picobot hands off to DAX Execution Authority
         ↓
5. DAX Workflow (if Band 3):
   ├─ Draft operation
   ├─ Request Approval (relayed via Picobot)
   └─ Execute upon user approval
         ↓
6. Response generation (LLM)
         ↓
7. Outbound message sent to user
         ↓
8. Activity synced to Soothsayer (Operator Plane supervision)
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
