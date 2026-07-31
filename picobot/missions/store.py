"""A local-first mission harness persistence layer.

Missions are deliberately small, explicit records.  This store holds mission
state and bounded human-readable checkpoints only; agent transcripts, tool
arguments, credentials, and arbitrary metadata belong elsewhere.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BlueprintStep:
    """A single ordered step in a mission workflow blueprint."""

    step_id: str
    title: str
    success_criterion: str | None
    state: str  # pending, active, blocked, completed, skipped
    blocked_reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MissionBlueprint:
    """A durable, reviewable workflow blueprint for a mission."""

    id: str
    mission_id: str
    owner_id: str
    version: int
    state: str  # draft, approved, superseded
    steps: list[BlueprintStep]
    created_at: str
    updated_at: str
    approved_at: str | None = None
    superseded_at: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["steps"] = [step.to_dict() if hasattr(step, "to_dict") else step for step in self.steps]
        return data


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
    # Internal event provenance used only to prevent duplicate run receipts.
    # It deliberately stays out of the public event projection; mission detail
    # already has a dedicated safe run receipt list.
    source_run_id: str | None = None

    def to_dict(self) -> dict:
        """Return the safe event record without tool arguments or transcripts."""
        data = asdict(self)
        data.pop("source_run_id", None)
        return data


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
                    source_run_id TEXT,
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS mission_events_mission_owner_created_idx
                    ON mission_events(mission_id, owner_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS mission_blueprints (
                    id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    superseded_at TEXT,
                    FOREIGN KEY (mission_id) REFERENCES missions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS mission_blueprints_mission_version_idx
                    ON mission_blueprints(mission_id, version DESC);
                CREATE INDEX IF NOT EXISTS mission_blueprints_mission_state_idx
                    ON mission_blueprints(mission_id, state);
                """
            )
            # Existing Pico workspaces predate run-linked events.  The
            # additive column keeps their event history valid while giving new
            # terminal receipts an exact, durable deduplication key.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(mission_events)").fetchall()
            }
            if "source_run_id" not in columns:
                connection.execute("ALTER TABLE mission_events ADD COLUMN source_run_id TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS mission_events_owner_run_idx
                ON mission_events(mission_id, owner_id, source_run_id)
                WHERE source_run_id IS NOT NULL
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
            INSERT INTO mission_events(id, mission_id, owner_id, event_type, summary, created_at, source_run_id)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
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

    _BLUEPRINT_STATES = {"draft", "approved", "superseded"}
    _STEP_STATES = {"pending", "active", "blocked", "completed", "skipped"}
    _MAX_BLUEPRINT_STEPS = 20
    _MAX_STEP_ID_LENGTH = 64
    _MAX_STEP_TITLE_LENGTH = 160
    _MAX_STEP_CRITERION_LENGTH = 2_000
    _MAX_STEP_BLOCKED_REASON_LENGTH = 2_000

    @classmethod
    def _blueprint(cls, row: sqlite3.Row) -> MissionBlueprint:
        data = dict(row)
        steps_json = data.pop("steps_json", "[]")
        raw_steps = json.loads(steps_json) if isinstance(steps_json, str) else []
        steps = [
            BlueprintStep(**s) if isinstance(s, dict) else s for s in raw_steps
        ]
        return MissionBlueprint(steps=steps, **data)

    @classmethod
    def _validate_and_normalize_steps(cls, raw_steps: object) -> list[BlueprintStep]:
        if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= cls._MAX_BLUEPRINT_STEPS):
            raise ValueError(f"Blueprint must contain between 1 and {cls._MAX_BLUEPRINT_STEPS} steps")

        seen_ids: set[str] = set()
        active_or_blocked_count = 0
        normalized: list[BlueprintStep] = []

        for idx, item in enumerate(raw_steps):
            if isinstance(item, BlueprintStep):
                step_id = item.step_id
                title = item.title
                criterion = item.success_criterion
                state = item.state
                blocked_reason = item.blocked_reason
            elif isinstance(item, dict):
                step_id = item.get("step_id") or f"step_{idx + 1}"
                title = item.get("title")
                criterion = item.get("success_criterion")
                state = item.get("state") or "pending"
                blocked_reason = item.get("blocked_reason")
            else:
                raise ValueError("Step must be a dictionary or BlueprintStep instance")

            clean_id = cls._bounded_text(step_id, "step_id", cls._MAX_STEP_ID_LENGTH, required=True)
            if clean_id in seen_ids:
                raise ValueError(f"Duplicate step ID: {clean_id}")
            seen_ids.add(clean_id)

            clean_title = cls._bounded_text(title, "step title", cls._MAX_STEP_TITLE_LENGTH, required=True)
            clean_criterion = cls._bounded_text(
                criterion, "step success criterion", cls._MAX_STEP_CRITERION_LENGTH, required=False
            )

            if state not in cls._STEP_STATES:
                raise ValueError(f"Invalid step state: {state}")

            clean_blocked_reason = None
            if state == "blocked":
                clean_blocked_reason = cls._bounded_text(
                    blocked_reason, "step blocked reason", cls._MAX_STEP_BLOCKED_REASON_LENGTH, required=True
                )

            if state in {"active", "blocked"}:
                active_or_blocked_count += 1

            normalized.append(
                BlueprintStep(
                    step_id=clean_id,
                    title=clean_title,
                    success_criterion=clean_criterion,
                    state=state,
                    blocked_reason=clean_blocked_reason,
                )
            )

        if active_or_blocked_count > 1:
            raise ValueError("At most one step can be active or blocked across the blueprint")

        return normalized

    def save_draft_blueprint(
        self, owner_id: str, mission_id: str, raw_steps: list[dict | BlueprintStep]
    ) -> MissionBlueprint:
        mission = self.get(owner_id, mission_id)
        if mission.state in self._TERMINAL_STATES:
            raise ValueError("Cannot modify blueprint for a completed or cancelled mission")
        if mission.state not in {"draft", "active"}:
            raise ValueError("Blueprint draft can only be created/edited for draft or active missions")

        steps = self._validate_and_normalize_steps(raw_steps)
        now = self._now()
        steps_json = json.dumps([s.to_dict() for s in steps])

        with self._connect() as connection:
            existing_draft = connection.execute(
                """
                SELECT * FROM mission_blueprints
                WHERE mission_id = ? AND owner_id = ? AND state = 'draft'
                ORDER BY version DESC LIMIT 1
                """,
                (mission.id, mission.owner_id),
            ).fetchone()

            if existing_draft is not None:
                blueprint_id = existing_draft["id"]
                connection.execute(
                    """
                    UPDATE mission_blueprints
                    SET steps_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (steps_json, now, blueprint_id),
                )
                self._record_event(
                    connection,
                    mission,
                    "blueprint_updated",
                    f"Draft blueprint v{existing_draft['version']} updated.",
                )
            else:
                highest_version_row = connection.execute(
                    """
                    SELECT MAX(version) as max_v FROM mission_blueprints
                    WHERE mission_id = ? AND owner_id = ?
                    """,
                    (mission.id, mission.owner_id),
                ).fetchone()
                new_version = (highest_version_row["max_v"] or 0) + 1 if highest_version_row else 1
                blueprint_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO mission_blueprints(
                        id, mission_id, owner_id, version, state, steps_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)
                    """,
                    (blueprint_id, mission.id, mission.owner_id, new_version, steps_json, now, now),
                )
                self._record_event(
                    connection,
                    mission,
                    "blueprint_created",
                    f"Draft blueprint v{new_version} created.",
                )

            row = connection.execute(
                "SELECT * FROM mission_blueprints WHERE id = ?", (blueprint_id,)
            ).fetchone()
            return self._blueprint(row)

    def approve_blueprint(
        self, owner_id: str, mission_id: str, blueprint_id: str | None = None
    ) -> MissionBlueprint:
        mission = self.get(owner_id, mission_id)
        if mission.state in self._TERMINAL_STATES:
            raise ValueError("Cannot approve blueprint for a completed or cancelled mission")

        now = self._now()
        with self._connect() as connection:
            if blueprint_id:
                draft_row = connection.execute(
                    "SELECT * FROM mission_blueprints WHERE id = ? AND mission_id = ? AND owner_id = ?",
                    (blueprint_id, mission.id, mission.owner_id),
                ).fetchone()
            else:
                draft_row = connection.execute(
                    """
                    SELECT * FROM mission_blueprints
                    WHERE mission_id = ? AND owner_id = ? AND state = 'draft'
                    ORDER BY version DESC LIMIT 1
                    """,
                    (mission.id, mission.owner_id),
                ).fetchone()

            if draft_row is None or draft_row["state"] != "draft":
                raise ValueError("No draft blueprint found to approve")

            draft_bp = self._blueprint(draft_row)
            steps = list(draft_bp.steps)

            # If no step is active/blocked/completed/skipped (all pending), make first step active
            has_active_or_done = any(s.state != "pending" for s in steps)
            if not has_active_or_done and steps:
                steps[0] = BlueprintStep(
                    step_id=steps[0].step_id,
                    title=steps[0].title,
                    success_criterion=steps[0].success_criterion,
                    state="active",
                    blocked_reason=None,
                )

            steps_json = json.dumps([s.to_dict() for s in steps])

            # Supersede any currently approved blueprint for this mission
            connection.execute(
                """
                UPDATE mission_blueprints
                SET state = 'superseded', superseded_at = ?, updated_at = ?
                WHERE mission_id = ? AND owner_id = ? AND state = 'approved'
                """,
                (now, now, mission.id, mission.owner_id),
            )

            # Approve this draft blueprint
            connection.execute(
                """
                UPDATE mission_blueprints
                SET state = 'approved', approved_at = ?, updated_at = ?, steps_json = ?
                WHERE id = ?
                """,
                (now, now, steps_json, draft_bp.id),
            )

            active_step = next((s for s in steps if s.state == "active"), None)
            blocked_step = next((s for s in steps if s.state == "blocked"), None)
            if active_step:
                new_current_step = active_step.title
            elif blocked_step:
                new_current_step = f"[Blocked] {blocked_step.title}"
            elif all(s.state in {"completed", "skipped"} for s in steps):
                new_current_step = "All blueprint steps completed"
            else:
                new_current_step = mission.current_step

            connection.execute(
                "UPDATE missions SET current_step = ?, updated_at = ? WHERE id = ?",
                (new_current_step, now, mission.id),
            )

            self._record_event(
                connection,
                mission,
                "blueprint_approved",
                f"Blueprint v{draft_bp.version} approved with {len(steps)} steps.",
            )

            approved_row = connection.execute(
                "SELECT * FROM mission_blueprints WHERE id = ?", (draft_bp.id,)
            ).fetchone()
            return self._blueprint(approved_row)

    def transition_blueprint_step(
        self,
        owner_id: str,
        mission_id: str,
        step_id: str,
        target_state: str,
        *,
        blocked_reason: str | None = None,
    ) -> MissionBlueprint:
        mission = self.get(owner_id, mission_id)
        if mission.state in self._TERMINAL_STATES:
            raise ValueError("Cannot transition step for a completed or cancelled mission")

        if target_state not in self._STEP_STATES:
            raise ValueError(f"Invalid step state: {target_state}")

        approved_bp = self.get_approved_blueprint(owner_id, mission_id)
        if approved_bp is None:
            raise ValueError("No approved blueprint found for mission")

        step_id = self._required_identifier(step_id, "step_id")
        steps = list(approved_bp.steps)
        target_idx = next((i for i, s in enumerate(steps) if s.step_id == step_id), None)
        if target_idx is None:
            raise ValueError(f"Step '{step_id}' not found in approved blueprint")

        clean_blocked_reason = None
        if target_state == "blocked":
            clean_blocked_reason = self._bounded_text(
                blocked_reason, "step blocked reason", self._MAX_STEP_BLOCKED_REASON_LENGTH, required=True
            )

        old_step = steps[target_idx]
        steps[target_idx] = BlueprintStep(
            step_id=old_step.step_id,
            title=old_step.title,
            success_criterion=old_step.success_criterion,
            state=target_state,
            blocked_reason=clean_blocked_reason,
        )

        if target_state in {"active", "blocked"}:
            for i, s in enumerate(steps):
                if i != target_idx and s.state in {"active", "blocked"}:
                    steps[i] = BlueprintStep(
                        step_id=s.step_id,
                        title=s.title,
                        success_criterion=s.success_criterion,
                        state="pending",
                        blocked_reason=None,
                    )
        elif target_state in {"completed", "skipped"} and old_step.state == "active":
            next_pending_idx = next((i for i in range(target_idx + 1, len(steps)) if steps[i].state == "pending"), None)
            if next_pending_idx is not None:
                next_step = steps[next_pending_idx]
                steps[next_pending_idx] = BlueprintStep(
                    step_id=next_step.step_id,
                    title=next_step.title,
                    success_criterion=next_step.success_criterion,
                    state="active",
                    blocked_reason=None,
                )

        now = self._now()
        steps_json = json.dumps([s.to_dict() for s in steps])

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE mission_blueprints
                SET steps_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (steps_json, now, approved_bp.id),
            )

            active_step = next((s for s in steps if s.state == "active"), None)
            blocked_step = next((s for s in steps if s.state == "blocked"), None)
            if active_step:
                new_current_step = active_step.title
            elif blocked_step:
                new_current_step = f"[Blocked] {blocked_step.title}"
            elif all(s.state in {"completed", "skipped"} for s in steps):
                new_current_step = "All blueprint steps completed"
            else:
                new_current_step = mission.current_step

            connection.execute(
                "UPDATE missions SET current_step = ?, updated_at = ? WHERE id = ?",
                (new_current_step, now, mission.id),
            )

            self._record_event(
                connection,
                mission,
                "blueprint_step_transition",
                f"Blueprint step '{step_id}' transitioned to '{target_state}'.",
            )

            row = connection.execute(
                "SELECT * FROM mission_blueprints WHERE id = ?", (approved_bp.id,)
            ).fetchone()
            return self._blueprint(row)

    def get_approved_blueprint(self, owner_id: str, mission_id: str) -> MissionBlueprint | None:
        mission = self.get(owner_id, mission_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM mission_blueprints
                WHERE mission_id = ? AND owner_id = ? AND state = 'approved'
                ORDER BY version DESC LIMIT 1
                """,
                (mission.id, mission.owner_id),
            ).fetchone()
        return self._blueprint(row) if row else None

    def get_latest_blueprint(self, owner_id: str, mission_id: str) -> MissionBlueprint | None:
        mission = self.get(owner_id, mission_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM mission_blueprints
                WHERE mission_id = ? AND owner_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (mission.id, mission.owner_id),
            ).fetchone()
        return self._blueprint(row) if row else None

    _RUN_TERMINAL_STATES = {"completed", "failed", "cancelled"}

    def record_run_terminal_event(
        self,
        owner_id: str,
        mission_id: str,
        run_id: str,
        state: str,
        error_summary: str | None = None,
    ) -> MissionEvent | None:
        """Record a bounded, deduplicated terminal run event for a mission."""
        if state not in self._RUN_TERMINAL_STATES:
            return None
        mission = self.get(owner_id, mission_id)
        run_id = self._required_identifier(run_id, "run")

        short_run_id = run_id[:8]
        summary = f"Run {short_run_id} {state}."
        with self._connect() as connection:
            event_id = str(uuid.uuid4())
            now = self._now()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO mission_events(
                    id, mission_id, owner_id, event_type, summary, created_at, source_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, mission.id, mission.owner_id, f"run_{state}", summary, now, run_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM mission_events WHERE id = ?", (event_id,)
            ).fetchone()
            return self._event(row)
