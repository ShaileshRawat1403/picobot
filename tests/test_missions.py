from pathlib import Path
import sqlite3

import pytest

from types import SimpleNamespace

from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.missions import MissionStore
from picobot.runs import RunStore
from picobot.session.manager import SessionManager


OWNER_A = "web:browser:owner-a"
OWNER_B = "web:browser:owner-b"
SESSION_A = "web:web:owner-a:session-a"


def _create(store: MissionStore, **overrides):
    values = {
        "owner_id": OWNER_A,
        "session_key": SESSION_A,
        "title": "Prepare the article brief",
        "objective": "Research the topic, prepare a concise brief, and preserve decisions.",
    }
    values.update(overrides)
    return store.create(**values)


def test_missions_persist_across_store_reloads_and_return_safe_dicts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    created = _create(MissionStore(workspace), current_step="Collect the primary sources")

    reloaded = MissionStore(workspace)
    mission = reloaded.get(OWNER_A, created.id)

    assert reloaded.path == workspace / "missions" / "pico-missions.db"
    assert mission.id == created.id
    assert mission.state == "draft"
    assert mission.current_step == "Collect the primary sources"
    assert mission.to_dict() == {
        "id": mission.id,
        "owner_id": OWNER_A,
        "session_key": SESSION_A,
        "title": "Prepare the article brief",
        "objective": "Research the topic, prepare a concise brief, and preserve decisions.",
        "state": "draft",
        "current_step": "Collect the primary sources",
        "blocked_reason": None,
        "created_at": mission.created_at,
        "updated_at": mission.updated_at,
        "completed_at": None,
        "cancelled_at": None,
    }


def test_missions_are_owner_scoped_in_get_and_list(tmp_path: Path):
    store = MissionStore(tmp_path / "workspace")
    mission_a = _create(store)
    mission_b = _create(store, owner_id=OWNER_B, session_key="web:web:owner-b:session-b", title="Private B mission")

    assert [mission.id for mission in store.list(OWNER_A)] == [mission_a.id]
    assert [mission.id for mission in store.list(OWNER_B)] == [mission_b.id]
    assert [mission.id for mission in store.list(OWNER_A, session_key=SESSION_A)] == [mission_a.id]
    assert store.list(OWNER_A, session_key="web:web:owner-a:another-session") == []
    with pytest.raises(KeyError, match="not found"):
        store.get(OWNER_B, mission_a.id)
    with pytest.raises(KeyError, match="not found"):
        store.transition(OWNER_B, mission_a.id, "active")


def test_mission_state_machine_idempotency_and_terminal_behavior(tmp_path: Path):
    store = MissionStore(tmp_path / "workspace")
    mission = _create(store)

    assert store.transition(OWNER_A, mission.id, "draft") == mission
    active = store.transition(OWNER_A, mission.id, "active")
    with pytest.raises(ValueError, match="blocked reason is required"):
        store.transition(OWNER_A, mission.id, "blocked")
    blocked = store.transition(OWNER_A, mission.id, "blocked", "Waiting for source access")
    assert blocked.blocked_reason == "Waiting for source access"
    assert store.transition(OWNER_A, mission.id, "blocked", "Source access still pending").blocked_reason == "Source access still pending"
    resumed = store.transition(OWNER_A, mission.id, "active")
    assert resumed.blocked_reason is None
    completed = store.transition(OWNER_A, mission.id, "completed")
    assert completed.completed_at is not None
    assert store.transition(OWNER_A, mission.id, "completed") == completed

    with pytest.raises(ValueError, match="terminal"):
        store.set_current_step(OWNER_A, mission.id, "A late change")
    with pytest.raises(ValueError, match="terminal"):
        store.add_checkpoint(OWNER_A, mission.id, "note", "A late note")

    for state in ("active", "blocked", "cancelled"):
        with pytest.raises(ValueError, match="cannot transition"):
            store.transition(OWNER_A, mission.id, state)

    cancelled = _create(store, title="Cancel me")
    cancelled = store.transition(OWNER_A, cancelled.id, "cancelled")
    assert cancelled.cancelled_at is not None
    with pytest.raises(ValueError, match="cannot transition"):
        store.transition(OWNER_A, cancelled.id, "active")

    invalid = _create(store, title="Invalid direction")
    with pytest.raises(ValueError, match="cannot transition"):
        store.transition(OWNER_A, invalid.id, "completed")


def test_checkpoints_are_owner_scoped_and_mission_scoped(tmp_path: Path):
    store = MissionStore(tmp_path / "workspace")
    first = _create(store)
    second = _create(store, title="Other mission")
    checkpoint = store.add_checkpoint(OWNER_A, first.id, "decision", "Use primary sources first.")
    store.add_checkpoint(OWNER_A, second.id, "note", "This belongs to another mission.")

    assert checkpoint.to_dict()["mission_id"] == first.id
    assert [item.id for item in store.checkpoints(OWNER_A, first.id)] == [checkpoint.id]
    with pytest.raises(KeyError, match="not found"):
        store.add_checkpoint(OWNER_B, first.id, "note", "Attempted cross-owner note")
    with pytest.raises(KeyError, match="not found"):
        store.checkpoints(OWNER_B, first.id)


