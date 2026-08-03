"""Durable, redacted evidence of context supplied to a model turn.

Context evidence is deliberately a small ledger, not a second transcript.  It
stores only bounded references and deterministic planning facts so an owner
can understand a run without exposing prompt text, skill contents, or hidden
reasoning.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ContextEvidence:
    """Observable context facts for one owner/session/run combination."""

    id: str
    owner_id: str
    session_key: str
    run_id: str
    recorded_at: str
    history_message_count: int
    memory_ids: tuple[str, ...]
    skill_names: tuple[str, ...]
    plan_action: str
    plan_reason: str
    estimated_tokens_before: int
    estimated_tokens_after: int
    compaction_record_ids: tuple[str, ...]
    stance_id: str = "explore"
    orientation: dict[str, object] | None = None

    def public_view(self) -> dict:
        """Return a safe owner-facing projection without private identifiers."""
        return {
            "record_id": self.id,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "history_message_count": self.history_message_count,
            "memory_ids": list(self.memory_ids),
            "skill_names": list(self.skill_names),
            "plan_action": self.plan_action,
            "plan_reason": self.plan_reason,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "compaction_record_ids": list(self.compaction_record_ids),
            "stance_id": self.stance_id,
            "orientation": self.orientation or {},
        }


class ContextEvidenceStore:
    """SQLite-backed context evidence scoped to one owner and session."""

    _MAX_LIST = 24
    _MAX_IDENTIFIER = 320
    _MAX_PLAN_VALUE = 80
    _VALID_ACTIONS = {"none", "compact", "trim"}

    def __init__(self, workspace: Path):
        root = workspace / "context"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-context.db"
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
                CREATE TABLE IF NOT EXISTS context_evidence (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL,
                    history_message_count INTEGER NOT NULL,
                    memory_ids TEXT NOT NULL,
                    skill_names TEXT NOT NULL,
                    plan_action TEXT NOT NULL,
                    plan_reason TEXT NOT NULL,
                    estimated_tokens_before INTEGER NOT NULL,
                    estimated_tokens_after INTEGER NOT NULL,
                    compaction_record_ids TEXT NOT NULL,
                    stance_id TEXT NOT NULL DEFAULT 'explore',
                    orientation_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS context_evidence_owner_session_idx
                    ON context_evidence(owner_id, session_key, recorded_at DESC);
                CREATE INDEX IF NOT EXISTS context_evidence_owner_run_idx
                    ON context_evidence(owner_id, run_id);
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(context_evidence)").fetchall()
            }
            if "stance_id" not in columns:
                connection.execute(
                    "ALTER TABLE context_evidence ADD COLUMN stance_id TEXT NOT NULL DEFAULT 'explore'"
                )
            if "orientation_json" not in columns:
                connection.execute(
                    "ALTER TABLE context_evidence ADD COLUMN orientation_json TEXT NOT NULL DEFAULT '{}'"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _required(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Context {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER:
            raise ValueError(f"Context {label} is limited to {cls._MAX_IDENTIFIER} characters")
        return clean

    @classmethod
    def _bounded_list(cls, values: object, label: str) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"Context {label} must be a list")
        clean: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            item = value.strip()
            if len(item) > cls._MAX_IDENTIFIER:
                raise ValueError(f"Context {label} item is too long")
            if item not in clean:
                clean.append(item)
        return tuple(clean[: cls._MAX_LIST])

    @staticmethod
    def _bounded_count(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Context {label} must be a non-negative integer")
        return value

    @classmethod
    def _plan_value(cls, value: object, label: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Context {label} must be text")
        clean = " ".join(value.split())
        if not clean:
            raise ValueError(f"Context {label} is required")
        return clean[: cls._MAX_PLAN_VALUE]

    @classmethod
    def _record(cls, row: sqlite3.Row) -> ContextEvidence:
        values = dict(row)
        return ContextEvidence(
            id=values["id"],
            owner_id=values["owner_id"],
            session_key=values["session_key"],
            run_id=values["run_id"],
            recorded_at=values["recorded_at"],
            history_message_count=int(values["history_message_count"]),
            memory_ids=tuple(json.loads(values["memory_ids"])),
            skill_names=tuple(json.loads(values["skill_names"])),
            plan_action=values["plan_action"],
            plan_reason=values["plan_reason"],
            estimated_tokens_before=int(values["estimated_tokens_before"]),
            estimated_tokens_after=int(values["estimated_tokens_after"]),
            compaction_record_ids=tuple(json.loads(values["compaction_record_ids"])),
            stance_id=values.get("stance_id") or "explore",
            orientation=json.loads(values.get("orientation_json") or "{}"),
        )

    def record(
        self,
        *,
        owner_id: str,
        session_key: str,
        run_id: str,
        history_message_count: int,
        memory_ids: list[str] | tuple[str, ...] = (),
        skill_names: list[str] | tuple[str, ...] = (),
        plan_action: str = "none",
        plan_reason: str = "below_budget",
        estimated_tokens_before: int = 0,
        estimated_tokens_after: int = 0,
        compaction_record_ids: list[str] | tuple[str, ...] = (),
        stance_id: str = "explore",
        orientation: dict[str, object] | None = None,
    ) -> ContextEvidence:
        owner_id = self._required(owner_id, "owner")
        session_key = self._required(session_key, "session")
        run_id = self._required(run_id, "run")
        history_message_count = self._bounded_count(history_message_count, "history count")
        estimated_tokens_before = self._bounded_count(estimated_tokens_before, "tokens before")
        estimated_tokens_after = self._bounded_count(estimated_tokens_after, "tokens after")
        if plan_action not in self._VALID_ACTIONS:
            raise ValueError(f"Unsupported context plan action: {plan_action}")
        plan_action = str(plan_action)
        plan_reason = self._plan_value(plan_reason, "plan reason")
        memory_ids = self._bounded_list(memory_ids, "memory ids")
        skill_names = self._bounded_list(skill_names, "skill names")
        compaction_record_ids = self._bounded_list(compaction_record_ids, "compaction ids")
        from picobot.session.stance import get_stance

        stance_id = get_stance(stance_id).id
        if orientation is None:
            orientation = {}
        if not isinstance(orientation, dict):
            raise ValueError("Context orientation must be an object")
        # The orientation receipt intentionally captures only the safe compact
        # projection prepared by session.orientation, never its private prose.
        try:
            orientation_json = json.dumps(orientation, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("Context orientation must be JSON-safe") from exc
        if len(orientation_json) > 2_000:
            raise ValueError("Context orientation is too large")
        record = ContextEvidence(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            run_id=run_id,
            recorded_at=self._now(),
            history_message_count=history_message_count,
            memory_ids=memory_ids,
            skill_names=skill_names,
            plan_action=plan_action,
            plan_reason=plan_reason,
            estimated_tokens_before=estimated_tokens_before,
            estimated_tokens_after=estimated_tokens_after,
            compaction_record_ids=compaction_record_ids,
            stance_id=stance_id,
            orientation=orientation,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO context_evidence(
                    id, owner_id, session_key, run_id, recorded_at,
                    history_message_count, memory_ids, skill_names, plan_action,
                    plan_reason, estimated_tokens_before, estimated_tokens_after,
                    compaction_record_ids, stance_id, orientation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    recorded_at=excluded.recorded_at,
                    history_message_count=excluded.history_message_count,
                    memory_ids=excluded.memory_ids,
                    skill_names=excluded.skill_names,
                    plan_action=excluded.plan_action,
                    plan_reason=excluded.plan_reason,
                    estimated_tokens_before=excluded.estimated_tokens_before,
                    estimated_tokens_after=excluded.estimated_tokens_after,
                    compaction_record_ids=excluded.compaction_record_ids,
                    stance_id=excluded.stance_id,
                    orientation_json=excluded.orientation_json
                """,
                (
                    record.id,
                    record.owner_id,
                    record.session_key,
                    record.run_id,
                    record.recorded_at,
                    record.history_message_count,
                    json.dumps(record.memory_ids),
                    json.dumps(record.skill_names),
                    record.plan_action,
                    record.plan_reason,
                    record.estimated_tokens_before,
                    record.estimated_tokens_after,
                    json.dumps(record.compaction_record_ids),
                    record.stance_id,
                    orientation_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM context_evidence WHERE owner_id = ? AND run_id = ?",
                (owner_id, run_id),
            ).fetchone()
        return self._record(row)

    def get_for_run(self, owner_id: str, run_id: str) -> ContextEvidence:
        owner_id = self._required(owner_id, "owner")
        run_id = self._required(run_id, "run")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_evidence WHERE owner_id = ? AND run_id = ?",
                (owner_id, run_id),
            ).fetchone()
        if row is None:
            raise KeyError("Context evidence was not found for this owner")
        return self._record(row)

    def latest(self, owner_id: str, session_key: str) -> ContextEvidence | None:
        owner_id = self._required(owner_id, "owner")
        session_key = self._required(session_key, "session")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM context_evidence
                WHERE owner_id = ? AND session_key = ?
                ORDER BY recorded_at DESC, id DESC LIMIT 1
                """,
                (owner_id, session_key),
            ).fetchone()
        return self._record(row) if row else None

    def skill_use_count(self, owner_id: str, skill_name: str) -> int:
        owner_id = self._required(owner_id, "owner")
        skill_name = self._required(skill_name, "skill")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT skill_names FROM context_evidence WHERE owner_id = ?",
                (owner_id,),
            ).fetchall()
        return sum(skill_name in json.loads(row["skill_names"]) for row in rows)
