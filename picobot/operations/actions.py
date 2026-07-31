"""Durable approval ledger for future external Pico actions.

The ledger stores a human-readable proposal and a bounded target label. It
never stores tool arguments, form data, cookies, or credentials. Executors are
deliberately separate: they may run only after `approve` succeeds for the
unchanged action ID.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class ProposedAction:
    id: str
    owner_id: str
    session_key: str
    profile_id: str
    capability_id: str
    tool_name: str
    target: str
    summary: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    resolved_at: str | None
    executed_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ProposedActionStore:
    """Owner/session-scoped action proposals with no approval reuse."""

    _MAX_TARGET_LENGTH = 240
    _MAX_SUMMARY_LENGTH = 600
    _DEFAULT_EXPIRY = timedelta(minutes=15)
    _MAX_EXPIRY = timedelta(hours=24)
    def __init__(self, workspace: Path):
        root = workspace / "operations"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-actions.db"
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
                CREATE TABLE IF NOT EXISTS proposed_actions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    target TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    resolved_at TEXT,
                    executed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS proposed_actions_owner_session_updated_idx
                    ON proposed_actions(owner_id, session_key, updated_at DESC);
                """
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _clean(value: object, label: str, limit: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Action {label} is required")
        clean = " ".join(value.split())
        if not clean:
            raise ValueError(f"Action {label} is required")
        if len(clean) > limit:
            raise ValueError(f"Action {label} is limited to {limit} characters")
        return clean

    @staticmethod
    def _action(row: sqlite3.Row) -> ProposedAction:
        return ProposedAction(**dict(row))

    def stage(
        self,
        *,
        owner_id: str,
        session_key: str,
        profile_id: str,
        capability_id: str,
        tool_name: str,
        target: object,
        summary: object,
        expires_in: timedelta | None = None,
    ) -> ProposedAction:
        if not all(isinstance(value, str) and value.strip() for value in (owner_id, session_key, profile_id, capability_id, tool_name)):
            raise ValueError("Action owner, session, profile, capability, and tool are required")
        lifetime = expires_in or self._DEFAULT_EXPIRY
        if lifetime <= timedelta(0) or lifetime > self._MAX_EXPIRY:
            raise ValueError("Action expiry must be between now and 24 hours")
        now = self._now()
        action = ProposedAction(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            profile_id=profile_id,
            capability_id=capability_id,
            tool_name=tool_name,
            target=self._clean(target, "target", self._MAX_TARGET_LENGTH),
            summary=self._clean(summary, "summary", self._MAX_SUMMARY_LENGTH),
            status="proposed",
            created_at=self._iso(now),
            updated_at=self._iso(now),
            expires_at=self._iso(now + lifetime),
            resolved_at=None,
            executed_at=None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proposed_actions(
                    id, owner_id, session_key, profile_id, capability_id, tool_name,
                    target, summary, status, created_at, updated_at, expires_at,
                    resolved_at, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    action.id,
                    action.owner_id,
                    action.session_key,
                    action.profile_id,
                    action.capability_id,
                    action.tool_name,
                    action.target,
                    action.summary,
                    action.status,
                    action.created_at,
                    action.updated_at,
                    action.expires_at,
                ),
            )
        return action

    def get(self, owner_id: str, action_id: str, *, session_key: str | None = None) -> ProposedAction:
        query = "SELECT * FROM proposed_actions WHERE id = ? AND owner_id = ?"
        params: list[object] = [action_id, owner_id]
        if session_key is not None:
            query += " AND session_key = ?"
            params.append(session_key)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise KeyError("Action was not found for this session")
        return self._action(row)

    def _expire_if_needed(self, action: ProposedAction) -> ProposedAction:
        if action.status != "proposed" or datetime.fromisoformat(action.expires_at) > self._now():
            return action
        now = self._iso(self._now())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE proposed_actions
                SET status = 'expired', updated_at = ?, resolved_at = ?
                WHERE id = ? AND owner_id = ? AND status = 'proposed'
                """,
                (now, now, action.id, action.owner_id),
            )
        return self.get(action.owner_id, action.id, session_key=action.session_key)

    def list(self, owner_id: str, session_key: str, *, limit: int = 30) -> list[ProposedAction]:
        limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proposed_actions
                WHERE owner_id = ? AND session_key = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (owner_id, session_key, limit),
            ).fetchall()
        return [self._expire_if_needed(self._action(row)) for row in rows]

    def resolve(self, owner_id: str, action_id: str, session_key: str, decision: str) -> ProposedAction:
        if decision not in {"approve", "reject"}:
            raise ValueError("Action decision must be approve or reject")
        action = self._expire_if_needed(self.get(owner_id, action_id, session_key=session_key))
        if action.status != "proposed":
            raise ValueError(f"Only proposed actions can be resolved; this action is {action.status}")
        next_status = "approved" if decision == "approve" else "rejected"
        now = self._iso(self._now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proposed_actions
                SET status = ?, updated_at = ?, resolved_at = ?
                WHERE id = ? AND owner_id = ? AND session_key = ? AND status = 'proposed'
                """,
                (next_status, now, now, action_id, owner_id, session_key),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action state changed before the decision could be recorded")
        return self.get(owner_id, action_id, session_key=session_key)

    def mark_execution(self, owner_id: str, action_id: str, session_key: str, *, success: bool) -> ProposedAction:
        action = self.get(owner_id, action_id, session_key=session_key)
        if action.status != "approved":
            raise ValueError("Only an approved action can execute")
        now = self._iso(self._now())
        next_status = "executed" if success else "failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proposed_actions
                SET status = ?, updated_at = ?, executed_at = ?
                WHERE id = ? AND owner_id = ? AND session_key = ? AND status = 'approved'
                """,
                (next_status, now, now, action_id, owner_id, session_key),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action state changed before execution")
        return self.get(owner_id, action_id, session_key=session_key)
