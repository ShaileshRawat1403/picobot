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


def test_session_search_index_updates_incrementally_on_save(tmp_path: Path):
    sessions = SessionManager(tmp_path / "workspace")
    session = sessions.get_or_create(SESSION_A)
    session.metadata["pico_web_title"] = "Launch planning"
    session.add_message("user", "Prepare the launch brief and decision log")
    sessions.save(session)
    assert [item["key"] for item in sessions.search_sessions("launch")] == [SESSION_A]
    assert sessions.search_sessions("tradeoff") == []
    session.add_message("assistant", "The risk tradeoff is captured")
    sessions.save(session)
    assert [item["key"] for item in sessions.search_sessions("tradeoff")] == [SESSION_A]
    assert [item["key"] for item in sessions.search_sessions("launch")] == [SESSION_A]


def test_session_search_index_is_rebuildable_from_transcripts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sessions = SessionManager(workspace)
    first = sessions.get_or_create(SESSION_A)
    first.metadata["pico_web_title"] = "Launch planning"
    first.add_message("user", "Prepare the launch brief and decision log")
    sessions.save(first)
    second = sessions.get_or_create(SESSION_B)
    second.add_message("user", "Private research notes stay isolated")
    sessions.save(second)
    index = workspace / "sessions" / "session-search.db"
    assert index.exists()
    index.unlink()
    assert sessions.rebuild_search_index() == 2
    assert [item["key"] for item in sessions.search_sessions("launch")] == [SESSION_A]
    assert [item["key"] for item in sessions.search_sessions("notes")] == [SESSION_B]


def test_session_search_self_heals_a_deleted_index(tmp_path: Path):
    workspace = tmp_path / "workspace"
    sessions = SessionManager(workspace)
    session = sessions.get_or_create(SESSION_A)
    session.add_message("user", "alpha decision log")
    sessions.save(session)
    (workspace / "sessions" / "session-search.db").unlink()
    hits = PersonalSearch(workspace).search(OWNER_A, "alpha")
    assert any(item.kind == "session" and item.session_id == "session-a" for item in hits)


def test_session_search_requires_all_terms_and_orders_by_updated_at(tmp_path: Path):
    from datetime import datetime

    sessions = SessionManager(tmp_path / "workspace")
    older = sessions.get_or_create(SESSION_A)
    older.metadata["pico_web_title"] = "First"
    older.add_message("user", "alpha beta")
    older.updated_at = datetime(2026, 1, 1, 12, 0, 0)
    sessions.save(older)
    newer = sessions.get_or_create(SESSION_B)
    newer.metadata["pico_web_title"] = "Second"
    newer.add_message("user", "alpha gamma")
    newer.updated_at = datetime(2026, 2, 1, 12, 0, 0)
    sessions.save(newer)
    assert [item["key"] for item in sessions.search_sessions("alpha beta")] == [SESSION_A]
    assert [item["key"] for item in sessions.search_sessions("alpha gamma")] == [SESSION_B]
    assert [item["key"] for item in sessions.search_sessions("alpha")] == [SESSION_B, SESSION_A]
    assert [item["key"] for item in sessions.search_sessions("alpha", session_prefix="telegram:")] == [SESSION_B]
