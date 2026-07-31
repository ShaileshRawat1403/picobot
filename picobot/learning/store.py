"""Explicit, reviewable conversion of a completed session into a Pico skill.

Pico never writes a live skill as a side effect of chat.  An owner first
creates a proposal tied to one of their sessions, reviews or edits its draft,
and then explicitly approves it.  Approval is intentionally non-destructive:
an existing workspace skill is never overwritten by this store.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SkillProposal:
    id: str
    owner_id: str
    session_key: str
    name: str
    description: str
    content: str
    status: str
    created_at: str
    updated_at: str
    approved_at: str | None
    installed_path: str | None


class SkillProposalStore:
    """SQLite-backed skill proposals scoped to an owner and session."""

    _NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
    _STATUSES = {"proposed", "approved", "rejected"}
    _MAX_DESCRIPTION_LENGTH = 280
    _MAX_CONTENT_LENGTH = 24_000

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = workspace / "learning"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "pico-learning.db"
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

                CREATE TABLE IF NOT EXISTS skill_proposals (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    installed_path TEXT
                );
                CREATE INDEX IF NOT EXISTS skill_proposals_owner_updated_idx
                    ON skill_proposals(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS skill_proposals_owner_session_idx
                    ON skill_proposals(owner_id, session_key, updated_at DESC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _clean_name(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Skill name is required")
        name = value.strip().lower()
        if not cls._NAME_RE.fullmatch(name):
            raise ValueError("Use a lowercase skill name with letters, numbers, and hyphens")
        return name

    @classmethod
    def _clean_description(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Skill description is required")
        description = " ".join(value.split())
        if not description:
            raise ValueError("Skill description is required")
        if len(description) > cls._MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"Skill description is limited to {cls._MAX_DESCRIPTION_LENGTH} characters")
        return description

    @classmethod
    def _clean_content(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Skill draft is required")
        content = value.strip()
        if not content:
            raise ValueError("Skill draft is required")
        if len(content) > cls._MAX_CONTENT_LENGTH:
            raise ValueError(f"Skill draft is limited to {cls._MAX_CONTENT_LENGTH} characters")
        return content + "\n"

    @staticmethod
    def _proposal(row: sqlite3.Row) -> SkillProposal:
        return SkillProposal(
            id=row["id"],
            owner_id=row["owner_id"],
            session_key=row["session_key"],
            name=row["name"],
            description=row["description"],
            content=row["content"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            installed_path=row["installed_path"],
        )

    @staticmethod
    def draft(name: str, description: str, guidance: str) -> str:
        """Create a conservative starter draft, ready for owner editing."""
        guidance = guidance.strip()
        guidance_block = guidance or "Describe the repeatable workflow in concrete, reviewable steps."
        return f'''---
name: {name}
description: {description}
---

# {name}

## Use when

{description}

## Workflow

{guidance_block}

## Guardrails

- Confirm the intended outcome and constraints before taking consequential action.
- Keep credentials, private data, and unrelated workspace files out of the task.
- Ask for review before irreversible external actions.
'''

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        name: object,
        description: object,
        guidance: object = "",
    ) -> SkillProposal:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("Skill proposal owner is required")
        if not isinstance(session_key, str) or not session_key.strip():
            raise ValueError("Skill proposal session is required")
        clean_name = self._clean_name(name)
        clean_description = self._clean_description(description)
        if not isinstance(guidance, str):
            raise ValueError("Skill guidance must be text")
        content = self._clean_content(self.draft(clean_name, clean_description, guidance))
        proposal_id = str(uuid.uuid4())
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO skill_proposals(
                    id, owner_id, session_key, name, description, content, status,
                    created_at, updated_at, approved_at, installed_path
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, NULL, NULL)
                """,
                (proposal_id, owner_id, session_key, clean_name, clean_description, content, now, now),
            )
        return self.get(owner_id, proposal_id)

    def get(self, owner_id: str, proposal_id: str) -> SkillProposal:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM skill_proposals WHERE id = ? AND owner_id = ?",
                (proposal_id, owner_id),
            ).fetchone()
        if row is None:
            raise KeyError("Skill proposal was not found for this user")
        return self._proposal(row)

    def list(self, owner_id: str, *, session_key: str | None = None, limit: int = 100) -> list[SkillProposal]:
        limit = max(1, min(limit, 100))
        query = "SELECT * FROM skill_proposals WHERE owner_id = ?"
        params: list[object] = [owner_id]
        if session_key is not None:
            query += " AND session_key = ?"
            params.append(session_key)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._proposal(row) for row in rows]

    def revise(self, owner_id: str, proposal_id: str, content: object) -> SkillProposal:
        proposal = self.get(owner_id, proposal_id)
        if proposal.status != "proposed":
            raise ValueError("Only proposed skills can be edited")
        clean_content = self._clean_content(content)
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE skill_proposals SET content = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (clean_content, now, proposal_id, owner_id),
            )
        return self.get(owner_id, proposal_id)

    def reject(self, owner_id: str, proposal_id: str) -> SkillProposal:
        proposal = self.get(owner_id, proposal_id)
        if proposal.status != "proposed":
            raise ValueError("Only proposed skills can be rejected")
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE skill_proposals SET status = 'rejected', updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (now, proposal_id, owner_id),
            )
        return self.get(owner_id, proposal_id)

    def approve(self, owner_id: str, proposal_id: str) -> SkillProposal:
        proposal = self.get(owner_id, proposal_id)
        if proposal.status != "proposed":
            raise ValueError("Only proposed skills can be approved")
        target = self.workspace / "skills" / proposal.name / "SKILL.md"
        resolved_target = target.resolve()
        skills_root = (self.workspace / "skills").resolve()
        try:
            resolved_target.relative_to(skills_root)
        except ValueError as exc:
            raise ValueError("Skill path escaped the Pico workspace") from exc
        if resolved_target.exists():
            raise ValueError("A workspace skill with this name already exists; Pico will not overwrite it")

        resolved_target.parent.mkdir(parents=True, exist_ok=False)
        try:
            resolved_target.write_text(proposal.content, encoding="utf-8")
            now = self._now()
            installed_path = str(resolved_target.relative_to(self.workspace))
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE skill_proposals
                    SET status = 'approved', approved_at = ?, installed_path = ?, updated_at = ?
                    WHERE id = ? AND owner_id = ?
                    """,
                    (now, installed_path, now, proposal_id, owner_id),
                )
        except Exception:
            if resolved_target.exists():
                resolved_target.unlink()
            try:
                resolved_target.parent.rmdir()
            except OSError:
                pass
            raise
        return self.get(owner_id, proposal_id)
