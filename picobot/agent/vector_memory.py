"""Retired vector-memory compatibility surface.

Picobot now uses ``picobot.memory.PersonalMemoryStore`` with local SQLite FTS5
for durable recall.  The former implementation persisted pickle data and
invented random vectors when an embedding model was unavailable; neither is a
safe basis for personal memory.
"""

from __future__ import annotations

from pathlib import Path


class VectorMemoryUnavailable(RuntimeError):
    """Raised when code attempts to use the retired, unsafe vector store."""


class VectorMemory:
    """Compatibility shim that fails closed instead of returning false recall."""

    def __init__(self, workspace: Path, model_name: str = "all-MiniLM-L6-v2"):
        self.workspace = workspace
        self.model_name = model_name

    @staticmethod
    def _unavailable() -> None:
        raise VectorMemoryUnavailable(
            "Vector memory is retired. Use Picobot's SQLite personal memory controls instead."
        )

    def add(self, content: str, metadata: dict | None = None) -> None:
        self._unavailable()

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        self._unavailable()

    def delete_oldest(self, count: int = 10) -> None:
        self._unavailable()

    def count(self) -> int:
        return 0

    def clear(self) -> None:
        self._unavailable()
