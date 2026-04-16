"""Vector memory tool for semantic search."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from picobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from picobot.agent.vector_memory import VectorMemory


class VectorMemoryTool(Tool):
    """Tool for semantic memory search using embeddings."""

    name = "search_memory"
    description = "Search your long-term memory for relevant past conversations and facts using semantic similarity."

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query to search memory. E.g., 'what did I say about the API?' or 'previous project preferences'",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, vector_memory: VectorMemory):
        self._vm = vector_memory

    async def execute(self, query: str, limit: int = 5) -> str:
        results = self._vm.search(query, top_k=limit)
        if not results:
            return "No matching memories found."
        lines = ["## Relevant Memories\n"]
        for i, (content, score) in enumerate(results, 1):
            lines.append(f"**{i}.** (relevance: {score:.2f}) {content}")
        return "\n".join(lines)


class AddMemoryTool(Tool):
    """Tool for adding important information to memory."""

    name = "remember"
    description = "Save important information to your long-term memory for future reference."

    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact or information to remember. Be specific and include relevant context.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata (project, person, topic, etc.)",
                "default": {},
            },
        },
        "required": ["content"],
    }

    def __init__(self, vector_memory: VectorMemory):
        self._vm = vector_memory

    async def execute(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        self._vm.add(content, metadata)
        logger.info("Added to memory: {}", content[:50])
        return f"Remembered: {content[:100]}..."


def create_vector_memory_tools(vector_memory: VectorMemory) -> list[Tool]:
    """Create vector memory tools."""
    return [VectorMemoryTool(vector_memory), AddMemoryTool(vector_memory)]
