from pathlib import Path

import pytest

from picobot.artifacts.store import ArtifactStore


def test_artifacts_are_owned_versioned_and_kept_inside_workspace(tmp_path: Path):
    store = ArtifactStore(tmp_path / "workspace")
    artifact = store.create(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        title="Pico workbench plan",
        content="# First draft\n\nBuild artifacts first.",
        kind="plan",
        source_run_id="run-123",
        source_mission_id="mission-456",
    )

    assert artifact.revision == 1
    assert artifact.verification_status == "unverified"
    assert artifact.source_run_id == "run-123"
    assert artifact.source_mission_id == "mission-456"
    assert store.read_content("web:browser:owner-a", artifact.id).startswith("# First draft")
    assert (store.root / artifact.relative_path).is_file()
    assert store.list("web:browser:owner-a") == [artifact]
    assert store.list("web:browser:owner-b") == []

    revised = store.revise("web:browser:owner-a", artifact.id, "# Revised\n\nNow with history.")
    assert revised.revision == 2
    assert revised.verification_status == "stale"
    assert revised.source_run_id == artifact.source_run_id
    assert revised.source_mission_id == artifact.source_mission_id
    assert store.read_content("web:browser:owner-a", artifact.id) == "# Revised\n\nNow with history."
    assert store.read_content("web:browser:owner-a", artifact.id, revision=1).startswith(
        "# First draft"
    )
    assert [item.revision for item in store.revisions("web:browser:owner-a", artifact.id)] == [2, 1]

    verified = store.set_verification("web:browser:owner-a", artifact.id, "verified")
    assert verified.verification_status == "verified"
    with pytest.raises(ValueError, match="verification status"):
        store.set_verification("web:browser:owner-a", artifact.id, "approved")

    with pytest.raises(KeyError):
        store.get("web:browser:owner-b", artifact.id)

def test_artifact_store_rejects_unsupported_types_and_oversized_titles(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    common = {"owner_id": "owner", "session_key": "session", "title": "A", "content": "body"}

    with pytest.raises(ValueError, match="Unsupported artifact kind"):
        store.create(**common, kind="executable")
    with pytest.raises(ValueError, match="Unsupported artifact content type"):
        store.create(**common, content_type="application/octet-stream")
    with pytest.raises(ValueError, match="title is limited"):
        store.create(**{**common, "title": "x" * 161})
    with pytest.raises(ValueError, match="source run ID"):
        store.create(**common, source_run_id=" ")
    with pytest.raises(ValueError, match="source mission ID"):
        store.create(**common, source_mission_id="x" * 321)


def test_link_artifacts_are_bounded_and_reject_unsafe_urls(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    link = store.create(
        owner_id="owner",
        session_key="session",
        title="Research sources",
        content=" https://example.com/docs \nhttps://example.org/notes ",
        kind="link",
        content_type="text/uri-list",
    )

    assert link.kind == "link"
    assert link.content_type == "text/uri-list"
    assert store.read_content("owner", link.id) == "https://example.com/docs\nhttps://example.org/notes"

    with pytest.raises(ValueError, match="HTTP"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Unsafe",
            content="file:///tmp/private.txt",
            kind="link",
            content_type="text/uri-list",
        )
    with pytest.raises(ValueError, match="embedded credentials"):
        store.revise("owner", link.id, "https://user:password@example.com/private")
