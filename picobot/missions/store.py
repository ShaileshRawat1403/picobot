"""A local-first mission harness persistence layer.

Missions are deliberately small, explicit records.  This store holds mission
state and bounded human-readable checkpoints only; agent transcripts, tool
arguments, credentials, and arbitrary metadata belong elsewhere.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Mission:
    """A durable, owner-scoped mission linked to one Pico session."""

    id: str
    owner_id: str
    session_key: str
    title: str
    objective: str
    state: str
    current_step: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    cancelled_at: str | None

    def to_dict(self) -> dict:
        """Return the bounded mission record without any hidden state."""
        return asdict(self)


@dataclass(frozen=True)
class MissionCheckpoint:
    """A bounded note about a mission's progress or handoff."""

    id: str
    mission_id: str
    owner_id: str
    kind: str
    summary: str
    created_at: str

    def to_dict(self) -> dict:
        """Return the safe checkpoint record."""
        return asdict(self)


@dataclass(frozen=True)
class MissionEvent:
    """A minimal, user-visible record of a mission lifecycle change."""

    id: str
    mission_id: str
    owner_id: str
    event_type: str
    summary: str
    created_at: str

    def to_dict(self) -> dict:
        """Return the safe event record without tool arguments or transcripts."""
        return asdict(self)


