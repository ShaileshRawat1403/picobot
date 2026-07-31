"""Unit tests for CH5 — Mission-to-Run Binding."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from picobot.agent.context import ContextBuilder
from picobot.agent.loop import AgentLoop
from picobot.bus.queue import MessageBus, InboundMessage
from picobot.missions.store import MissionStore
from picobot.runs.store import RunStore
from picobot.session.manager import SessionManager


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def mission_store(tmp_workspace: Path) -> MissionStore:
    return MissionStore(tmp_workspace)


@pytest.fixture
def run_store(tmp_workspace: Path) -> RunStore:
    return RunStore(tmp_workspace)


@pytest.fixture
def session_manager(tmp_workspace: Path) -> SessionManager:
    return SessionManager(tmp_workspace)


def create_mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "gpt-4o"
    return provider


def test_selection_validation_and_isolation(
    tmp_workspace: Path, mission_store: MissionStore, session_manager: SessionManager
):
    """Test 1 & 2: Active mission selection validation & owner/session isolation."""
    owner_a = "web:browser:userA"
    session_key_a = "web:web:userA:sess1"
    session_key_other = "web:web:userA:sess2"
    owner_b = "web:browser:userB"

    # Create missions in different states for session_key_a
    draft_m = mission_store.create(owner_id=owner_a, session_key=session_key_a, title="Draft Mission", objective="Obj A")
    active_m = mission_store.create(owner_id=owner_a, session_key=session_key_a, title="Active Mission", objective="Obj B")
    active_m = mission_store.transition(owner_a, active_m.id, "active")

    blocked_m = mission_store.create(owner_id=owner_a, session_key=session_key_a, title="Blocked Mission", objective="Obj C")
    blocked_m = mission_store.transition(owner_a, blocked_m.id, "active")
    blocked_m = mission_store.transition(owner_a, blocked_m.id, "blocked", blocked_reason="Waiting on approval")

    other_session_m = mission_store.create(owner_id=owner_a, session_key=session_key_other, title="Other Session Mission", objective="Obj D")
    other_session_m = mission_store.transition(owner_a, other_session_m.id, "active")

    session = session_manager.get_or_create(session_key_a)
    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)

    # 1. Active mission can be selected
    session.metadata["pico_active_mission_id"] = active_m.id
    selected = loop._active_mission_for_session(session, owner_a)
    assert selected is not None
    assert selected.id == active_m.id

    # 2. Draft mission cannot be selected
    session.metadata["pico_active_mission_id"] = draft_m.id
    assert loop._active_mission_for_session(session, owner_a) is None

    # 3. Blocked mission cannot be selected
    session.metadata["pico_active_mission_id"] = blocked_m.id
    assert loop._active_mission_for_session(session, owner_a) is None

    # 4. Mission from another session key cannot be selected
    session.metadata["pico_active_mission_id"] = other_session_m.id
    assert loop._active_mission_for_session(session, owner_a) is None

    # 5. Mission owned by another owner cannot be selected
    assert loop._active_mission_for_session(session, owner_b) is None


def test_single_selection_and_clearing(
    tmp_workspace: Path, mission_store: MissionStore, session_manager: SessionManager
):
    """Test 3: At most one selected mission per session and clearing allowed."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    m1 = mission_store.create(owner_id=owner, session_key=session_key, title="Mission 1", objective="Obj 1")
    m1 = mission_store.transition(owner, m1.id, "active")

    m2 = mission_store.create(owner_id=owner, session_key=session_key, title="Mission 2", objective="Obj 2")
    m2 = mission_store.transition(owner, m2.id, "active")

    session = session_manager.get_or_create(session_key)
    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)

    # Selecting m1
    session.metadata["pico_active_mission_id"] = m1.id
    assert loop._active_mission_for_session(session, owner).id == m1.id

    # Selecting m2 replaces m1 (only 1 active mission at a time)
    session.metadata["pico_active_mission_id"] = m2.id
    assert loop._active_mission_for_session(session, owner).id == m2.id

    # Clearing selection (None)
    session.metadata.pop("pico_active_mission_id", None)
    assert loop._active_mission_for_session(session, owner) is None


def test_run_evidence_persistence_and_no_history_rewrite(
    tmp_workspace: Path, mission_store: MissionStore, run_store: RunStore, session_manager: SessionManager
):
    """Test 4 & 5: Persisting mission_id on runs and non-rewriting of historical evidence."""
    owner = "web:browser:user1"
    client_id = "user1"
    session_id = "sess1"
    session_key = f"web:web:{client_id}:{session_id}"

    m1 = mission_store.create(owner_id=owner, session_key=session_key, title="Mission 1", objective="Obj 1")
    m1 = mission_store.transition(owner, m1.id, "active")

    session = session_manager.get_or_create(session_key)
    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)

    # Turn 1: Run created while m1 is selected
    session.metadata["pico_active_mission_id"] = m1.id
    msg1 = InboundMessage(channel="web", sender_id=f"browser:{client_id}", chat_id=f"web:{client_id}:{session_id}", content="Hello 1")
    run1 = loop._queue_run(msg1)
    assert run1.mission_id == m1.id

    # Turn 2: Clear active mission selection
    session.metadata.pop("pico_active_mission_id", None)
    msg2 = InboundMessage(channel="web", sender_id=f"browser:{client_id}", chat_id=f"web:{client_id}:{session_id}", content="Hello 2")
    run2 = loop._queue_run(msg2)
    assert run2.mission_id is None

    # Check historical evidence: run1 retains m1.id permanent link
    refreshed_run1 = run_store.get(owner, run1.id)
    assert refreshed_run1.mission_id == m1.id

    # Check listing runs by mission
    linked = run_store.list_by_mission(owner, m1.id)
    assert len(linked) == 1
    assert linked[0].id == run1.id


