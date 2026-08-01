"""Durable task store for Pico bounded task lifecycle."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from picobot.missions.store import MissionStore
from picobot.runs.store import RunStore


@dataclass(frozen=True)
class TaskRecord:
    """A durable task record representing bounded work under a mission."""

    id: str
    owner_id: str
    session_key: str
    mission_id: str
    parent_run_id: str | None
    retry_of_task_id: str | None
    title: str
    objective: str
    capability_profile: str
    state: str  # draft, queued, running, waiting_for_approval, completed, failed, cancelled
    max_turns: int
    max_elapsed_sec: int
    max_attempts: int
    depth: int
    current_run_id: str | None
    result_summary: str | None
    result_ref: str | None
    failure_category: str | None
    attempts_count: int
    created_at: str
    updated_at: str
    started_at: str | None
    ended_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return safe public projection; NEVER include prompts, secrets, or reasoning."""
        return asdict(self)


class TaskStore:
    """Owner- and session-scoped task store with atomic execution claiming."""

    _STATES = {
        "draft",
        "queued",
        "running",
        "waiting_for_approval",
        "completed",
        "failed",
        "cancelled",
    }
    _TERMINAL_STATES = {"completed", "failed", "cancelled"}
    _ACTIVE_STATES = {"queued", "running", "waiting_for_approval"}
    _TRANSITIONS = {
        "draft": {"queued", "cancelled"},
        "queued": {"running", "cancelled", "failed"},
        "running": {"waiting_for_approval", "completed", "failed", "cancelled"},
        "waiting_for_approval": {"running", "completed", "failed", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }
    _MAX_IDENTIFIER_LENGTH = 320
    _MAX_TITLE_LENGTH = 160
    _MAX_OBJECTIVE_LENGTH = 2000
    _MAX_SUMMARY_LENGTH = 600
    _MAX_REF_LENGTH = 400
    _MAX_FAILURE_CATEGORY_LENGTH = 100
    _MAX_LIST_LIMIT = 100

    def __init__(self, workspace: Path):
        self.workspace = workspace
        root = workspace / "tasks"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-tasks.db"
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

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    parent_run_id TEXT,
                    retry_of_task_id TEXT,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    capability_profile TEXT NOT NULL,
                    state TEXT NOT NULL,
                    max_turns INTEGER NOT NULL,
                    max_elapsed_sec INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    depth INTEGER NOT NULL,
                    current_run_id TEXT,
                    result_summary TEXT,
                    result_ref TEXT,
                    failure_category TEXT,
                    attempts_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_owner_session_idx
                    ON tasks(owner_id, session_key, updated_at DESC);
                CREATE INDEX IF NOT EXISTS tasks_mission_idx
                    ON tasks(owner_id, mission_id);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _required_identifier(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Task {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER_LENGTH:
            raise ValueError(f"Task {label} is limited to {cls._MAX_IDENTIFIER_LENGTH} characters")
        return clean

    @classmethod
    def _bounded_text(
        cls, value: object, label: str, limit: int, *, required: bool
    ) -> str | None:
        if value is None:
            if required:
                raise ValueError(f"Task {label} is required")
            return None
        if not isinstance(value, str):
            raise ValueError(f"Task {label} must be a string")
        clean = " ".join(value.split())
        if not clean:
            if required:
                raise ValueError(f"Task {label} is required")
            return None
        if len(clean) > limit:
            raise ValueError(f"Task {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _task(cls, row: sqlite3.Row) -> TaskRecord:
        d = dict(row)
        return TaskRecord(**d)

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        mission_id: str,
        title: str,
        objective: str,
        capability_profile: str,
        parent_run_id: str | None = None,
        max_turns: int = 10,
        max_elapsed_sec: int = 600,
        max_attempts: int = 3,
        depth: int = 0,
    ) -> TaskRecord:
        """Create a new TaskRecord in 'draft' state."""
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        mission_id = self._required_identifier(mission_id, "mission")
        capability_profile = self._required_identifier(capability_profile, "capability profile")
        title = self._bounded_text(title, "title", self._MAX_TITLE_LENGTH, required=True)
        objective = self._bounded_text(
            objective, "objective", self._MAX_OBJECTIVE_LENGTH, required=True
        )
        parent_run_id = self._bounded_text(
            parent_run_id, "parent run", self._MAX_IDENTIFIER_LENGTH, required=False
        )

        if depth != 0:
            raise ValueError("Task depth must be 0 in this slice")

        if not (1 <= max_turns <= 40):
            raise ValueError("Task max_turns must be between 1 and 40")
        if not (1 <= max_elapsed_sec <= 3600):
            raise ValueError("Task max_elapsed_sec must be between 1 and 3600")
        if not (1 <= max_attempts <= 10):
            raise ValueError("Task max_attempts must be between 1 and 10")

        now = self._now()
        task = TaskRecord(
            id=f"task_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            session_key=session_key,
            mission_id=mission_id,
            parent_run_id=parent_run_id,
            retry_of_task_id=None,
            title=title,
            objective=objective,
            capability_profile=capability_profile,
            state="draft",
            max_turns=max_turns,
            max_elapsed_sec=max_elapsed_sec,
            max_attempts=max_attempts,
            depth=0,
            current_run_id=None,
            result_summary=None,
            result_ref=None,
            failure_category=None,
            attempts_count=0,
            created_at=now,
            updated_at=now,
            started_at=None,
            ended_at=None,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, session_key, mission_id, parent_run_id, retry_of_task_id,
                    title, objective, capability_profile, state, max_turns, max_elapsed_sec,
                    max_attempts, depth, current_run_id, result_summary, result_ref,
                    failure_category, attempts_count, created_at, updated_at, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.owner_id,
                    task.session_key,
                    task.mission_id,
                    task.parent_run_id,
                    task.retry_of_task_id,
                    task.title,
                    task.objective,
                    task.capability_profile,
                    task.state,
                    task.max_turns,
                    task.max_elapsed_sec,
                    task.max_attempts,
                    task.depth,
                    task.current_run_id,
                    task.result_summary,
                    task.result_ref,
                    task.failure_category,
                    task.attempts_count,
                    task.created_at,
                    task.updated_at,
                    task.started_at,
                    task.ended_at,
                ),
            )
        return task

    def get(self, owner_id: str, task_id: str) -> TaskRecord:
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Task was not found for this user")
        return self._task(row)

    def list(
        self,
        owner_id: str,
        *,
        session_key: str | None = None,
        mission_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        owner_id = self._required_identifier(owner_id, "owner")
        query = "SELECT * FROM tasks WHERE owner_id = ?"
        params: list[object] = [owner_id]
        if session_key is not None:
            query += " AND session_key = ?"
            params.append(self._required_identifier(session_key, "session"))
        if mission_id is not None:
            query += " AND mission_id = ?"
            params.append(self._required_identifier(mission_id, "mission"))
        if state is not None:
            if state not in self._STATES:
                raise ValueError(f"Invalid task state filter: {state}")
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, self._MAX_LIST_LIMIT)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._task(row) for row in rows]

    def get_active(self, owner_id: str, session_key: str) -> TaskRecord | None:
        """Return the active task for one session, if any."""
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE owner_id = ? AND session_key = ?
                  AND state IN ('queued', 'running', 'waiting_for_approval')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (owner_id, session_key),
            ).fetchone()
        return self._task(row) if row else None

    def claim_for_run(
        self,
        owner_id: str,
        task_id: str,
        session_key: str,
        current_session_profile: str,
        active_mission: Any,
    ) -> TaskRecord:
        """Atomic execution claim.

        Enforces:
        - Owner, session match
        - Active mission present, matches task.mission_id, state='active'
        - Capability profile matches task.capability_profile and matches current_session_profile
        - Depth is 0
        - Attempts count < max_attempts
        - Single active task per session
        - Legal state (draft or queued)
        """
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        session_key = self._required_identifier(session_key, "session")
        current_session_profile = self._required_identifier(
            current_session_profile, "capability profile"
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ? AND session_key = ?",
                (task_id, owner_id, session_key),
            ).fetchone()

            if row is None:
                raise KeyError(f"Task '{task_id}' not found for owner/session")

            task = self._task(row)

            # Active mission check
            if not active_mission:
                raise ValueError("No active mission attached to session")
            m_id = getattr(active_mission, "id", None) or (
                active_mission.get("id") if isinstance(active_mission, dict) else None
            )
            m_state = getattr(active_mission, "state", None) or (
                active_mission.get("state") if isinstance(active_mission, dict) else None
            )
            m_skey = getattr(active_mission, "session_key", None) or (
                active_mission.get("session_key") if isinstance(active_mission, dict) else None
            )
            if m_id != task.mission_id or m_state != "active" or m_skey != session_key:
                raise ValueError("Active mission mismatch or mission is no longer active")

            # Profile restriction re-check
            if task.capability_profile != current_session_profile:
                raise ValueError("Task profile does not match session profile")

            # Depth check
            if task.depth != 0:
                raise ValueError("Task depth must be 0")

            # Attempt budget
            if task.attempts_count >= task.max_attempts:
                raise ValueError(
                    f"Task attempt limit reached ({task.attempts_count}/{task.max_attempts})"
                )

            # Legal state check
            if task.state != "draft":
                raise ValueError(f"Task in state '{task.state}' cannot be claimed for run")

            # One active task per session check
            active_row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE owner_id = ? AND session_key = ?
                  AND state IN ('queued', 'running', 'waiting_for_approval')
                  AND id != ?
                """,
                (owner_id, session_key, task_id),
            ).fetchone()
            if active_row is not None:
                raise ValueError("Only one active task is allowed per session")

            now = self._now()
            new_attempts = task.attempts_count + 1
            connection.execute(
                """
                UPDATE tasks
                SET state = 'queued', attempts_count = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (new_attempts, now, task_id, owner_id),
            )
            updated_row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated_row)

    def mark_running(
        self, owner_id: str, task_id: str, run_id: str, session_key: str
    ) -> TaskRecord:
        """Transition task state to 'running' when AgentLoop starts executing run."""
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        session_key = self._required_identifier(session_key, "session")
        run_id = self._required_identifier(run_id, "run_id")

        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ? AND session_key = ?",
                (task_id, owner_id, session_key),
            ).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            if row["state"] != "queued":
                raise ValueError(f"Task in state '{row['state']}' cannot transition to running")

            from picobot.runs.store import RunStore
            run = RunStore(self.workspace).get(owner_id, run_id)
            if (
                run.owner_id != row["owner_id"]
                or run.session_key != row["session_key"]
                or run.mission_id != row["mission_id"]
                or run.capability_profile != row["capability_profile"]
                or getattr(run, "task_id", None) != task_id
            ):
                raise ValueError("RunRecord does not match TaskRecord attributes")

            started_at = row["started_at"] or now
            connection.execute(
                """
                UPDATE tasks
                SET state = 'running', current_run_id = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_id, started_at, now, task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated)

    def mark_waiting_for_approval(
        self, owner_id: str, task_id: str, session_key: str
    ) -> TaskRecord:
        """Transition task state to 'waiting_for_approval' when a action is proposed."""
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        session_key = self._required_identifier(session_key, "session")

        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ? AND session_key = ?",
                (task_id, owner_id, session_key),
            ).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            if row["state"] != "running":
                raise ValueError(
                    f"Task in state '{row['state']}' cannot transition to waiting_for_approval"
                )
            connection.execute(
                """
                UPDATE tasks
                SET state = 'waiting_for_approval', updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated)

    def complete(
        self,
        owner_id: str,
        task_id: str,
        *,
        result_summary: str | None = None,
        result_ref: str | None = None,
        session_key: str | None = None,
    ) -> TaskRecord:
        """Transition task to 'completed'."""
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        clean_summary = self._bounded_text(
            result_summary, "result summary", self._MAX_SUMMARY_LENGTH, required=False
        )
        clean_ref = self._bounded_text(
            result_ref, "result ref", self._MAX_REF_LENGTH, required=False
        )

        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            if session_key and row["session_key"] != session_key:
                raise ValueError("Session mismatch for task completion")
            if row["state"] not in {"running", "waiting_for_approval"}:
                raise ValueError(f"Task in state '{row['state']}' cannot be completed")
            connection.execute(
                """
                UPDATE tasks
                SET state = 'completed', result_summary = ?, result_ref = ?, ended_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_summary, clean_ref, now, now, task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated)

    def fail(
        self,
        owner_id: str,
        task_id: str,
        *,
        failure_category: str | None = None,
        result_summary: str | None = None,
        session_key: str | None = None,
    ) -> TaskRecord:
        """Transition task to 'failed'."""
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        clean_cat = self._bounded_text(
            failure_category,
            "failure category",
            self._MAX_FAILURE_CATEGORY_LENGTH,
            required=False,
        ) or "execution_error"
        clean_summary = self._bounded_text(
            result_summary, "result summary", self._MAX_SUMMARY_LENGTH, required=False
        )

        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            if session_key and row["session_key"] != session_key:
                raise ValueError("Session mismatch for task failure")
            if row["state"] not in {"queued", "running", "waiting_for_approval"}:
                raise ValueError(f"Task in state '{row['state']}' cannot be marked failed")
            connection.execute(
                """
                UPDATE tasks
                SET state = 'failed', failure_category = ?, result_summary = ?, ended_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_cat, clean_summary, now, now, task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated)

    def cancel(
        self,
        owner_id: str,
        task_id: str,
        session_key: str,
        run_store: RunStore | None = None,
    ) -> TaskRecord:
        """Cancel task and safely cancel its active run if any."""
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        session_key = self._required_identifier(session_key, "session")

        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ? AND session_key = ?",
                (task_id, owner_id, session_key),
            ).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found for owner/session")

            task = self._task(row)
            if task.state in self._TERMINAL_STATES:
                raise ValueError(f"Task is already in terminal state '{task.state}'")

            if task.current_run_id and run_store:
                try:
                    run_store.cancel(owner_id, task.current_run_id)
                except Exception:
                    pass

            connection.execute(
                """
                UPDATE tasks
                SET state = 'cancelled', failure_category = 'cancelled', ended_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, task_id),
            )
            updated = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task(updated)

    def retry_task(
        self,
        owner_id: str,
        task_id: str,
        session_key: str,
        current_session_profile: str,
        active_mission: Any,
    ) -> TaskRecord:
        """Create a NEW TaskRecord attempt linked by retry_of_task_id.

        Terminal task history is preserved and never resurrected.
        Enforces attempt budget limit.
        """
        owner_id = self._required_identifier(owner_id, "owner")
        task_id = self._required_identifier(task_id, "task")
        session_key = self._required_identifier(session_key, "session")
        current_session_profile = self._required_identifier(
            current_session_profile, "capability profile"
        )

        old_task = self.get(owner_id, task_id)
        if current_session_profile != old_task.capability_profile:
            raise ValueError("Retry session profile does not match original task profile")

        if old_task.session_key != session_key:
            raise ValueError("Session mismatch for task retry")
        if old_task.state not in self._TERMINAL_STATES:
            raise ValueError(f"Only terminal tasks can be retried; task state is '{old_task.state}'")

        if old_task.attempts_count >= old_task.max_attempts:
            raise ValueError(
                f"Maximum attempts limit ({old_task.max_attempts}) reached for task"
            )

        # Active mission check
        if not active_mission:
            raise ValueError("No active mission attached to session")
        m_id = getattr(active_mission, "id", None) or (
            active_mission.get("id") if isinstance(active_mission, dict) else None
        )
        m_state = getattr(active_mission, "state", None) or (
            active_mission.get("state") if isinstance(active_mission, dict) else None
        )
        if m_id != old_task.mission_id or m_state != "active":
            raise ValueError("Active mission mismatch or mission is no longer active")

        now = self._now()
        new_task = TaskRecord(
            id=f"task_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            session_key=session_key,
            mission_id=old_task.mission_id,
            parent_run_id=old_task.parent_run_id,
            retry_of_task_id=old_task.id,
            title=old_task.title,
            objective=old_task.objective,
            capability_profile=old_task.capability_profile,
            state="draft",
            max_turns=old_task.max_turns,
            max_elapsed_sec=old_task.max_elapsed_sec,
            max_attempts=old_task.max_attempts,
            depth=0,
            current_run_id=None,
            result_summary=None,
            result_ref=None,
            failure_category=None,
            attempts_count=old_task.attempts_count,
            created_at=now,
            updated_at=now,
            started_at=None,
            ended_at=None,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_retry = connection.execute(
                "SELECT * FROM tasks WHERE retry_of_task_id = ? AND owner_id = ?",
                (old_task.id, owner_id),
            ).fetchone()
            if existing_retry is not None:
                raise ValueError("Task has already been retried")

            active_row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE owner_id = ? AND session_key = ?
                  AND state IN ('queued', 'running', 'waiting_for_approval')
                """,
                (owner_id, session_key),
            ).fetchone()
            if active_row is not None:
                raise ValueError("Only one active task is allowed per session")
            connection.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, session_key, mission_id, parent_run_id, retry_of_task_id,
                    title, objective, capability_profile, state, max_turns, max_elapsed_sec,
                    max_attempts, depth, current_run_id, result_summary, result_ref,
                    failure_category, attempts_count, created_at, updated_at, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_task.id,
                    new_task.owner_id,
                    new_task.session_key,
                    new_task.mission_id,
                    new_task.parent_run_id,
                    new_task.retry_of_task_id,
                    new_task.title,
                    new_task.objective,
                    new_task.capability_profile,
                    new_task.state,
                    new_task.max_turns,
                    new_task.max_elapsed_sec,
                    new_task.max_attempts,
                    new_task.depth,
                    new_task.current_run_id,
                    new_task.result_summary,
                    new_task.result_ref,
                    new_task.failure_category,
                    new_task.attempts_count,
                    new_task.created_at,
                    new_task.updated_at,
                    new_task.started_at,
                    new_task.ended_at,
                ),
            )
        return new_task
