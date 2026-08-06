"""Regression tests for the unified local owner identity."""

from picobot.artifacts.store import ArtifactStore
from picobot.config.identity import (
    LOCAL_OWNER_ID,
    migrate_workspace_identity,
    resolve_owner_id,
)
from picobot.memory.store import PersonalMemoryStore


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
