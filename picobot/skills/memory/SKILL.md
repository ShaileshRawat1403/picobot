---
name: memory
description: Two-layer memory with vector search + grep. Always load this skill.
always: true
---

# Memory

Picobot has two memory systems:

## 1. Vector Memory (Semantic Search)

Fast, semantic similarity search for past conversations and facts.

- **Search**: Use `search_memory` tool for natural language queries like "what did I say about the API?" or "previous project preferences"
- **Store**: Use `remember` tool to save important facts with automatic embedding

Example:
```
Tool: search_memory
query: "what did I ask about OAuth?" 
limit: 5
```

## 2. File Memory (Grep Search)

For precise text matching and large history files.

- `memory/MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded.
- `memory/HISTORY.md` — Append-only event log. Search with grep when vector search misses.

### Grep Search (for HISTORY.md)

- **Linux/macOS:** `grep -i "keyword" memory/HISTORY.md`
- **Windows:** `findstr /i "keyword" memory\HISTORY.md`

## When to Update Memory

Use `remember` tool immediately for important facts:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Auto-consolidation

Old conversations are automatically summarized and appended to file memory when the session grows large.
