"""Vector memory for semantic search using embeddings."""

from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None

from loguru import logger

EMBEDDING_DIM = 384


@dataclass
class MemoryEntry:
    """A single memory with embedding."""

    content: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: np.ndarray | None = None


class VectorMemory:
    """Semantic memory using embeddings for similarity search."""

    def __init__(self, workspace: Path, model_name: str = "all-MiniLM-L6-v2"):
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.vector_file = self.memory_dir / "vectors.pkl"
        self.entries: list[MemoryEntry] = []
        self.model_name = model_name
        self._model = None
        self._load()

    def _load(self) -> None:
        if self.vector_file.exists():
            try:
                with open(self.vector_file, "rb") as f:
                    data = pickle.load(f)
                    self.entries = [MemoryEntry(**e) for e in data.get("entries", [])]
                    self.model_name = data.get("model_name", self.model_name)
                logger.info("Loaded {} vector memories", len(self.entries))
            except Exception:
                logger.warning("Failed to load vectors, starting fresh")
                self.entries = []

    def _save(self) -> None:
        data = {
            "entries": [
                {
                    "content": e.content,
                    "timestamp": e.timestamp,
                    "metadata": e.metadata,
                }
                for e in self.entries
            ],
            "model_name": self.model_name,
        }
        with open(self.vector_file, "wb") as f:
            pickle.dump(data, f)

    def _get_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            logger.info("Loaded embedding model: {}", self.model_name)
            return self._model
        except ImportError:
            logger.warning("sentence-transformers not installed, using fake embeddings")
            return None

    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self._get_model()
        if model is None or np is None:
            return np.random.rand(len(texts), EMBEDDING_DIM).astype(np.float32) if np else None
        embeddings = model.encode(texts, convert_to_numpy=True)
        return embeddings

    def add(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Add a new memory entry with embedding."""
        entry = MemoryEntry(
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata=metadata or {},
        )
        entry.embedding = self._encode([content])[0]
        self.entries.append(entry)
        self._save()
        logger.debug("Added memory: {}", content[:50])

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Search memories by semantic similarity."""
        if not self.entries:
            return []
        query_embedding = self._encode([query])[0]
        scores: list[tuple[int, float]] = []
        for i, entry in enumerate(self.entries):
            if entry.embedding is None:
                continue
            sim = float(np.dot(query_embedding, entry.embedding))
            scores.append((i, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in scores[:top_k]:
            results.append((self.entries[idx].content, score))
        return results

    def delete_oldest(self, count: int = 10) -> None:
        """Delete oldest entries."""
        self.entries = self.entries[count:] if count < len(self.entries) else []
        self._save()

    def count(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        """Clear all vector memories."""
        self.entries = []
        self._save()
