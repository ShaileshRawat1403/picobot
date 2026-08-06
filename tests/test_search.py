from pathlib import Path

import pytest

from picobot.artifacts.store import ArtifactStore
from picobot.memory.store import PersonalMemoryStore
from picobot.search import PersonalSearch
from picobot.session.manager import SessionManager


OWNER_A = "local:owner"
OWNER_B = "telegram:987654321"
SESSION_A = "web:web:session-a"
SESSION_B = "telegram:chat-b"


def test_search_returns_owner_scoped_hits_with_inspectable_sources(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sessions = SessionManager(workspace)
    session = sessions.get_or_create(SESSION_A)
    session.metadata["pico_web_title"] = "Launch planning"
    session.add_message("user", "Prepare the launch brief and decision log")
    sessions.save(session)
    other = sessions.get_or_create(SESSION_B)
    other.add_message("user", "Launch brief for another identity")
    sessions.save(other)

    memory = PersonalMemoryStore(workspace)
    memory.remember(OWNER_A, "The launch review is Friday")
    memory.remember(OWNER_B, "The launch review is private")
    artifact = ArtifactStore(workspace).create(
        owner_id=OWNER_A,
        session_key=SESSION_A,
        title="Launch brief",
        content="Decision log and launch risks.",
        kind="brief",
    )

    results = PersonalSearch(workspace).search(
        OWNER_A,
        "launch",
        limit=20,
    )

    assert {item.kind for item in results} == {"session", "memory", "artifact"}
    assert all(item.id != other.key for item in results if item.kind == "session")
    assert all("private" not in item.snippet.lower() for item in results)
    artifact_result = next(item for item in results if item.kind == "artifact")
    assert artifact_result.id == artifact.id
    assert artifact_result.href == f"/api/artifacts/{artifact.id}"
    session_result = next(item for item in results if item.kind == "session")
    assert session_result.session_id == "session-a"
    assert session_result.href == "/api/sessions/session-a"


def test_search_requires_a_nonempty_bounded_query(tmp_path: Path):
    search = PersonalSearch(tmp_path / "workspace")
    with pytest.raises(ValueError, match="query is required"):
        search.search(OWNER_A, "   ")
    with pytest.raises(ValueError, match="limited"):
        search.search(OWNER_A, "x" * 201)


def test_search_does_not_treat_punctuation_as_a_match(tmp_path: Path):
    memory = PersonalMemoryStore(tmp_path / "workspace")
    memory.remember(OWNER_A, "A useful confirmed fact")

    assert PersonalSearch(tmp_path / "workspace").search(OWNER_A, "!!!") == []
