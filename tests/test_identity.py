"""Regression tests for the unified local owner identity."""

import json
from types import SimpleNamespace

import pytest

from picobot.artifacts.store import ArtifactStore
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.config.identity import (
    LOCAL_OWNER_ID,
    migrate_workspace_identity,
    resolve_owner_id,
)
from picobot.memory.store import PersonalMemoryStore
from picobot.session.manager import SessionManager

CLIENT_A = "browser_identity_0001"
SESSION_A = "session_identity_0001"
OLD_KEY = "web:web:browser_identity_0001:session_identity_0001"
OLD_FILENAME = "web_web_browser_identity_0001_session_identity_0001.jsonl"
NEW_FILENAME = "web_web_session_identity_0001.jsonl"


def _write_session(path, key, messages):
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "_type": "metadata",
        "key": key,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:01",
        "metadata": {},
        "last_consolidated": 0,
    }
    body = [json.dumps(metadata, ensure_ascii=False)]
    body.extend(json.dumps(message, ensure_ascii=False) for message in messages)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def _old_session(workspace, messages):
    sessions = workspace / "sessions"
    path = sessions / OLD_FILENAME
    _write_session(path, OLD_KEY, messages)
    return path


def test_resolve_owner_id_collapses_local_surfaces_and_keeps_remote_distinct():
    assert resolve_owner_id("web", "browser-anything") == LOCAL_OWNER_ID
    assert resolve_owner_id("cli", "terminal-user") == LOCAL_OWNER_ID
    assert resolve_owner_id("web", "whoever") == resolve_owner_id("cli", "whoever")

    telegram = resolve_owner_id("telegram", "987654321")
    slack = resolve_owner_id("slack", "C0")
    assert telegram == "telegram:987654321"
    assert slack == "slack:C0"
    assert telegram != LOCAL_OWNER_ID
    assert slack != LOCAL_OWNER_ID
    assert telegram != slack


def test_migrate_workspace_identity_is_idempotent_and_preserves_remote_owners(tmp_path):
    workspace = tmp_path / "workspace"
    memory = PersonalMemoryStore(workspace)
    memory.remember("web:browser:aaaa", "Legacy web fact")
    memory.remember("cli:terminal-user", "Legacy cli fact")
    memory.remember("telegram:987654321", "Remote telegram fact")
    memory.remember("slack:C0", "Remote slack fact")

    artifacts = ArtifactStore(workspace)
    artifact = artifacts.create(
        owner_id="web:browser:bbbb",
        session_key="web:web:client_identity_0001:session_identity_0001",
        title="Legacy artifact",
        content="Decision log and launch risks.",
        kind="brief",
    )

    changed = migrate_workspace_identity(workspace)
    assert changed, "migration must report rewritten rows"

    local_facts = {item.value for item in memory.list(LOCAL_OWNER_ID)}
    assert local_facts == {"Legacy web fact", "Legacy cli fact"}
    assert {item.value for item in memory.list("web:browser:aaaa")} == set()
    assert {item.value for item in memory.list("cli:terminal-user")} == set()

    remote = {item.value for item in memory.list("telegram:987654321")}
    assert remote == {"Remote telegram fact"}
    assert {item.value for item in memory.list("slack:C0")} == {"Remote slack fact"}

    migrated = ArtifactStore(workspace).list(LOCAL_OWNER_ID)
    assert {item.id for item in migrated} == {artifact.id}
    assert migrated[0].session_key == "web:web:session_identity_0001"

    assert migrate_workspace_identity(workspace) == {}
    assert migrate_workspace_identity(workspace, force=True) == {}


def test_fts_recall_still_matches_after_migration(tmp_path):
    workspace = tmp_path / "workspace"
    memory = PersonalMemoryStore(workspace)
    legacy_item = memory.remember("web:browser:aaaa", "The launch review is Friday")

    changed = migrate_workspace_identity(workspace)
    assert changed

    migrated = memory.search(LOCAL_OWNER_ID, "launch")
    assert {item.id for item in migrated} == {legacy_item.id}
    assert {item.value for item in migrated} == {"The launch review is Friday"}

    assert memory.search("web:browser:aaaa", "launch") == []