def test_mission_events_are_durable_and_record_lifecycle_changes(tmp_path: Path):
    store = MissionStore(tmp_path / "workspace")
    mission = _create(store)
    store.set_current_step(OWNER_A, mission.id, "Read the primary source")
    store.transition(OWNER_A, mission.id, "active")
    store.transition(OWNER_A, mission.id, "blocked", "Waiting for access")

    events = store.events(OWNER_A, mission.id)

    assert [event.event_type for event in events] == [
        "state_changed",
        "state_changed",
        "current_step_updated",
        "created",
    ]
    assert all(event.owner_id == OWNER_A for event in events)
    with pytest.raises(KeyError, match="not found"):
        store.events(OWNER_B, mission.id)


def test_browser_mission_helpers_derive_owner_and_require_saved_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    client_a = "browser_identity_0001"
    client_b = "browser_identity_0002"
    session_a = "session_identity_0001"
    session_b = "session_identity_0002"
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(client_a, session_a)))
    sessions.save(sessions.get_or_create(channel._session_key(client_b, session_b)))

    created = channel._create_browser_mission(
        client_a,
        session_a,
        "Review personal workflow",
        "Keep the outcome and decisions in one local record.",
        "Start with the current friction.",
    )
    linked_run = RunStore(workspace).create(
        owner_id=channel._memory_owner(client_a),
        session_key=channel._session_key(client_a, session_a),
        capability_profile="personal-work",
        mission_id=created["id"],
    )

    assert created["owner_id"] == channel._memory_owner(client_a)
    assert created["session_key"] == channel._session_key(client_a, session_a)
    assert [item["id"] for item in channel._list_browser_missions(client_a, session_id=session_a)] == [
        created["id"]
    ]
    with pytest.raises(ValueError, match="Session was not found"):
        channel._list_browser_missions(client_a, session_id=session_b)
    with pytest.raises(KeyError, match="not found"):
        channel._browser_mission_detail(client_b, session_b, created["id"])
    with pytest.raises(ValueError, match="Session was not found"):
        channel._create_browser_mission(
            client_a,
            "missing_session_0001",
            "Not allowed",
            "This must never create an arbitrary session.",
            None,
        )

    detail = channel._browser_mission_detail(client_a, session_a, created["id"])
    assert detail["evidence"] == {"artifact_count": 0, "activity_count": 0, "checkpoint_count": 0, "run_count": 1}
    assert detail["runs"][0]["run_id"] == linked_run.id
    assert "id" not in detail["runs"][0]
    assert [event["event_type"] for event in detail["events"]] == ["created"]
    assert detail["resume_brief"] == {
        "outcome": created["objective"],
        "current_step": created["current_step"],
        "state": "draft",
        "blocker": None,
        "last_checkpoint": None,
        "active_task": None,
        "pending_approvals": 0,
    }


def test_validation_and_bounds_are_enforced(tmp_path: Path):
    store = MissionStore(tmp_path / "workspace")
    with pytest.raises(ValueError, match="title is required"):
        _create(store, title=" \n ")
    with pytest.raises(ValueError, match="title is limited"):
        _create(store, title="x" * 161)
    with pytest.raises(ValueError, match="objective is required"):
        _create(store, objective="")
    with pytest.raises(ValueError, match="objective is limited"):
        _create(store, objective="x" * 8_001)
    with pytest.raises(ValueError, match="current step is limited"):
        _create(store, current_step="x" * 2_001)

    mission = _create(store)
    with pytest.raises(ValueError, match="blocked reason"):
        store.transition(OWNER_A, mission.id, "blocked", "x" * 2_001)
    with pytest.raises(ValueError, match="only allowed for blocked"):
        store.transition(OWNER_A, mission.id, "active", "No reason here")
    with pytest.raises(ValueError, match="Unsupported mission state"):
        store.list(OWNER_A, state="unknown")
    with pytest.raises(ValueError, match="list limit"):
        store.list(OWNER_A, limit=101)
    with pytest.raises(ValueError, match="checkpoint kind"):
        store.add_checkpoint(OWNER_A, mission.id, "trace", "A summary")
    with pytest.raises(ValueError, match="checkpoint summary"):
        store.add_checkpoint(OWNER_A, mission.id, "note", "x" * 2_001)
    with pytest.raises(ValueError, match="list limit"):
        store.checkpoints(OWNER_A, mission.id, limit=0)


def test_existing_mission_database_migrates_for_exact_run_event_deduplication(tmp_path: Path):
    """CH5's additive column must not strand an existing local workspace."""
    workspace = tmp_path / "workspace"
    mission_root = workspace / "missions"
    mission_root.mkdir(parents=True)
    path = mission_root / "pico-missions.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE mission_events (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    store = MissionStore(workspace)
    with store._connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(mission_events)").fetchall()
        }
    assert "source_run_id" in columns
