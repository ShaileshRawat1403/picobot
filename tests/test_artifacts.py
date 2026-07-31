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
    )

    assert artifact.revision == 1
    assert store.read_content("web:browser:owner-a", artifact.id).startswith("# First draft")
    assert (store.root / artifact.relative_path).is_file()
    assert store.list("web:browser:owner-a") == [artifact]
    assert store.list("web:browser:owner-b") == []

    revised = store.revise("web:browser:owner-a", artifact.id, "# Revised\n\nNow with history.")
    assert revised.revision == 2
    assert store.read_content("web:browser:owner-a", artifact.id) == "# Revised\n\nNow with history."
    assert store.read_content("web:browser:owner-a", artifact.id, revision=1).startswith(
        "# First draft"
    )
    assert [item.revision for item in store.revisions("web:browser:owner-a", artifact.id)] == [2, 1]

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