def test_legacy_web_session_file_is_rekeyed_in_place(tmp_path):
    workspace = tmp_path / "workspace"
    messages = [
        {"role": "user", "content": "Plan the launch review"},
        {"role": "assistant", "content": "Here is the checklist"},
    ]
    _old_session(workspace, messages)

    changed = migrate_workspace_identity(workspace)

    assert changed[f"sessions/{OLD_FILENAME}"] == 1
    assert not (workspace / "sessions" / OLD_FILENAME).exists()
    assert (workspace / "sessions" / NEW_FILENAME).exists()

    with (workspace / "sessions" / NEW_FILENAME).open(encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    header = json.loads(lines[0])
    assert header["key"] == "web:web:session_identity_0001"
    assert [json.loads(line) for line in lines[1:]] == messages


def test_unversioned_marker_still_runs_transcript_step(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".owner-identity-migrated").write_text(
        "Local web and CLI identities unified.\n", encoding="utf-8"
    )
    _old_session(workspace, [{"role": "user", "content": "Migrate me"}])
    marker_path = workspace / ".owner-identity-migrated"

    changed = migrate_workspace_identity(workspace)

    assert changed[f"sessions/{OLD_FILENAME}"] == 1
    assert (workspace / "sessions" / NEW_FILENAME).exists()
    marker = marker_path.read_text(encoding="utf-8")
    assert "version = 2" in marker


def test_current_marker_version_is_a_noop(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".owner-identity-migrated").write_text(
        "Local web and CLI identities unified.\nversion = 2\n", encoding="utf-8"
    )
    _old_session(workspace, [{"role": "user", "content": "Already done"}])

    assert migrate_workspace_identity(workspace) == {}
    assert (workspace / "sessions" / OLD_FILENAME).exists()
    assert not (workspace / "sessions" / NEW_FILENAME).exists()


def test_cli_direct_session_is_never_rewritten(tmp_path):
    workspace = tmp_path / "workspace"
    sessions = workspace / "sessions"
    path = sessions / "cli_direct.jsonl"
    messages = [{"role": "user", "content": "CLI only"}]
    _write_session(path, "cli:direct", messages)

    changed = migrate_workspace_identity(workspace)

    assert "sessions/cli_direct.jsonl" not in changed
    with path.open(encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert json.loads(lines[0])["key"] == "cli:direct"
    assert [json.loads(line) for line in lines[1:]] == messages


def test_legacy_session_yielding_an_occupied_name_is_left_alone(tmp_path):
    workspace = tmp_path / "workspace"
    sessions = workspace / "sessions"
    _old_session(workspace, [{"role": "user", "content": "Legacy copy"}])
    occupant_messages = [{"role": "user", "content": "Live occupant"}]
    _write_session(sessions / NEW_FILENAME, "web:web:session_identity_0001", occupant_messages)

    changed = migrate_workspace_identity(workspace)

    assert changed == {}
    assert (sessions / OLD_FILENAME).exists()
    with (sessions / NEW_FILENAME).open(encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert [json.loads(line) for line in lines[1:]] == occupant_messages


def test_require_browser_session_succeeds_for_old_format_after_migration(tmp_path):
    workspace = tmp_path / "workspace"
    _old_session(
        workspace,
        [
            {"role": "user", "content": "Plan the launch"},
            {"role": "assistant", "content": "Deploy and verify"},
        ],
    )

    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    with pytest.raises(ValueError, match="Session was not found"):
        channel._require_browser_session(CLIENT_A, SESSION_A)

    migrate_workspace_identity(workspace)

    channel._require_browser_session(CLIENT_A, SESSION_A)

    reloaded = SessionManager(workspace).get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    assert [m["content"] for m in reloaded.messages] == [
        "Plan the launch",
        "Deploy and verify",
    ]
