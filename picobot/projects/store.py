"""Small, explicit project registry for Pico Home.

Projects are context records, not filesystem sandboxes or execution authority.
They make sources, intended capabilities, relationships, and inspection
freshness reviewable before later context and action slices consume them.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Project:
    id: str
    owner_id: str
    title: str
    kind: str
    purpose: str
    status: str
    capabilities: list[str]
    inspected_at: str | None
    created_at: str
    updated_at: str
    archived_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectSource:
    id: str
    project_id: str
    kind: str
    label: str
    locator: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectLink:
    id: str
    project_id: str
    related_project_id: str
    relation: str
    summary: str | None
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectStore:
    """Persist additive owner-scoped project context in the Pico workspace."""

    _KINDS = {"software", "research", "writing", "business", "personal"}
    _STATES = {"active", "paused", "archived"}
    _SOURCE_KINDS = {"local_folder", "github_repo", "url", "artifact"}
    _RELATIONS = {"depends_on", "informs", "overlaps", "uses", "replaces"}
    _CAPABILITIES = {
        "read_workspace", "run_diagnostics", "draft_changes", "apply_bounded_changes",
        "github_review", "create_artifacts", "browser_read",
    }
    _ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")

    def __init__(self, workspace: Path):
        root = workspace / "projects"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-projects.db"
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
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL,
                    kind TEXT NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL, inspected_at TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS projects_owner_updated_idx ON projects(owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS project_sources (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    label TEXT NOT NULL, locator TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS project_sources_project_idx ON project_sources(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS project_links (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, related_project_id TEXT NOT NULL,
                    relation TEXT NOT NULL, summary TEXT, created_at TEXT NOT NULL,
                    UNIQUE(project_id, related_project_id, relation),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(related_project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _text(value: object, label: str, limit: int, *, required: bool = True) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Project {label} must be text")
        clean = " ".join(value.split())
        if not clean and required:
            raise ValueError(f"Project {label} is required")
        if not clean:
            return None
        if len(clean) > limit:
            raise ValueError(f"Project {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _capabilities(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("Project capabilities must be a list of capability names")
        result = sorted(set(value))
        unknown = set(result) - cls._CAPABILITIES
        if unknown:
            raise ValueError(f"Unknown project capability: {sorted(unknown)[0]}")
        return result

    @classmethod
    def _row(cls, row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"], owner_id=row["owner_id"], title=row["title"], kind=row["kind"],
            purpose=row["purpose"], status=row["status"],
            capabilities=json.loads(row["capabilities_json"]), inspected_at=row["inspected_at"],
            created_at=row["created_at"], updated_at=row["updated_at"], archived_at=row["archived_at"],
        )

    def create(self, owner_id: str, *, title: object, kind: object, purpose: object, capabilities: object = None) -> Project:
        owner = self._text(owner_id, "owner", 160)
        if kind not in self._KINDS:
            raise ValueError("Project kind is not supported")
        now, project_id, grants = self._now(), uuid.uuid4().hex, self._capabilities(capabilities)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, NULL)",
                (project_id, owner, self._text(title, "title", 160), kind, self._text(purpose, "purpose", 4000), json.dumps(grants), now, now),
            )
        return self.get(owner, project_id)

    def list(self, owner_id: str, *, include_archived: bool = False) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE owner_id = ?" + ("" if include_archived else " AND status != 'archived'") + " ORDER BY updated_at DESC",
                (owner_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, owner_id: str, project_id: str) -> Project:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ? AND owner_id = ?", (project_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("Project was not found")
        return self._row(row)

    def update(self, owner_id: str, project_id: str, *, title: object, kind: object, purpose: object, capabilities: object, status: object) -> Project:
        current = self.get(owner_id, project_id)
        if kind not in self._KINDS or status not in self._STATES:
            raise ValueError("Project kind or status is not supported")
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE projects SET title=?, kind=?, purpose=?, status=?, capabilities_json=?, archived_at=?, updated_at=? WHERE id=? AND owner_id=?",
                (self._text(title, "title", 160), kind, self._text(purpose, "purpose", 4000), status, json.dumps(self._capabilities(capabilities)), now if status == "archived" else None, now, project_id, owner_id),
            )
        return self.get(owner_id, current.id)

    def add_source(self, owner_id: str, project_id: str, *, kind: object, label: object, locator: object) -> ProjectSource:
        self.get(owner_id, project_id)
        if kind not in self._SOURCE_KINDS:
            raise ValueError("Project source kind is not supported")
        clean_locator = self._text(locator, "source locator", 2000)
        if kind == "url":
            parsed = urlsplit(clean_locator)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("Project URL sources must be absolute HTTP(S) URLs without credentials")
        if kind == "github_repo" and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", clean_locator):
            raise ValueError("GitHub project sources must use owner/repository")
        source = ProjectSource(uuid.uuid4().hex, project_id, kind, self._text(label, "source label", 160), clean_locator, self._now())
        with self._connect() as connection:
            connection.execute("INSERT INTO project_sources VALUES (?, ?, ?, ?, ?, ?)", tuple(asdict(source).values()))
            connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (source.created_at, project_id))
        return source

    def sources(self, owner_id: str, project_id: str) -> list[ProjectSource]:
        self.get(owner_id, project_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM project_sources WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [ProjectSource(**dict(row)) for row in rows]

    def link(self, owner_id: str, project_id: str, related_project_id: str, *, relation: object, summary: object = None) -> ProjectLink:
        if project_id == related_project_id:
            raise ValueError("A project cannot link to itself")
        self.get(owner_id, project_id)
        self.get(owner_id, related_project_id)
        if relation not in self._RELATIONS:
            raise ValueError("Project relationship is not supported")
        link = ProjectLink(uuid.uuid4().hex, project_id, related_project_id, relation, self._text(summary, "relationship summary", 500, required=False), self._now())
        with self._connect() as connection:
            connection.execute("INSERT INTO project_links VALUES (?, ?, ?, ?, ?, ?)", tuple(asdict(link).values()))
        return link

    def links(self, owner_id: str, project_id: str) -> list[ProjectLink]:
        self.get(owner_id, project_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM project_links WHERE project_id=? OR related_project_id=? ORDER BY created_at DESC", (project_id, project_id)).fetchall()
        return [ProjectLink(**dict(row)) for row in rows]

    def mark_inspected(self, owner_id: str, project_id: str) -> Project:
        self.get(owner_id, project_id)
        now = self._now()
        with self._connect() as connection:
            connection.execute("UPDATE projects SET inspected_at=?, updated_at=? WHERE id=? AND owner_id=?", (now, now, project_id, owner_id))
        return self.get(owner_id, project_id)
