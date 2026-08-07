"""Session management for conversation history."""

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from picobot.config.paths import get_legacy_sessions_dir
from picobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a user turn."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Drop leading non-user messages to avoid orphaned tool_result blocks
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                sliced = sliced[i:]
                break

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self._cache: dict[str, Session] = {}

    _SEARCH_SCHEMA = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5("
        "key UNINDEXED, title, content, updated_at UNINDEXED, "
        "tokenize = 'unicode61 remove_diacritics 2')"
    )
    _SEARCH_INDEX_FILENAME = "session-search.db"
    _SEARCH_INDEX_LIMIT = 200

    def _search_index_path(self) -> Path:
        """Sidecar FTS5 mirror. It is never a source of truth."""
        return self.sessions_dir / self._SEARCH_INDEX_FILENAME

    def _search_db(self) -> sqlite3.Connection:
        """Open (and if needed create) the searchable mirror database."""
        path = self._search_index_path()
        try:
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(self._SEARCH_SCHEMA)
        except sqlite3.Error:
            # A corrupt or partial mirror is disposable: drop it and rebuild
            # from the authoritative JSONL transcripts.
            connection.close()
            if path.exists():
                path.unlink()
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute(self._SEARCH_SCHEMA)
        return connection

    @staticmethod
    def _search_title(session: Session) -> str:
        title = session.metadata.get("pico_web_title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        for message in session.messages:
            if message.get("role") == "user" and str(message.get("content", "")).strip():
                return " ".join(str(message["content"]).split())[:72]
        return "Untitled session"

    @classmethod
    def _searchable_text(cls, session: Session) -> tuple[str, str]:
        content = " ".join(
            str(message.get("content", "")).strip()
            for message in session.messages
            if message.get("role") in {"user", "assistant"}
        ).strip()
        return cls._search_title(session), content

    def _index_session(self, session: Session, *, updated_at: str | None = None) -> None:
        title, content = self._searchable_text(session)
        stamp = updated_at or session.updated_at.isoformat()
        with closing(self._search_db()) as connection:
            connection.execute("DELETE FROM session_fts WHERE key = ?", (session.key,))
            connection.execute(
                "INSERT INTO session_fts(key, title, content, updated_at) VALUES (?, ?, ?, ?)",
                (session.key, title, content, stamp),
            )
            connection.commit()

    def rebuild_search_index(self) -> int:
        """Rebuild the searchable mirror from the authoritative JSONL files."""
        with closing(self._search_db()) as connection:
            connection.execute("DELETE FROM session_fts")
            count = 0
            for item in self.list_sessions():
                session = self.get_or_create(item["key"])
                connection.execute(
                    "INSERT INTO session_fts(key, title, content, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        session.key,
                        *self._searchable_text(session),
                        item.get("updated_at") or session.updated_at.isoformat(),
                    ),
                )
                count += 1
            connection.commit()
            return count

    def search_sessions(
        self,
        query: str,
        *,
        limit: int = 20,
        session_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching session keys with bounded text for snippets.

        The mirror is refreshed lazily: if it is empty while transcripts
        exist on disk (deleted or never built), it is rebuilt first.
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Search limit must be an integer")
        limit = max(1, min(limit, self._SEARCH_INDEX_LIMIT))
        if not isinstance(query, str) or not " ".join(query.split()):
            return []
        terms = query.casefold().split()
        with closing(self._search_db()) as connection:
            empty = connection.execute("SELECT count(*) AS n FROM session_fts").fetchone()["n"] == 0
        if empty and next(iter(self.sessions_dir.glob("*.jsonl")), None) is not None:
            try:
                self.rebuild_search_index()
            except sqlite3.Error:
                return []
        with closing(self._search_db()) as connection:
            match = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms)
            try:
                rows = connection.execute(
                    "SELECT key, title, content, updated_at FROM session_fts "
                    "WHERE session_fts MATCH ?",
                    (match,),
                ).fetchall()
            except sqlite3.Error:
                return []
        results: list[dict[str, Any]] = []
        for row in rows:
            key = row["key"]
            if session_prefix is not None and not key.startswith(session_prefix):
                continue
            results.append(
                {
                    "key": key,
                    "title": row["title"],
                    "content": row["content"],
                    "updated_at": row["updated_at"],
                }
            )
        results.sort(key=lambda item: item["updated_at"] or "", reverse=True)
        return results[:limit]

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.picobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk and refresh its searchable mirror row."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session
        try:
            self._index_session(session)
        except (sqlite3.Error, OSError):
            # The mirror is disposable; the JSONL transcript remains the source
            # of truth, so a failed index write must never break persistence.
            logger.warning("Failed to refresh search index for session {}", session.key)

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
