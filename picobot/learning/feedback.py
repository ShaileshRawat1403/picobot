"""Owner-controlled feedback on completed Pico responses.

Feedback is a bounded learning signal, not an instruction channel.  It links
one user's review to a durable run without copying the response, transcript,
tool arguments, or hidden reasoning.  Recording feedback never changes
memory, skills, profiles, or provider policy by itself.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


FeedbackKind = Literal["useful", "not_useful", "correction"]


@dataclass(frozen=True)
class ResponseFeedback:
    id: str
    owner_id: str
    session_key: str
    run_id: str
    kind: str
    note: str | None
    created_at: str
    updated_at: str
    candidate_type: str | None = None
    candidate_ref: str | None = None


class ResponseFeedbackStore:
    """SQLite-backed, owner/session-scoped response reviews."""

    _KINDS = {"useful", "not_useful", "correction"}
    _MAX_IDENTIFIER_LENGTH = 320
    _MAX_NOTE_LENGTH = 2_000

    def __init__(self, workspace: Path):
        root = workspace / "learning"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-feedback.db"
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

                CREATE TABLE IF NOT EXISTS response_feedback (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    candidate_type TEXT,
                    candidate_ref TEXT,
                    UNIQUE(owner_id, session_key, run_id)
                );
                CREATE INDEX IF NOT EXISTS response_feedback_owner_updated_idx
                    ON response_feedback(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS response_feedback_owner_session_updated_idx
                    ON response_feedback(owner_id, session_key, updated_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(response_feedback)").fetchall()
            }
            if "candidate_type" not in columns:
                connection.execute("ALTER TABLE response_feedback ADD COLUMN candidate_type TEXT")
            if "candidate_ref" not in columns:
                connection.execute("ALTER TABLE response_feedback ADD COLUMN candidate_ref TEXT")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _identifier(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Feedback {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER_LENGTH:
            raise ValueError(f"Feedback {label} is limited to {cls._MAX_IDENTIFIER_LENGTH} characters")
        return clean

    @classmethod
    def _kind(cls, value: object) -> str:
        if not isinstance(value, str) or value not in cls._KINDS:
            raise ValueError("Feedback kind must be useful, not_useful, or correction")
        return value

    @classmethod
    def _note(cls, value: object, *, required: bool) -> str | None:
        if value is None:
            if required:
                raise ValueError("A correction note is required")
            return None
        if not isinstance(value, str):
            raise ValueError("Feedback note must be text")
        note = " ".join(value.split())
        if required and not note:
            raise ValueError("A correction note is required")
        if len(note) > cls._MAX_NOTE_LENGTH:
            raise ValueError(f"Feedback note is limited to {cls._MAX_NOTE_LENGTH} characters")
        return note or None

    @staticmethod
    def _record(row: sqlite3.Row) -> ResponseFeedback:
        return ResponseFeedback(
            id=row["id"],
            owner_id=row["owner_id"],
            session_key=row["session_key"],
            run_id=row["run_id"],
            kind=row["kind"],
            note=row["note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            candidate_type=row["candidate_type"] if "candidate_type" in row.keys() else None,
            candidate_ref=row["candidate_ref"] if "candidate_ref" in row.keys() else None,
        )

    def record(
        self,
        *,
        owner_id: object,
        session_key: object,
        run_id: object,
        kind: object,
        note: object = None,
    ) -> ResponseFeedback:
        owner = self._identifier(owner_id, "owner")
        session = self._identifier(session_key, "session")
        run = self._identifier(run_id, "run")
        feedback_kind = self._kind(kind)
        clean_note = self._note(note, required=feedback_kind == "correction")
        now = self._now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, created_at, kind, note, candidate_ref FROM response_feedback
                WHERE owner_id = ? AND session_key = ? AND run_id = ?
                """,
                (owner, session, run),
            ).fetchone()
            if existing and existing["candidate_ref"] and (
                existing["kind"] != feedback_kind or existing["note"] != clean_note
            ):
                raise ValueError("Feedback with a learning candidate cannot be changed")
            feedback_id = existing["id"] if existing else str(uuid.uuid4())
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """
                INSERT INTO response_feedback(
                    id, owner_id, session_key, run_id, kind, note, created_at, updated_at,
                    candidate_type, candidate_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(owner_id, session_key, run_id) DO UPDATE SET
                    kind = excluded.kind,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (feedback_id, owner, session, run, feedback_kind, clean_note, created_at, now),
            )
        return self.get(owner, feedback_id)

    def attach_candidate(
        self, owner_id: object, feedback_id: object, candidate_type: object, candidate_ref: object
    ) -> ResponseFeedback:
        owner = self._identifier(owner_id, "owner")
        feedback = self._identifier(feedback_id, "id")
        if candidate_type not in {"memory", "skill"}:
            raise ValueError("Candidate type must be memory or skill")
        reference = self._identifier(candidate_ref, "candidate reference")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE response_feedback
                SET candidate_type = ?, candidate_ref = ?, updated_at = ?
                WHERE id = ? AND owner_id = ? AND candidate_ref IS NULL
                """,
                (candidate_type, reference, self._now(), feedback, owner),
            )
        if cursor.rowcount == 0:
            record = self.get(owner, feedback)
            if record.candidate_type != candidate_type or record.candidate_ref != reference:
                raise ValueError("Feedback already has a different learning candidate")
        return self.get(owner, feedback)

    def get(self, owner_id: object, feedback_id: object) -> ResponseFeedback:
        owner = self._identifier(owner_id, "owner")
        feedback = self._identifier(feedback_id, "id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM response_feedback WHERE id = ? AND owner_id = ?",
                (feedback, owner),
            ).fetchone()
        if row is None:
            raise KeyError("Feedback was not found for this user")
        return self._record(row)

    def list(
        self, owner_id: object, *, session_key: object | None = None, limit: int = 100
    ) -> list[ResponseFeedback]:
        owner = self._identifier(owner_id, "owner")
        limit = max(1, min(int(limit), 100))
        query = "SELECT * FROM response_feedback WHERE owner_id = ?"
        params: list[object] = [owner]
        if session_key is not None:
            session = self._identifier(session_key, "session")
            query += " AND session_key = ?"
            params.append(session)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._record(row) for row in rows]
