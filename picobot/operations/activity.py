"""Durable, redacted evidence of Pico tool execution."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ToolActivity:
    id: str
    owner_id: str
    session_key: str
    profile_id: str
    capability_id: str | None
    tool_name: str
    risk: str
    outcome: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class ToolActivityStore:
    """Store only tool evidence, never arguments, results, or credentials."""

    def __init__(self, workspace: Path):
        root = workspace / "operations"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-activity.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS tool_activity (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    capability_id TEXT,
                    tool_name TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tool_activity_owner_session_created_idx
                    ON tool_activity(owner_id, session_key, created_at DESC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _activity(row: sqlite3.Row) -> ToolActivity:
        return ToolActivity(**dict(row))

    def record(
        self,
        *,
        owner_id: str,
        session_key: str,
        profile_id: str,
        capability_id: str | None,
        tool_name: str,
        risk: str,
        outcome: str,
    ) -> ToolActivity:
        if outcome not in {"success", "error", "blocked"}:
            raise ValueError("Unsupported tool activity outcome")
        activity = ToolActivity(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            profile_id=profile_id,
            capability_id=capability_id,
            tool_name=tool_name,
            risk=risk,
            outcome=outcome,
            created_at=self._now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_activity(
                    id, owner_id, session_key, profile_id, capability_id,
                    tool_name, risk, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity.id,
                    activity.owner_id,
                    activity.session_key,
                    activity.profile_id,
                    activity.capability_id,
                    activity.tool_name,
                    activity.risk,
                    activity.outcome,
                    activity.created_at,
                ),
            )
        return activity

    def list(self, owner_id: str, session_key: str, *, limit: int = 30) -> list[ToolActivity]:
        limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tool_activity
                WHERE owner_id = ? AND session_key = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (owner_id, session_key, limit),
            ).fetchall()
        return [self._activity(row) for row in rows]
