"""Local artifact store with revisions and provenance.

Artifacts are intentional user-owned work products. They live inside the Pico
workspace, not in arbitrary locations on the host filesystem. The SQLite
manifest makes them queryable while the revision files remain easy to inspect
or move as ordinary local files.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Artifact:
    id: str
    owner_id: str
    session_key: str
    title: str
    kind: str
    content_type: str
    status: str
    verification_status: str
    revision: int
    relative_path: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ArtifactRevision:
    artifact_id: str
    revision: int
    relative_path: str
    created_at: str


class ArtifactStore:
    """Store artifacts locally with owner and session provenance."""

    _MAX_TITLE_LENGTH = 160
    _MAX_CONTENT_LENGTH = 512_000
    _KINDS = {"note", "brief", "plan", "draft", "checklist", "data"}
    _CONTENT_TYPES = {
        "text/markdown": "md",
        "text/plain": "txt",
        "application/json": "json",
        "text/csv": "csv",
    }
    _VERIFICATION_STATES = {"verified", "stale", "unverified"}

    def __init__(self, workspace: Path):
        self.root = workspace / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / ".pico-artifacts.db"
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

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS artifacts_owner_updated_idx
                    ON artifacts(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS artifacts_owner_session_idx
                    ON artifacts(owner_id, session_key, updated_at DESC);

                CREATE TABLE IF NOT EXISTS artifact_revisions (
                    artifact_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, revision),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()}
            if "verification_status" not in columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified'"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _clean_title(cls, title: str) -> str:
        clean = " ".join(title.split())
        if not clean:
            raise ValueError("Artifact title is required")
        if len(clean) > cls._MAX_TITLE_LENGTH:
            raise ValueError(f"Artifact title is limited to {cls._MAX_TITLE_LENGTH} characters")
        return clean

    @classmethod
    def _clean_content(cls, content: str) -> str:
        if not isinstance(content, str):
            raise ValueError("Artifact content is required")
        if len(content) > cls._MAX_CONTENT_LENGTH:
            raise ValueError(f"Artifact content is limited to {cls._MAX_CONTENT_LENGTH} characters")
        return content

    @classmethod
    def _validate_kind(cls, kind: str) -> str:
        if kind not in cls._KINDS:
            raise ValueError(f"Unsupported artifact kind: {kind}")
        return kind

    @classmethod
    def _validate_content_type(cls, content_type: str) -> str:
        if content_type not in cls._CONTENT_TYPES:
            raise ValueError(f"Unsupported artifact content type: {content_type}")
        return content_type

    @staticmethod
    def _artifact(row: sqlite3.Row) -> Artifact:
        return Artifact(
            id=row["id"],
            owner_id=row["owner_id"],
            session_key=row["session_key"],
            title=row["title"],
            kind=row["kind"],
            content_type=row["content_type"],
            status=row["status"],
            verification_status=row["verification_status"] or "unverified",
            revision=int(row["revision"]),
            relative_path=row["relative_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _revision_path(self, artifact_id: str, revision: int, content_type: str) -> Path:
        extension = self._CONTENT_TYPES[content_type]
        path = self.root / artifact_id / f"v{revision}.{extension}"
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("Artifact path escaped the Pico workspace") from exc
        return resolved

    def create(
        self,
        *,
        owner_id: str,
        session_key: str,
        title: str,
        content: str,
        kind: str = "note",
        content_type: str = "text/markdown",
    ) -> Artifact:
        if not owner_id.strip() or not session_key.strip():
            raise ValueError("Artifact owner and session are required")
        title = self._clean_title(title)
        content = self._clean_content(content)
        kind = self._validate_kind(kind)
        content_type = self._validate_content_type(content_type)
        artifact_id = str(uuid.uuid4())
        now = self._now()
        revision = 1
        file_path = self._revision_path(artifact_id, revision, content_type)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        relative_path = str(file_path.relative_to(self.root))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        id, owner_id, session_key, title, kind, content_type,
                        status, verification_status, revision, relative_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'draft', 'unverified', ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        owner_id,
                        session_key,
                        title,
                        kind,
                        content_type,
                        revision,
                        relative_path,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_revisions(artifact_id, revision, relative_path, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (artifact_id, revision, relative_path, now),
                )
        except Exception:
            file_path.unlink(missing_ok=True)
            raise
        return self.get(owner_id, artifact_id)

    def get(self, owner_id: str, artifact_id: str) -> Artifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ? AND owner_id = ?", (artifact_id, owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Artifact was not found for this user")
        return self._artifact(row)

    def list(self, owner_id: str, *, session_key: str | None = None, limit: int = 100) -> list[Artifact]:
        limit = max(1, min(limit, 100))
        query = "SELECT * FROM artifacts WHERE owner_id = ?"
        params: list[object] = [owner_id]
        if session_key:
            query += " AND session_key = ?"
            params.append(session_key)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._artifact(row) for row in rows]

    def read_content(self, owner_id: str, artifact_id: str, revision: int | None = None) -> str:
        artifact = self.get(owner_id, artifact_id)
        selected_revision = revision or artifact.revision
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relative_path FROM artifact_revisions
                WHERE artifact_id = ? AND revision = ?
                """,
                (artifact_id, selected_revision),
            ).fetchone()
        if row is None:
            raise KeyError("Artifact revision was not found")
        path = (self.root / row["relative_path"]).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("Artifact path escaped the Pico workspace") from exc
        if not path.exists():
            raise FileNotFoundError("Artifact file is missing")
        return path.read_text(encoding="utf-8")

    def revise(self, owner_id: str, artifact_id: str, content: str) -> Artifact:
        artifact = self.get(owner_id, artifact_id)
        content = self._clean_content(content)
        next_revision = artifact.revision + 1
        now = self._now()
        file_path = self._revision_path(artifact_id, next_revision, artifact.content_type)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        relative_path = str(file_path.relative_to(self.root))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE artifacts
                    SET revision = ?, relative_path = ?, verification_status = 'stale', updated_at = ?
                    WHERE id = ? AND owner_id = ?
                    """,
                    (next_revision, relative_path, now, artifact_id, owner_id),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_revisions(artifact_id, revision, relative_path, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (artifact_id, next_revision, relative_path, now),
                )
        except Exception:
            file_path.unlink(missing_ok=True)
            raise
        return self.get(owner_id, artifact_id)

    def set_verification(self, owner_id: str, artifact_id: str, verification_status: str) -> Artifact:
        """Set an explicit owner-reviewed verification state for an artifact."""
        if verification_status not in self._VERIFICATION_STATES:
            raise ValueError("Unsupported artifact verification status")
        self.get(owner_id, artifact_id)
        now = self._now()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE artifacts
                SET verification_status = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (verification_status, now, artifact_id, owner_id),
            )
        if updated.rowcount != 1:
            raise KeyError("Artifact was not found for this user")
        return self.get(owner_id, artifact_id)

    def revisions(self, owner_id: str, artifact_id: str) -> list[ArtifactRevision]:
        self.get(owner_id, artifact_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, revision, relative_path, created_at
                FROM artifact_revisions WHERE artifact_id = ? ORDER BY revision DESC
                """,
                (artifact_id,),
            ).fetchall()
        return [ArtifactRevision(**dict(row)) for row in rows]