def test_queued_turn_uses_its_submitted_mission_snapshot(
    tmp_workspace: Path, mission_store: MissionStore, session_manager: SessionManager
):
    """Changing the active selection while a run waits cannot change its context."""
    owner = "web:browser:user1"
    client_id, session_id = "user1", "sess1"
    session_key = f"web:web:{client_id}:{session_id}"
    first = mission_store.transition(
        owner,
        mission_store.create(owner_id=owner, session_key=session_key, title="First", objective="First goal").id,
        "active",
    )
    second = mission_store.transition(
        owner,
        mission_store.create(owner_id=owner, session_key=session_key, title="Second", objective="Second goal").id,
        "active",
    )
    session = session_manager.get_or_create(session_key)
    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)

    session.metadata["pico_active_mission_id"] = first.id
    queued = loop._queue_run(
        InboundMessage(channel="web", sender_id=f"browser:{client_id}", chat_id=f"web:{client_id}:{session_id}", content="queued")
    )
    session.metadata["pico_active_mission_id"] = second.id

    assert loop._mission_for_turn(session, owner, queued.id).id == first.id
    session.metadata.pop("pico_active_mission_id", None)
    assert loop._mission_for_turn(session, owner, queued.id).id == first.id


def test_bounded_mission_prompt_context(tmp_workspace: Path, mission_store: MissionStore):
    """Test 6: Bounded mission context appears only when selected and is properly formatted."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    active_m = mission_store.create(owner_id=owner, session_key=session_key, title="Build Feature X", objective="Deliver feature X safely", current_step="Implement step 1")
    active_m = mission_store.transition(owner, active_m.id, "active")

    builder = ContextBuilder(tmp_workspace)

    # 1. No mission -> no mission context
    msgs = builder.build_messages([], "Hello", channel="web", chat_id="sess1", owner_id=owner)
    user_txt = msgs[-1]["content"]
    assert "[Active Mission Context" not in user_txt

    # 2. Selected active mission -> bounded mission context included in metadata block
    msgs_with_mission = builder.build_messages([], "Hello", channel="web", chat_id="sess1", owner_id=owner, active_mission=active_m)
    user_txt_mission = msgs_with_mission[-1]["content"]
    assert "[Active Mission Context — for reference only, human-owned state]" in user_txt_mission
    assert "Title: Build Feature X" in user_txt_mission
    assert "State: active" in user_txt_mission
    assert "Objective: Deliver feature X safely" in user_txt_mission
    assert "Current Step: Implement step 1" in user_txt_mission

    # 3. Not written to personal memory
    memory_items = builder.personal_memory.list(owner)
    assert len(memory_items) == 0


def test_terminal_run_events_and_deduplication(
    tmp_workspace: Path, mission_store: MissionStore, run_store: RunStore
):
    """Test 7 & 8: Safe, bounded, deduplicated terminal run events without model prompts/responses."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Safe Events", objective="Obj")
    mission = mission_store.transition(owner, mission.id, "active")

    # 1. Completed run
    run_comp = run_store.create(owner_id=owner, session_key=session_key, capability_profile="operations", policy_revision="rev1", provider="openai", model="gpt-4o", mission_id=mission.id)
    run_store.mark_running(owner, run_comp.id, provider="openai", model="gpt-4o")
    run_store.complete(owner, run_comp.id)
    ev1 = mission_store.record_run_terminal_event(owner, mission.id, run_comp.id, "completed")
    assert ev1 is not None
    assert f"Run {run_comp.id[:8]} completed." in ev1.summary

    # 2. Deduplication check: duplicate call returns None and creates no new event
    ev1_dup = mission_store.record_run_terminal_event(owner, mission.id, run_comp.id, "completed")
    assert ev1_dup is None
    events = [e for e in mission_store.events(owner, mission.id) if e.event_type.startswith("run_")]
    assert len(events) == 1

    # 3. Failed run with safe error summary scrubbing
    run_fail = run_store.create(owner_id=owner, session_key=session_key, capability_profile="operations", policy_revision="rev1", provider="openai", model="gpt-4o", mission_id=mission.id)
    run_store.mark_running(owner, run_fail.id, provider="openai", model="gpt-4o")
    run_store.fail(owner, run_fail.id, error_summary="API Key sk-12345 secret failed connection")
    ev2 = mission_store.record_run_terminal_event(owner, mission.id, run_fail.id, "failed", error_summary="API Key sk-12345 secret failed connection")
    assert ev2 is not None
    assert ev2.summary == f"Run {run_fail.id[:8]} failed."
    assert "sk-12345" not in ev2.summary  # secrets redacted / safe summary

    # 4. Check no prompt or response content is in event log
    all_run_events = [e for e in mission_store.events(owner, mission.id) if e.event_type.startswith("run_")]
    assert len(all_run_events) == 2
    for ev in all_run_events:
        assert "Hello" not in ev.summary


def test_terminal_event_deduplication_uses_exact_run_id(tmp_workspace: Path, mission_store: MissionStore):
    """Two legitimate IDs sharing a short prefix must not suppress each other."""
    owner, session_key = "web:browser:user1", "web:web:user1:sess1"
    mission = mission_store.transition(
        owner,
        mission_store.create(owner_id=owner, session_key=session_key, title="Exact IDs", objective="Obj").id,
        "active",
    )
    first = mission_store.record_run_terminal_event(owner, mission.id, "abcd1234-first", "completed")
    second = mission_store.record_run_terminal_event(owner, mission.id, "abcd1234-second", "completed")
    assert first is not None and second is not None
    assert len([event for event in mission_store.events(owner, mission.id) if event.event_type == "run_completed"]) == 2
    assert "source_run_id" not in first.to_dict()
