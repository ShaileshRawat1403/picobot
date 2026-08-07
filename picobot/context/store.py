"""Durable, owner-scoped context compaction records.

Every protected-tail compaction of a session owns exactly one
``CompactionRecord``.  The record keeps observable facts only: the source
message range that was summarized, the protected-tail boundary, token
estimates before/after, the bounded handoff text, provider/model attribution
when trusted, and an outcome (``completed`` | ``failed`` | ``skipped``).

Records are evidence.  They never modify the source transcript: the session
JSONL stays untouched and the handoff is only ever presented in the *next*
model window.  Compaction records are intentionally decoupled from personal
memory and are never used for learning.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class CompactionRecord:
    """A durable record of one context-compaction attempt for a session."""

    id: str
    owner_id: str
    session_key: str
    role: str
    outcome: str  # "completed" | "failed" | "skipped"
    reason: str | None
    created_at: str
    history_message_count: int
    source_range: str | None
    compact_start: int
    compact_end: int
    protected_tail_start: int
    tail_start: int
    compacted_message_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    saved_tokens: int
    summary: str | None
    provider: str | None
    model: str | None
    error_summary: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict:
        """Return the bounded record, including the handoff text."""
        return asdict(self)

    def public_view(self) -> dict:
        """Return the safe projection shown to the owner.

        The handoff text is intentionally excluded from the public view so
        transcripts are never replayed through the browser surface; the
        timeline shows facts (outcome, boundary, counts, saved tokens).
        """
        return {
            "record_id": self.id,
            "role": self.role,
            "outcome": self.outcome,
            "reason": self.reason,
            "created_at": self.created_at,
            "history_message_count": self.history_message_count,
            "source_range": self.source_range,
            "protected_tail_start": self.protected_tail_start,
            "tail_start": self.tail_start,
            "compacted_message_count": self.compacted_message_count,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "saved_tokens": self.saved_tokens,
            "provider": self.provider,
            "model": self.model,
            "error_summary": self.error_summary,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class CompactionStore:
    """Persist owner/session-scoped compaction evidence in the local workspace."""

    _OUTCOMES = {"completed", "failed", "skipped"}
    _DEFAULT_ROLE = "context_compression"
    _MAX_IDENTIFIER_LENGTH = 320
    _MAX_TEXT_LENGTH = 240
    _MAX_SUMMARY_LENGTH = 8000
    _MAX_ERROR_SUMMARY_LENGTH = 800
    _MAX_REASON_LENGTH = 240
    _MAX_LIST_LIMIT = 100

    def __init__(self, workspace: Path):
        root = workspace / "context"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-compactions.db"
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

                CREATE TABLE IF NOT EXISTS compactions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    history_message_count INTEGER NOT NULL,
                    source_range TEXT,
                    compact_start INTEGER NOT NULL,
                    compact_end INTEGER NOT NULL,
                    protected_tail_start INTEGER NOT NULL,
                    tail_start INTEGER NOT NULL,
                    compacted_message_count INTEGER NOT NULL,
                    estimated_tokens_before INTEGER NOT NULL,
                    estimated_tokens_after INTEGER NOT NULL,
                    saved_tokens INTEGER NOT NULL,
                    summary TEXT,
                    provider TEXT,
                    model TEXT,
                    error_summary TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER
                );
                CREATE INDEX IF NOT EXISTS compactions_owner_created_idx
                    ON compactions(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS compactions_owner_session_created_idx
                    ON compactions(owner_id, session_key, created_at DESC);
                """
            )
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Idempotently add columns introduced after the original schema."""
        with self._connect() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(compactions)").fetchall()
            }
            if "input_tokens" not in columns:
                connection.execute("ALTER TABLE compactions ADD COLUMN input_tokens INTEGER")
            if "output_tokens" not in columns:
                connection.execute("ALTER TABLE compactions ADD COLUMN output_tokens INTEGER")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _required_identifier(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Compaction {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER_LENGTH:
            raise ValueError(f"Compaction {label} is limited to {cls._MAX_IDENTIFIER_LENGTH} characters")
        return clean

    @classmethod
    def _bounded_text(cls, value: object, label: str, limit: int, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Compaction {label} is required" if required else f"Compaction {label} must be text")
        clean = " ".join(value.split())
        if required and not clean:
            raise ValueError(f"Compaction {label} is required")
        if not required and not clean:
            return None
        if len(clean) > limit:
            raise ValueError(f"Compaction {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _validate_limit(cls, limit: object) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= cls._MAX_LIST_LIMIT:
            raise ValueError(f"Compaction list limit must be between 1 and {cls._MAX_LIST_LIMIT}")
        return limit

    @classmethod
    def _validate_outcome(cls, outcome: object) -> str:
        if outcome not in cls._OUTCOMES:
            raise ValueError(f"Unsupported compaction outcome: {outcome}")
        return str(outcome)

    @classmethod
    def _validate_count(cls, value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Compaction {label} must be a non-negative integer")
        return value

    @classmethod
    def _validate_optional_count(cls, value: object, label: str) -> int | None:
        if value is None:
            return None
        return cls._validate_count(value, label)

    @classmethod
    def _validate_index(cls, value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Compaction {label} must be a non-negative integer")
        return value

    @staticmethod
    def _redact_sensitive_text(value: str) -> str:
        """Redact common secret shapes before any generated text reaches disk."""
        value = re.sub(r"\b(?:sk|sk-proj)-[A-Za-z0-9_-]{4,}\b", "[key redacted]", value)
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [token redacted]", value)
        return value

    @classmethod
    def _record(cls, row: sqlite3.Row) -> CompactionRecord:
        return CompactionRecord(**dict(row))

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        outcome: str,
        history_message_count: int,
        compact_start: int,
        compact_end: int,
        protected_tail_start: int,
        tail_start: int,
        compacted_message_count: int,
        estimated_tokens_before: int,
        estimated_tokens_after: int,
        role: str = _DEFAULT_ROLE,
        reason: str | None = None,
        summary: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        error_summary: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> CompactionRecord:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        outcome = self._validate_outcome(outcome)
        role = self._bounded_text(role, "role", self._MAX_TEXT_LENGTH, required=True) or self._DEFAULT_ROLE
        history_message_count = self._validate_count(history_message_count, "history message count")
        compact_start = self._validate_index(compact_start, "compact start")
        compact_end = self._validate_index(compact_end, "compact end")
        protected_tail_start = self._validate_index(protected_tail_start, "protected tail start")
        tail_start = self._validate_index(tail_start, "tail start")
        compacted_message_count = self._validate_count(compacted_message_count, "compacted message count")
        estimated_tokens_before = self._validate_count(estimated_tokens_before, "estimated tokens before")
        estimated_tokens_after = self._validate_count(estimated_tokens_after, "estimated tokens after")
        reason = self._bounded_text(reason, "reason", self._MAX_REASON_LENGTH, required=False)
        summary = self._bounded_text(summary, "summary", self._MAX_SUMMARY_LENGTH, required=False)
        if summary:
            summary = self._redact_sensitive_text(summary)
        provider = self._bounded_text(provider, "provider", self._MAX_TEXT_LENGTH, required=False)
        model = self._bounded_text(model, "model", self._MAX_TEXT_LENGTH, required=False)
        input_tokens = self._validate_optional_count(input_tokens, "input tokens")
        output_tokens = self._validate_optional_count(output_tokens, "output tokens")

        clean_error = self._bounded_text(
            error_summary, "error summary", self._MAX_ERROR_SUMMARY_LENGTH, required=False
        )
        if clean_error:
            clean_error = self._redact_sensitive_text(clean_error)
            if not clean_error.strip():
                clean_error = "Compaction failed without a safe diagnostic."
        if outcome == "failed" and not clean_error:
            raise ValueError("A safe error summary is required when a compaction fails")

        saved_tokens = max(0, estimated_tokens_before - estimated_tokens_after)
        record = CompactionRecord(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            session_key=session_key,
            role=role,
            outcome=outcome,
            reason=reason,
            created_at=self._now(),
            history_message_count=history_message_count,
            source_range=f"history[{compact_start}:{compact_end + 1}]",
            compact_start=compact_start,
            compact_end=compact_end,
            protected_tail_start=protected_tail_start,
            tail_start=tail_start,
            compacted_message_count=compacted_message_count,
            estimated_tokens_before=estimated_tokens_before,
            estimated_tokens_after=estimated_tokens_after,
            saved_tokens=saved_tokens,
            summary=summary,
            provider=provider,
            model=model,
            error_summary=clean_error,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO compactions (
                    id, owner_id, session_key, role, outcome, reason, created_at,
                    history_message_count, source_range, compact_start, compact_end,
                    protected_tail_start, tail_start, compacted_message_count,
                    estimated_tokens_before, estimated_tokens_after, saved_tokens,
                    summary, provider, model, error_summary, input_tokens, output_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.owner_id,
                    record.session_key,
                    record.role,
                    record.outcome,
                    record.reason,
                    record.created_at,
                    record.history_message_count,
                    record.source_range,
                    record.compact_start,
                    record.compact_end,
                    record.protected_tail_start,
                    record.tail_start,
                    record.compacted_message_count,
                    record.estimated_tokens_before,
                    record.estimated_tokens_after,
                    record.saved_tokens,
                    record.summary,
                    record.provider,
                    record.model,
                    record.error_summary,
                    record.input_tokens,
                    record.output_tokens,
                ),
            )
        return record

    def get(self, owner_id: str, record_id: str) -> CompactionRecord:
        owner_id = self._required_identifier(owner_id, "owner")
        if not isinstance(record_id, str) or not record_id.strip():
            raise KeyError("Compaction record was not found for this owner")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM compactions WHERE id = ? AND owner_id = ?",
                (record_id.strip(), owner_id),
            ).fetchone()
        if row is None:
            raise KeyError("Compaction record was not found for this owner")
        return self._record(row)

    def list(
        self,
        owner_id: str,
        session_key: str,
        limit: int = 20,
    ) -> list[CompactionRecord]:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        limit = self._validate_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM compactions
                WHERE owner_id = ? AND session_key = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (owner_id, session_key, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def latest(self, owner_id: str, session_key: str) -> CompactionRecord | None:
        records = self.list(owner_id, session_key, limit=1)
        return records[0] if records else None

    def latest_completed(self, owner_id: str, session_key: str) -> CompactionRecord | None:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM compactions
                WHERE owner_id = ? AND session_key = ? AND outcome = 'completed'
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (owner_id, session_key),
            ).fetchone()
        return self._record(row) if row else None
