"""Small, inspectable search over Pico's durable local work products."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from picobot.artifacts.store import ArtifactStore
from picobot.memory.store import PersonalMemoryStore
from picobot.session.manager import SessionManager


@dataclass(frozen=True)
class SearchResult:
    """A bounded search hit with enough provenance to inspect its source."""

    kind: str
    id: str
    title: str
    snippet: str
    updated_at: str | None
    session_id: str | None = None
    status: str | None = None
    href: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonalSearch:
    """Search sessions, memory, and artifacts without crossing owners."""

    _MAX_QUERY_LENGTH = 200
    _MAX_LIMIT = 50
    _MAX_SNIPPET_LENGTH = 240

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions = SessionManager(workspace)
        self.memory = PersonalMemoryStore(workspace)
        self.artifacts = ArtifactStore(workspace)

    @classmethod
    def _query_terms(cls, query: str) -> list[str]:
        if not isinstance(query, str):
            raise ValueError("Search query is required")
        clean = " ".join(query.split())
        if not clean:
            raise ValueError("Search query is required")
        if len(clean) > cls._MAX_QUERY_LENGTH:
            raise ValueError(f"Search query is limited to {cls._MAX_QUERY_LENGTH} characters")
        return clean.casefold().split()

    @classmethod
    def _limit(cls, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Search limit must be an integer")
        return max(1, min(limit, cls._MAX_LIMIT))

    @classmethod
    def _snippet(cls, text: str, terms: list[str]) -> str:
        clean = " ".join(str(text).split())
        if len(clean) <= cls._MAX_SNIPPET_LENGTH:
            return clean
        lowered = clean.casefold()
        positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
        start = max(0, (min(positions) if positions else 0) - 70)
        excerpt = clean[start : start + cls._MAX_SNIPPET_LENGTH]
        if start > 0:
            excerpt = "…" + excerpt
        if start + cls._MAX_SNIPPET_LENGTH < len(clean):
            excerpt += "…"
        return excerpt

    @staticmethod
    def _matches(text: str, terms: list[str]) -> bool:
        lowered = text.casefold()
        return all(term in lowered for term in terms)

    @staticmethod
    def _session_title(session) -> str:
        title = session.metadata.get("pico_web_title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        for message in session.messages:
            if message.get("role") == "user" and str(message.get("content", "")).strip():
                return " ".join(str(message["content"]).split())[:72]
        return "Untitled session"

    def _session_results(
        self, owner_id: str, terms: list[str], session_prefix: str | None
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in self.sessions.list_sessions():
            key = item["key"]
            if session_prefix is not None and not key.startswith(session_prefix):
                continue
            session = self.sessions.get_or_create(key)
            title = self._session_title(session)
            visible_messages = [
                str(message.get("content", ""))
                for message in session.messages
                if message.get("role") in {"user", "assistant"}
            ]
            searchable = " ".join([title, *visible_messages])
            if not self._matches(searchable, terms):
                continue
            session_id = key.rsplit(":", 1)[-1]
            results.append(
                SearchResult(
                    kind="session",
                    id=session_id,
                    title=title,
                    snippet=self._snippet(searchable, terms),
                    updated_at=item.get("updated_at"),
                    session_id=session_id,
                    href=f"/api/sessions/{session_id}",
                )
            )
        return results

    @staticmethod
    def _default_session_prefix(owner_id: str) -> str | None:
        """Derive the web session namespace without trusting a browser value."""
        marker = "web:browser:"
        if owner_id.startswith(marker) and owner_id.removeprefix(marker):
            return f"web:web:{owner_id.removeprefix(marker)}:"
        return None

    def _memory_results(
        self, owner_id: str, query: str, terms: list[str], limit: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in self.memory.search(owner_id, query, limit=limit):
            if not self._matches(item.value, terms):
                continue
            results.append(
                SearchResult(
                    kind="memory",
                    id=item.id,
                    title=item.value[:96],
                    snippet=item.value,
                    updated_at=item.updated_at,
                    status=item.status,
                    href=f"/api/memory/{item.id}",
                )
            )
        return results

    def _artifact_results(self, owner_id: str, terms: list[str], limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for artifact in self.artifacts.list(owner_id, limit=100):
            try:
                content = self.artifacts.read_content(owner_id, artifact.id)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            searchable = " ".join([artifact.title, content])
            if not self._matches(searchable, terms):
                continue
            results.append(
                SearchResult(
                    kind="artifact",
                    id=artifact.id,
                    title=artifact.title,
                    snippet=self._snippet(content or artifact.title, terms),
                    updated_at=artifact.updated_at,
                    session_id=artifact.session_key.rsplit(":", 1)[-1],
                    status=artifact.status,
                    href=f"/api/artifacts/{artifact.id}",
                )
            )
            if len(results) >= limit:
                break
        return results

    def search(
        self,
        owner_id: str,
        query: str,
        *,
        limit: int = 20,
        session_prefix: str | None = None,
    ) -> list[SearchResult]:
        """Return newest bounded hits from all durable personal sources."""
        terms = self._query_terms(query)
        limit = self._limit(limit)
        if session_prefix is None:
            session_prefix = self._default_session_prefix(owner_id)
        results = self._session_results(owner_id, terms, session_prefix)
        results.extend(self._memory_results(owner_id, query, terms, limit))
        results.extend(self._artifact_results(owner_id, terms, limit))
        results.sort(key=lambda item: item.updated_at or "", reverse=True)
        return results[:limit]
