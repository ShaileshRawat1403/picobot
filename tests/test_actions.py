from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from picobot.operations.actions import ProposedActionStore


def _stage(store: ProposedActionStore):
    return store.stage(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-review",
        capability_id="browser.form_submit",
        tool_name="browser_submit",
        target="example.com",
        summary="Submit the reviewed contact form on example.com.",
    )


def test_actions_require_exact_owner_session_approval_and_do_not_store_payloads(tmp_path: Path):
    store = ProposedActionStore(tmp_path / "workspace")
    action = _stage(store)

    assert action.status == "proposed"
    assert store.list("web:browser:owner-a", "web:web:owner-a:session-a") == [action]
    assert store.list("web:browser:owner-b", "web:web:owner-a:session-a") == []
    # payload field exists on the dataclass but must be None (never populated for simple stage)
    # and must NOT appear in the public to_dict() projection
    assert action.payload is None
    assert "payload" not in action.to_dict()

    with pytest.raises(KeyError, match="not found"):
        store.resolve("web:browser:owner-a", action.id, "web:web:owner-a:other-session", "approve")

    approved = store.resolve("web:browser:owner-a", action.id, action.session_key, "approve")
    assert approved.status == "approved"
    assert approved.resolved_at is not None

    with pytest.raises(ValueError, match="Only proposed"):
        store.resolve("web:browser:owner-a", action.id, action.session_key, "reject")

    executed = store.mark_execution("web:browser:owner-a", action.id, action.session_key, success=True)
    assert executed.status == "executed"
    assert executed.executed_at is not None


def test_rejected_expired_and_invalid_actions_cannot_execute(tmp_path: Path):
    store = ProposedActionStore(tmp_path / "workspace")
    rejected = _stage(store)
    store.resolve(rejected.owner_id, rejected.id, rejected.session_key, "reject")
    with pytest.raises(ValueError, match="Only an approved"):
        store.mark_execution(rejected.owner_id, rejected.id, rejected.session_key, success=True)

    expired = store.stage(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-review",
        capability_id="browser.form_submit",
        tool_name="browser_submit",
        target="example.com",
        summary="Submit a form.",
        expires_in=timedelta(microseconds=1),
    )
    assert store.list(expired.owner_id, expired.session_key)[0].status == "expired"
    with pytest.raises(ValueError, match="Only proposed"):
        store.resolve(expired.owner_id, expired.id, expired.session_key, "approve")

    with pytest.raises(ValueError, match="Action target"):
        store.stage(
            owner_id="owner",
            session_key="session",
            profile_id="profile",
            capability_id="capability",
            tool_name="tool",
            target="",
            summary="Summary",
        )
    with pytest.raises(ValueError, match="between now and 24 hours"):
        store.stage(
            owner_id="owner",
            session_key="session",
            profile_id="profile",
            capability_id="capability",
            tool_name="tool",
            target="target",
            summary="Summary",
            expires_in=timedelta(hours=25),
        )


def test_existing_action_database_migrates_before_creating_mission_index(tmp_path: Path):
    """A pre-mission local ledger must still open without manual database repair."""
    workspace = tmp_path / "workspace"
    actions_root = workspace / "operations"
    actions_root.mkdir(parents=True)
    path = actions_root / "pico-actions.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE proposed_actions (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                capability_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                target TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                resolved_at TEXT,
                executed_at TEXT
            )
            """
        )

    store = ProposedActionStore(workspace)

    with store._connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(proposed_actions)")}
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(proposed_actions)")}
    assert {"mission_id", "payload", "initiating_run_id"}.issubset(columns)
    assert "proposed_actions_mission_idx" in indexes