class MissionStore:
    """Persist owner-scoped mission state in the local Pico workspace."""

    _STATES = {"draft", "active", "blocked", "completed", "cancelled"}
    _CHECKPOINT_KINDS = {"note", "decision", "evidence", "handoff"}
    _TRANSITIONS = {
        "draft": {"active", "cancelled"},
        "active": {"blocked", "completed", "cancelled"},
        "blocked": {"active", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    _MAX_TITLE_LENGTH = 160
    _MAX_OBJECTIVE_LENGTH = 8_000
    _MAX_CURRENT_STEP_LENGTH = 2_000
    _MAX_BLOCKED_REASON_LENGTH = 2_000
    _MAX_CHECKPOINT_SUMMARY_LENGTH = 2_000
    _MAX_LIST_LIMIT = 100
    _TERMINAL_STATES = {"completed", "cancelled"}

    def __init__(self, workspace: Path):
        root = workspace / "missions"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-missions.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_step TEXT,
                    blocked_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    cancelled_at TEXT
                );
                CREATE INDEX IF NOT EXISTS missions_owner_updated_idx
                    ON missions(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS missions_owner_state_updated_idx
                    ON missions(owner_id, state, updated_at DESC);

                CREATE TABLE IF NOT EXISTS mission_checkpoints (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS mission_checkpoints_mission_owner_created_idx
                    ON mission_checkpoints(mission_id, owner_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS mission_events (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS mission_events_mission_owner_created_idx
                    ON mission_events(mission_id, owner_id, created_at DESC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _required_identifier(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Mission {label} is required")
        return value.strip()

    @staticmethod
    def _bounded_text(value: object, label: str, limit: int, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Mission {label} is required" if required else f"Mission {label} must be text")
        clean = " ".join(value.split())
        if required and not clean:
            raise ValueError(f"Mission {label} is required")
        if not required and not clean:
            return None
        if len(clean) > limit:
            raise ValueError(f"Mission {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _validate_limit(cls, limit: object) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= cls._MAX_LIST_LIMIT:
            raise ValueError(f"Mission list limit must be between 1 and {cls._MAX_LIST_LIMIT}")
        return limit

    @classmethod
    def _validate_state(cls, state: object) -> str:
        if state not in cls._STATES:
            raise ValueError(f"Unsupported mission state: {state}")
        return str(state)

    @classmethod
    def _mission(cls, row: sqlite3.Row) -> Mission:
        return Mission(**dict(row))

    @classmethod
    def _checkpoint(cls, row: sqlite3.Row) -> MissionCheckpoint:
        return MissionCheckpoint(**dict(row))

    @classmethod
    def _event(cls, row: sqlite3.Row) -> MissionEvent:
        return MissionEvent(**dict(row))

    @classmethod
    def _record_event(
        cls,
        connection: sqlite3.Connection,
        mission: Mission,
        event_type: str,
        summary: str,
        *,
        created_at: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO mission_events(id, mission_id, owner_id, event_type, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                mission.id,
                mission.owner_id,
                event_type,
                summary,
                created_at or cls._now(),
            ),
        )

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        title: str,
        objective: str,
        current_step: str | None = None,
    ) -> Mission:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        title = self._bounded_text(title, "title", self._MAX_TITLE_LENGTH, required=True)
        objective = self._bounded_text(objective, "objective", self._MAX_OBJECTIVE_LENGTH, required=True)
        current_step = self._bounded_text(
            current_step, "current step", self._MAX_CURRENT_STEP_LENGTH, required=False
        )
        now = self._now()
        mission = Mission(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            title=title,
            objective=objective,
            state="draft",
            current_step=current_step,
            blocked_reason=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
            cancelled_at=None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO missions (
                    id, owner_id, session_key, title, objective, state, current_step,
                    blocked_reason, created_at, updated_at, completed_at, cancelled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission.id,
                    mission.owner_id,
                    mission.session_key,
                    mission.title,
                    mission.objective,
                    mission.state,
                    mission.current_step,
                    mission.blocked_reason,
                    mission.created_at,
                    mission.updated_at,
                    mission.completed_at,
                    mission.cancelled_at,
                ),
            )
            self._record_event(connection, mission, "created", "Mission created as a draft.", created_at=now)
        return mission

    def get(self, owner_id: str, mission_id: str) -> Mission:
        owner_id = self._required_identifier(owner_id, "owner")
        if not isinstance(mission_id, str) or not mission_id:
            raise KeyError("Mission was not found for this owner")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM missions WHERE id = ? AND owner_id = ?", (mission_id, owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Mission was not found for this owner")
        return self._mission(row)

    def list(
        self,
        owner_id: str,
        state: str | None = None,
        limit: int = 100,
        *,
        session_key: str | None = None,
    ) -> list[Mission]:
        owner_id = self._required_identifier(owner_id, "owner")
        limit = self._validate_limit(limit)
        query = "SELECT * FROM missions WHERE owner_id = ?"
        params: list[object] = [owner_id]
        if session_key is not None:
            query += " AND session_key = ?"
            params.append(self._required_identifier(session_key, "session"))
        if state is not None:
            query += " AND state = ?"
            params.append(self._validate_state(state))
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._mission(row) for row in rows]

    def transition(
        self,
        owner_id: str,
        mission_id: str,
        state: str,
        blocked_reason: str | None = None,
    ) -> Mission:
        state = self._validate_state(state)
        mission = self.get(owner_id, mission_id)
        reason = self._bounded_text(
            blocked_reason, "blocked reason", self._MAX_BLOCKED_REASON_LENGTH, required=False
        )
        if state != mission.state and state not in self._TRANSITIONS[mission.state]:
            raise ValueError(f"Mission cannot transition from {mission.state} to {state}")
        if state == "blocked" and reason is None:
            raise ValueError("A blocked reason is required when blocking a mission")
        if state != "blocked" and reason is not None:
            raise ValueError("A blocked reason is only allowed for blocked missions")
        if state == mission.state:
            if state != "blocked" or reason == mission.blocked_reason:
                return mission
            now = self._now()
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE missions SET blocked_reason = ?, updated_at = ?
                    WHERE id = ? AND owner_id = ? AND state = 'blocked'
                    """,
                    (reason, now, mission.id, mission.owner_id),
                )
                self._record_event(connection, mission, "blocker_updated", "Blocker updated.", created_at=now)
            return self.get(mission.owner_id, mission.id)
        now = self._now()
        completed_at = now if state == "completed" else None
        cancelled_at = now if state == "cancelled" else None
        next_reason = reason if state == "blocked" else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE missions
                SET state = ?, blocked_reason = ?, updated_at = ?, completed_at = ?, cancelled_at = ?
                WHERE id = ? AND owner_id = ? AND state = ?
                """,
                (
                    state,
                    next_reason,
                    now,
                    completed_at,
                    cancelled_at,
                    mission.id,
                    mission.owner_id,
                    mission.state,
                ),
            )
            if cursor.rowcount == 1:
                summary = f"State changed from {mission.state} to {state}."
                self._record_event(connection, mission, "state_changed", summary, created_at=now)
        if cursor.rowcount != 1:
            raise ValueError("Mission state changed before the transition could be recorded")
        return self.get(mission.owner_id, mission.id)

    def set_current_step(self, owner_id: str, mission_id: str, current_step: str | None) -> Mission:
        mission = self.get(owner_id, mission_id)
        current_step = self._bounded_text(
            current_step, "current step", self._MAX_CURRENT_STEP_LENGTH, required=False
        )
        if mission.state in self._TERMINAL_STATES:
            raise ValueError("Mission is terminal and cannot be changed")
        if current_step == mission.current_step:
            return mission
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE missions SET current_step = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (current_step, now, mission.id, mission.owner_id),
            )
            self._record_event(connection, mission, "current_step_updated", "Current step updated.", created_at=now)
        return self.get(mission.owner_id, mission.id)

    def add_checkpoint(
        self, owner_id: str, mission_id: str, kind: str, summary: str
    ) -> MissionCheckpoint:
        mission = self.get(owner_id, mission_id)
        if mission.state in self._TERMINAL_STATES:
            raise ValueError("Mission is terminal and cannot be changed")
        if kind not in self._CHECKPOINT_KINDS:
            raise ValueError(f"Unsupported mission checkpoint kind: {kind}")
        summary = self._bounded_text(
            summary, "checkpoint summary", self._MAX_CHECKPOINT_SUMMARY_LENGTH, required=True
        )
        checkpoint = MissionCheckpoint(
            id=str(uuid.uuid4()),
            mission_id=mission.id,
            owner_id=mission.owner_id,
            kind=kind,
            summary=summary,
            created_at=self._now(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mission_checkpoints(id, mission_id, owner_id, kind, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.id,
                    checkpoint.mission_id,
                    checkpoint.owner_id,
                    checkpoint.kind,
                    checkpoint.summary,
                    checkpoint.created_at,
                ),
            )
        return checkpoint

    def checkpoints(self, owner_id: str, mission_id: str, limit: int = 100) -> list[MissionCheckpoint]:
        mission = self.get(owner_id, mission_id)
        limit = self._validate_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM mission_checkpoints
                WHERE mission_id = ? AND owner_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (mission.id, mission.owner_id, limit),
            ).fetchall()
        return [self._checkpoint(row) for row in rows]

    def events(self, owner_id: str, mission_id: str, limit: int = 100) -> list[MissionEvent]:
        """Return the bounded mission lifecycle record for the owning user."""
        mission = self.get(owner_id, mission_id)
        limit = self._validate_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM mission_events
                WHERE mission_id = ? AND owner_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (mission.id, mission.owner_id, limit),
            ).fetchall()
        return [self._event(row) for row in rows]
