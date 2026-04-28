"""DAX message queue for offline requests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class QueuedDAXRequest:
    """A DAX request that failed and is queued for retry."""

    id: str
    action: str
    payload: dict[str, Any]
    timestamp: str
    chat_id: str
    channel: str
    retries: int = 0


class DAXMessageQueue:
    """Queue for DAX requests when server is unavailable."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.queue_file = workspace / ".picobot" / "dax_queue.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self._queue: list[QueuedDAXRequest] = []
        self._load()

    def _load(self) -> None:
        if self.queue_file.exists():
            try:
                data = json.loads(self.queue_file.read_text("utf-8"))
                self._queue = [QueuedDAXRequest(**item) for item in data.get("queue", [])]
                logger.info("Loaded {} queued DAX requests", len(self._queue))
            except Exception as e:
                logger.warning("Failed to load DAX queue: {}", e)
                self._queue = []

    def _save(self) -> None:
        data = {"queue": [asdict(req) for req in self._queue]}
        self.queue_file.write_text(json.dumps(data, indent=2), "utf-8")

    def enqueue(
        self,
        action: str,
        payload: dict[str, Any],
        chat_id: str,
        channel: str,
    ) -> str:
        """Add a request to the queue."""
        req_id = f"q_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        request = QueuedDAXRequest(
            id=req_id,
            action=action,
            payload=payload,
            timestamp=datetime.now().isoformat(),
            chat_id=chat_id,
            channel=channel,
        )
        self._queue.append(request)
        self._save()
        logger.info("Queued DAX request: {} action={}", req_id, action)
        return req_id

    def dequeue(self) -> QueuedDAXRequest | None:
        """Get the next request from the queue."""
        if self._queue:
            return self._queue.pop(0)
        return None

    def requeue(self, request: QueuedDAXRequest) -> None:
        """Re-queue a failed request with incremented retry count."""
        request.retries += 1
        request.timestamp = datetime.now().isoformat()
        self._queue.append(request)
        self._save()
        logger.info("Re-queued request {} (retry {})", request.id, request.retries)

    def remove(self, request_id: str) -> bool:
        """Remove a specific request from the queue."""
        for i, req in enumerate(self._queue):
            if req.id == request_id:
                self._queue.pop(i)
                self._save()
                return True
        return False

    def get_pending(self) -> list[QueuedDAXRequest]:
        """Get all pending requests."""
        return list(self._queue)

    def count(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        """Clear all queued requests."""
        self._queue.clear()
        self._save()


def get_dax_queue(workspace: Path) -> DAXMessageQueue:
    """Get the DAX message queue instance."""
    return DAXMessageQueue(workspace)
