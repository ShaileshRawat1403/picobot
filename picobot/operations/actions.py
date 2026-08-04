"""Durable, session-scoped approval ledger for governed Pico actions.

The public projection contains only the proposal and its outcome.  A narrowly
scoped executor may keep a private, JSON-serialised payload so it can verify
the proposal fingerprint immediately before a *human-triggered* execution.
Raw payloads are never returned by ``to_dict()`` or included in UI evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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
    status: str  # proposed, approved, rejected, executed, failed, cancelled, expired
    created_at: str
    updated_at: str
    expires_at: str
    resolved_at: str | None = None
    executed_at: str | None = None
    mission_id: str | None = None
    blueprint_step_id: str | None = None
    expected_outcome: str | None = None
    payload_fingerprint: str | None = None
    payload: str | None = None  # Private payload (JSON string), NOT included in to_dict()!
    result_summary: str | None = None
    result_ref: str | None = None
    failure_category: str | None = None
    initiating_run_id: str | None = None

    def to_dict(self) -> dict:
        """Return safe public projection; NEVER include raw payload, secrets, or prompts."""
        d = asdict(self)
        d.pop("payload", None)
        return d


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
                    executed_at TEXT,
                    mission_id TEXT,
                    blueprint_step_id TEXT,
                    expected_outcome TEXT,
                    payload_fingerprint TEXT,
                    payload TEXT,
                    result_summary TEXT,
                    result_ref TEXT,
                    failure_category TEXT,
                    initiating_run_id TEXT
                );
                CREATE INDEX IF NOT EXISTS proposed_actions_owner_session_updated_idx
                    ON proposed_actions(owner_id, session_key, updated_at DESC);
                """
            )
            cursor = connection.execute("PRAGMA table_info(proposed_actions);")
            columns = {row["name"] for row in cursor.fetchall()}
            migrations = [
                ("mission_id", "TEXT"),
                ("blueprint_step_id", "TEXT"),
                ("expected_outcome", "TEXT"),
                ("payload_fingerprint", "TEXT"),
                ("payload", "TEXT"),
                ("result_summary", "TEXT"),
                ("result_ref", "TEXT"),
                ("failure_category", "TEXT"),
                ("initiating_run_id", "TEXT"),
            ]
            for col, col_type in migrations:
                if col not in columns:
                    connection.execute(f"ALTER TABLE proposed_actions ADD COLUMN {col} {col_type}")
            # Existing local workspaces can predate mission support. Create this
            # index only after the additive migration has supplied mission_id.
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS proposed_actions_mission_idx
                    ON proposed_actions(owner_id, mission_id)
                """
            )

    @staticmethod
    def compute_fingerprint(
        mission_id: str | None,
        blueprint_step_id: str | None,
        capability_id: str,
        summary: str,
        expected_outcome: str | None,
        payload_data: Any,
    ) -> str:
        raw = json.dumps(
            {
                "mission_id": mission_id,
                "blueprint_step_id": blueprint_step_id,
                "capability_id": capability_id,
                "summary": summary,
                "expected_outcome": expected_outcome,
                "payload": payload_data,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
        d = dict(row)
        return ProposedAction(**d)

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
        mission_id: str | None = None,
        blueprint_step_id: str | None = None,
        expected_outcome: str | None = None,
        payload_data: Any = None,
        payload_fingerprint: str | None = None,
        initiating_run_id: str | None = None,
    ) -> ProposedAction:
        if not all(isinstance(value, str) and value.strip() for value in (owner_id, session_key, profile_id, capability_id, tool_name)):
            raise ValueError("Action owner, session, profile, capability, and tool are required")
        lifetime = expires_in or self._DEFAULT_EXPIRY
        if lifetime <= timedelta(0) or lifetime > self._MAX_EXPIRY:
            raise ValueError("Action expiry must be between now and 24 hours")
        now = self._now()

        target_clean = self._clean(target, "target", self._MAX_TARGET_LENGTH)
        summary_clean = self._clean(summary, "summary", self._MAX_SUMMARY_LENGTH)
        outcome_clean = (
            self._clean(expected_outcome, "expected outcome", self._MAX_SUMMARY_LENGTH)
            if expected_outcome is not None
            else None
        )
        try:
            payload_str = (
                json.dumps(payload_data, sort_keys=True, separators=(",", ":"))
                if payload_data is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Action payload must be JSON serializable") from exc
        computed_fingerprint = (
            self.compute_fingerprint(
                mission_id,
                blueprint_step_id,
                capability_id,
                summary_clean,
                outcome_clean,
                payload_data,
            )
            if payload_data is not None
            else None
        )
        if payload_fingerprint is not None and payload_fingerprint != computed_fingerprint:
            raise ValueError("Action payload fingerprint does not match the proposal")

        action = ProposedAction(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            profile_id=profile_id,
            capability_id=capability_id,
            tool_name=tool_name,
            target=target_clean,
            summary=summary_clean,
            status="proposed",
            created_at=self._iso(now),
            updated_at=self._iso(now),
            expires_at=self._iso(now + lifetime),
            resolved_at=None,
            executed_at=None,
            mission_id=mission_id,
            blueprint_step_id=blueprint_step_id,
            expected_outcome=outcome_clean,
            payload_fingerprint=computed_fingerprint,
            payload=payload_str,
            result_summary=None,
            result_ref=None,
            failure_category=None,
            initiating_run_id=initiating_run_id,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO proposed_actions(
                    id, owner_id, session_key, profile_id, capability_id, tool_name,
                    target, summary, status, created_at, updated_at, expires_at,
                    resolved_at, executed_at, mission_id, blueprint_step_id,
                    expected_outcome, payload_fingerprint, payload, result_summary,
                    result_ref, failure_category, initiating_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)
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
                    action.mission_id,
                    action.blueprint_step_id,
                    action.expected_outcome,
                    action.payload_fingerprint,
                    action.payload,
                    action.initiating_run_id,
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
        if action.status not in {"proposed", "approved"} or datetime.fromisoformat(action.expires_at) > self._now():
            return action
        now = self._iso(self._now())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE proposed_actions
                SET status = 'expired', updated_at = ?, resolved_at = ?, failure_category = 'expired'
                WHERE id = ? AND owner_id = ? AND status IN ('proposed', 'approved')
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

    def list_owner(self, owner_id: str, *, limit: int = 100) -> list[ProposedAction]:
        """Return an owner's safe action records without exposing payloads."""
        limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proposed_actions
                WHERE owner_id = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (owner_id, limit),
            ).fetchall()
        return [self._expire_if_needed(self._action(row)) for row in rows]

    def list_by_mission(self, owner_id: str, mission_id: str, *, limit: int = 30) -> list[ProposedAction]:
        limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proposed_actions
                WHERE owner_id = ? AND mission_id = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (owner_id, mission_id, limit),
            ).fetchall()
        return [self._expire_if_needed(self._action(row)) for row in rows]

    def resolve(
        self,
        owner_id: str,
        action_id: str,
        session_key: str,
        decision: str,
        *,
        payload_fingerprint: str | None = None,
    ) -> ProposedAction:
        if decision not in {"approve", "reject"}:
            raise ValueError("Action decision must be approve or reject")
        action = self._expire_if_needed(self.get(owner_id, action_id, session_key=session_key))
        if action.status != "proposed":
            raise ValueError(f"Only proposed actions can be resolved; this action is {action.status}")
        if (
            decision == "approve"
            and action.payload_fingerprint
            and payload_fingerprint != action.payload_fingerprint
        ):
            raise ValueError("Action payload fingerprint mismatch")

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

    def cancel(self, owner_id: str, action_id: str, session_key: str) -> ProposedAction:
        action = self._expire_if_needed(self.get(owner_id, action_id, session_key=session_key))
        if action.status not in {"proposed", "approved"}:
            raise ValueError(f"Only proposed or approved actions can be cancelled; this action is {action.status}")
        now = self._iso(self._now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proposed_actions
                SET status = 'cancelled', updated_at = ?, resolved_at = ?, failure_category = 'cancelled'
                WHERE id = ? AND owner_id = ? AND session_key = ? AND status IN ('proposed', 'approved')
                """,
                (now, now, action_id, owner_id, session_key),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action state changed before cancellation")
        return self.get(owner_id, action_id, session_key=session_key)

    def record_execution_result(
        self,
        owner_id: str,
        action_id: str,
        session_key: str,
        *,
        success: bool,
        result_summary: str | None = None,
        result_ref: str | None = None,
        failure_category: str | None = None,
    ) -> ProposedAction:
        now = self._iso(self._now())
        next_status = "executed" if success else "failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE proposed_actions
                SET status = ?, updated_at = ?, executed_at = ?,
                    result_summary = ?, result_ref = ?, failure_category = ?
                WHERE id = ? AND owner_id = ? AND session_key = ? AND status = 'approved'
                """,
                (
                    next_status,
                    now,
                    now,
                    result_summary[:600] if result_summary else None,
                    result_ref,
                    failure_category,
                    action_id,
                    owner_id,
                    session_key,
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("Action state changed before execution result could be recorded")
        return self.get(owner_id, action_id, session_key=session_key)

    def mark_execution(
        self,
        owner_id: str,
        action_id: str,
        session_key: str,
        *,
        success: bool,
        result_summary: str | None = None,
    ) -> ProposedAction:
        """Backward-compatible alias for record_execution_result (used by existing tests)."""
        action = self._expire_if_needed(self.get(owner_id, action_id, session_key=session_key))
        if action.status != "approved":
            raise ValueError(f"Only an approved action can execute; this action is {action.status}")
        return self.record_execution_result(
            owner_id,
            action_id,
            session_key,
            success=success,
            result_summary=result_summary,
        )
