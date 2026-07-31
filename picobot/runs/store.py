"""A durable, owner-scoped turn and run ledger.

Every submitted message that executes against the agent loop owns exactly one
``RunRecord``.  The ledger keeps observable execution facts only: effective
provider/model/profile, lifecycle state and timestamps, trusted usage, and
bounded references and counts.  It never stores chat transcripts, tool
arguments, credentials, or hidden reasoning.  A turn receipt is derived only
from those stored facts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RunRecord:
    """A durable execution record for one submitted message turn."""

    id: str
    owner_id: str
    session_key: str
    mission_id: str | None
    provider: str | None
    model: str | None
    capability_profile: str
    policy_revision: str | None
    state: str
    created_at: str
    updated_at: str
    started_at: str | None
    ended_at: str | None
    elapsed_ms: int | None
    usage: dict[str, int] = field(default_factory=dict)
    error_summary: str | None = None
    result_ref: str | None = None
    blueprint_step_id: str | None = None
    tool_activity_count: int = 0
    approvals_count: int = 0
    artifact_count: int = 0

    def to_dict(self) -> dict:
        """Return the bounded run record without any hidden state."""
        return asdict(self)

    def turn_receipt(self) -> dict:
        """Report observable work only; never reasoning or private arguments.

        The receipt is a safe projection of this record.  Every value is
        bounded at write time, so the projection adds no new exposure.
        """
        return {
            "run_id": self.id,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_ms": self.elapsed_ms,
            "provider": self.provider,
            "model": self.model,
            "capability_profile": self.capability_profile,
            "policy_revision": self.policy_revision,
            "usage": self.usage or None,
            "error_summary": self.error_summary,
            "result_ref": self.result_ref,
            "blueprint_step_id": self.blueprint_step_id,
            "tool_activity_count": self.tool_activity_count,
            "approvals_count": self.approvals_count,
            "artifact_count": self.artifact_count,
        }


class RunStore:
    """Persist owner/session-scoped run state in the local Pico workspace."""

    _STATES = {"queued", "running", "waiting_for_approval", "completed", "failed", "cancelled"}
    _TERMINAL_STATES = {"completed", "failed", "cancelled"}
    _TRANSITIONS = {
        "queued": {"running", "waiting_for_approval", "failed", "cancelled"},
        "running": {"waiting_for_approval", "completed", "failed", "cancelled"},
        "waiting_for_approval": {"running", "completed", "failed", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }
    _USAGE_KEYS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})
    _MAX_IDENTIFIER_LENGTH = 320
    _MAX_TEXT_LENGTH = 240
    _MAX_POLICY_REVISION_LENGTH = 200
    _MAX_ERROR_SUMMARY_LENGTH = 800
    _MAX_RESULT_REF_LENGTH = 400
    _MAX_LIST_LIMIT = 100

    def __init__(self, workspace: Path):
        root = workspace / "runs"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-runs.db"
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

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    mission_id TEXT,
                    provider TEXT,
                    model TEXT,
                    capability_profile TEXT NOT NULL,
                    policy_revision TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    elapsed_ms INTEGER,
                    usage TEXT,
                    error_summary TEXT,
                    result_ref TEXT,
                    tool_activity_count INTEGER NOT NULL DEFAULT 0,
                    approvals_count INTEGER NOT NULL DEFAULT 0,
                    artifact_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS runs_owner_updated_idx
                    ON runs(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS runs_owner_session_updated_idx
                    ON runs(owner_id, session_key, updated_at DESC);
                CREATE INDEX IF NOT EXISTS runs_owner_state_updated_idx
                    ON runs(owner_id, state, updated_at DESC);
                CREATE INDEX IF NOT EXISTS runs_owner_mission_updated_idx
                    ON runs(owner_id, mission_id, updated_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "blueprint_step_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN blueprint_step_id TEXT")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _required_identifier(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Run {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER_LENGTH:
            raise ValueError(f"Run {label} is limited to {cls._MAX_IDENTIFIER_LENGTH} characters")
        return clean

    @classmethod
    def _bounded_text(cls, value: object, label: str, limit: int, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Run {label} is required" if required else f"Run {label} must be text")
        clean = " ".join(value.split())
        if required and not clean:
            raise ValueError(f"Run {label} is required")
        if not required and not clean:
            return None
        if len(clean) > limit:
            raise ValueError(f"Run {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _validate_limit(cls, limit: object) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= cls._MAX_LIST_LIMIT:
            raise ValueError(f"Run list limit must be between 1 and {cls._MAX_LIST_LIMIT}")
        return limit

    @classmethod
    def _validate_state(cls, state: object) -> str:
        if state not in cls._STATES:
            raise ValueError(f"Unsupported run state: {state}")
        return str(state)

    @classmethod
    def _validate_usage(cls, usage: object) -> dict[str, int]:
        if usage is None:
            return {}
        if not isinstance(usage, dict):
            raise ValueError("Run usage must be a dictionary")
        clean: dict[str, int] = {}
        for key, value in usage.items():
            if key not in cls._USAGE_KEYS:
                raise ValueError(f"Unsupported run usage key: {key}")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Run usage {key} must be a non-negative integer")
            clean[key] = value
        return clean

    @classmethod
    def _validate_count(cls, value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Run {label} must be a non-negative integer")
        return value

    @staticmethod
    def _redact_error_summary(value: str) -> str:
        """Redact common secret shapes so safe summaries stay safe at rest."""
        value = re.sub(r"\b(?:sk|sk-proj)-[A-Za-z0-9_-]{4,}\b", "[key redacted]", value)
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [token redacted]", value)
        return value

    @classmethod
    def _run(cls, row: sqlite3.Row) -> RunRecord:
        values = dict(row)
        usage = values.pop("usage", None)
        record = RunRecord(
            **values,
            usage=cls._validate_usage(json.loads(usage)) if usage else {},
        )
        return record

    @classmethod
    def _elapsed_ms(cls, created_at: str, started_at: str | None, ended_at: str) -> int:
        start = datetime.fromisoformat(started_at or created_at)
        end = datetime.fromisoformat(ended_at)
        return max(0, int((end - start).total_seconds() * 1000))

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        capability_profile: str,
        mission_id: str | None = None,
        blueprint_step_id: str | None = None,
        policy_revision: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> RunRecord:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        capability_profile = self._bounded_text(
            capability_profile, "capability profile", self._MAX_IDENTIFIER_LENGTH, required=True
        )
        mission_id = self._bounded_text(
            mission_id, "mission", self._MAX_IDENTIFIER_LENGTH, required=False
        )
        blueprint_step_id = self._bounded_text(
            blueprint_step_id, "blueprint step", self._MAX_IDENTIFIER_LENGTH, required=False
        )
        policy_revision = self._bounded_text(
            policy_revision, "policy revision", self._MAX_POLICY_REVISION_LENGTH, required=False
        )
        provider = self._bounded_text(provider, "provider", self._MAX_TEXT_LENGTH, required=False)
        model = self._bounded_text(model, "model", self._MAX_TEXT_LENGTH, required=False)
        now = self._now()
        run = RunRecord(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            mission_id=mission_id,
            blueprint_step_id=blueprint_step_id,
            provider=provider,
            model=model,
            capability_profile=capability_profile,
            policy_revision=policy_revision,
            state="queued",
            created_at=now,
            updated_at=now,
            started_at=None,
            ended_at=None,
            elapsed_ms=None,
            usage={},
            error_summary=None,
            result_ref=None,
            tool_activity_count=0,
            approvals_count=0,
            artifact_count=0,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, owner_id, session_key, mission_id, blueprint_step_id, provider, model,
                    capability_profile, policy_revision, state, created_at, updated_at,
                    started_at, ended_at, elapsed_ms, usage, error_summary, result_ref,
                    tool_activity_count, approvals_count, artifact_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.owner_id,
                    run.session_key,
                    run.mission_id,
                    run.blueprint_step_id,
                    run.provider,
                    run.model,
                    run.capability_profile,
                    run.policy_revision,
                    run.state,
                    run.created_at,
                    run.updated_at,
                    run.started_at,
                    run.ended_at,
                    run.elapsed_ms,
                    None,
                    run.error_summary,
                    run.result_ref,
                    run.tool_activity_count,
                    run.approvals_count,
                    run.artifact_count,
                ),
            )
        return run

    def get(self, owner_id: str, run_id: str) -> RunRecord:
        owner_id = self._required_identifier(owner_id, "owner")
        if not isinstance(run_id, str) or not run_id.strip():
            raise KeyError("Run was not found for this owner")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND owner_id = ?", (run_id.strip(), owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Run was not found for this owner")
        return self._run(row)

    def list(
        self,
        owner_id: str,
        state: str | None = None,
        limit: int = 100,
        *,
        session_key: str | None = None,
    ) -> list[RunRecord]:
        owner_id = self._required_identifier(owner_id, "owner")
        limit = self._validate_limit(limit)
        query = "SELECT * FROM runs WHERE owner_id = ?"
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
        return [self._run(row) for row in rows]

    def list_by_mission(
        self, owner_id: str, mission_id: str, limit: int = 100
    ) -> list[RunRecord]:
        """Return runs linked to one mission, newest first."""
        owner_id = self._required_identifier(owner_id, "owner")
        mission_id = self._required_identifier(mission_id, "mission")
        limit = self._validate_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE owner_id = ? AND mission_id = ?
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                (owner_id, mission_id, limit),
            ).fetchall()
        return [self._run(row) for row in rows]

    def list_active(self, owner_id: str, session_key: str) -> list[RunRecord]:
        """Return non-terminal runs for one session, oldest first.

        The active-task boundary is the session; an owner may have several
        queued turns for the same session waiting on the processing lock.
        """
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE owner_id = ? AND session_key = ?
                  AND state IN ('queued', 'running', 'waiting_for_approval')
                ORDER BY updated_at ASC, id ASC
                """,
                (owner_id, session_key),
            ).fetchall()
        return [self._run(row) for row in rows]

    def get_active(self, owner_id: str, session_key: str) -> RunRecord | None:
        active = self.list_active(owner_id, session_key)
        # The agent processes one turn at a time. Prefer that current turn over
        # queued follow-up messages so a Stop decision never broad-cancels an
        # entire session backlog.
        for state in ("running", "waiting_for_approval", "queued"):
            current = next((run for run in active if run.state == state), None)
            if current is not None:
                return current
        return None

    def _transition(
        self,
        owner_id: str,
        run_id: str,
        target: str,
        *,
        started_at: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, int] | None = None,
        error_summary: str | None = None,
        result_ref: str | None = None,
        tool_activity_count: int | None = None,
        approvals_count: int | None = None,
        artifact_count: int | None = None,
    ) -> RunRecord:
        run = self.get(owner_id, run_id)
        if target not in self._TRANSITIONS[run.state]:
            raise ValueError(f"Run cannot transition from {run.state} to {target}")
        now = self._now()
        if target == "failed" and not error_summary:
            raise ValueError("A safe error summary is required when failing a run")
        ended_at = now if target in self._TERMINAL_STATES else None
        elapsed_ms = self._elapsed_ms(run.created_at, run.started_at, now) if ended_at else None
        next_started_at = run.started_at
        if target == "running" and run.started_at is None:
            next_started_at = started_at or now
        values = {
            "state": target,
            "updated_at": now,
            "started_at": next_started_at,
            "ended_at": ended_at,
            "elapsed_ms": elapsed_ms,
        }
        if provider is not None:
            values["provider"] = self._bounded_text(provider, "provider", self._MAX_TEXT_LENGTH, required=False)
        if model is not None:
            values["model"] = self._bounded_text(model, "model", self._MAX_TEXT_LENGTH, required=False)
        if usage is not None:
            values["usage"] = json.dumps(self._validate_usage(usage), sort_keys=True)
        if error_summary is not None:
            clean_summary = self._bounded_text(
                error_summary, "error summary", self._MAX_ERROR_SUMMARY_LENGTH, required=False
            )
            if clean_summary:
                clean_summary = self._redact_error_summary(clean_summary)
                if not clean_summary.strip():
                    clean_summary = "The model returned an error."
            values["error_summary"] = clean_summary
        if result_ref is not None:
            values["result_ref"] = self._bounded_text(
                result_ref, "result reference", self._MAX_RESULT_REF_LENGTH, required=False
            )
        if tool_activity_count is not None:
            values["tool_activity_count"] = self._validate_count(tool_activity_count, "tool activity count")
        if approvals_count is not None:
            values["approvals_count"] = self._validate_count(approvals_count, "approvals count")
        if artifact_count is not None:
            values["artifact_count"] = self._validate_count(artifact_count, "artifact count")

        sets = ", ".join(f"{column} = ?" for column in values)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE runs
                SET {sets}
                WHERE id = ? AND owner_id = ? AND state = ?
                """,
                (*values.values(), run.id, run.owner_id, run.state),
            )
        if cursor.rowcount != 1:
            raise ValueError("Run state changed before the transition could be recorded")
        return self.get(run.owner_id, run.id)

    def mark_running(self, owner_id: str, run_id: str, *, provider: str | None = None, model: str | None = None) -> RunRecord:
        return self._transition(
            owner_id, run_id, "running", started_at=self._now(), provider=provider, model=model
        )

    def wait_for_approval(self, owner_id: str, run_id: str) -> RunRecord:
        return self._transition(owner_id, run_id, "waiting_for_approval")

    def resume(self, owner_id: str, run_id: str) -> RunRecord:
        return self._transition(owner_id, run_id, "running", started_at=self._now())

    def complete(
        self,
        owner_id: str,
        run_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, int] | None = None,
        result_ref: str | None = None,
        tool_activity_count: int | None = None,
        approvals_count: int | None = None,
        artifact_count: int | None = None,
    ) -> RunRecord:
        return self._transition(
            owner_id,
            run_id,
            "completed",
            provider=provider,
            model=model,
            usage=usage,
            result_ref=result_ref,
            tool_activity_count=tool_activity_count,
            approvals_count=approvals_count,
            artifact_count=artifact_count,
        )

    def fail(
        self,
        owner_id: str,
        run_id: str,
        *,
        error_summary: str,
        provider: str | None = None,
        model: str | None = None,
        usage: dict[str, int] | None = None,
        result_ref: str | None = None,
        tool_activity_count: int | None = None,
        approvals_count: int | None = None,
        artifact_count: int | None = None,
    ) -> RunRecord:
        return self._transition(
            owner_id,
            run_id,
            "failed",
            error_summary=error_summary,
            provider=provider,
            model=model,
            usage=usage,
            result_ref=result_ref,
            tool_activity_count=tool_activity_count,
            approvals_count=approvals_count,
            artifact_count=artifact_count,
        )

    def cancel(self, owner_id: str, run_id: str) -> RunRecord:
        return self._transition(owner_id, run_id, "cancelled")

    def cancel_active(self, owner_id: str, session_key: str) -> RunRecord | None:
        """Cancel the current run for one session; never a queued follow-up.

        Returns the cancelled record, or ``None`` when the session has no
        non-terminal work.
        """
        current = self.get_active(owner_id, session_key)
        return self.cancel(owner_id, current.id) if current else None
