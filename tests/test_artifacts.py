from pathlib import Path
import sqlite3

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


def test_structured_artifact_formats_are_validated_and_versioned(tmp_path: Path):
    store = ArtifactStore(tmp_path)

    json_artifact = store.create(
        owner_id="owner",
        session_key="session",
        title="Structured result",
        content='{"answer": 42}',
        kind="data",
        content_type="application/json",
    )
    yaml_artifact = store.create(
        owner_id="owner",
        session_key="session",
        title="Personal defaults",
        content="tone: concise\nreview: weekly\n",
        kind="config",
        content_type="application/yaml",
    )
    jsonl_artifact = store.create(
        owner_id="owner",
        session_key="session",
        title="Research observations",
        content='{"source":"a"}\n{"source":"b"}\n',
        kind="data",
        content_type="application/x-ndjson",
    )
    tsv_artifact = store.create(
        owner_id="owner",
        session_key="session",
        title="Work tracker",
        content="item\tstate\nPico\tactive\n",
        kind="data",
        content_type="text/tab-separated-values",
    )
    report = store.create(
        owner_id="owner",
        session_key="session",
        title="Decision report",
        content="# Decision\n\nShip the small slice.",
        kind="report",
    )

    assert json_artifact.relative_path.endswith(".json")
    assert yaml_artifact.relative_path.endswith(".yaml")
    assert jsonl_artifact.relative_path.endswith(".jsonl")
    assert tsv_artifact.relative_path.endswith(".tsv")
    assert report.kind == "report"

    with pytest.raises(ValueError, match="valid JSON"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Bad JSON",
            content='{"answer": NaN}',
            kind="data",
            content_type="application/json",
        )
    with pytest.raises(ValueError, match="safe, valid YAML"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Unsafe YAML",
            content="!!python/object/apply:os.system ['echo unsafe']",
            kind="config",
            content_type="application/yaml",
        )
    with pytest.raises(ValueError, match="Every JSONL line"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Bad JSONL",
            content='{"valid": true}\nnot-json\n',
            kind="data",
            content_type="application/x-ndjson",
        )
    with pytest.raises(ValueError, match="valid CSV/TSV"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Bad TSV",
            content='"unterminated\tvalue\n',
            kind="data",
            content_type="text/tab-separated-values",
        )


def test_link_bundle_artifacts_keep_safe_metadata(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    bundle = store.create(
        owner_id="owner",
        session_key="session",
        title="Source pack",
        content='{"links":[{"url":"https://example.com","label":"Reference","note":"Read this first"}]}',
        kind="link",
        content_type="application/json",
    )

    assert bundle.content_type == "application/json"
    assert '"label": "Reference"' in store.read_content("owner", bundle.id)
    with pytest.raises(ValueError, match="links array"):
        store.create(
            owner_id="owner",
            session_key="session",
            title="Bad source pack",
            content='{"items":[]}',
            kind="link",
            content_type="application/json",
        )


def test_artifact_store_migrates_legacy_manifest_without_provenance(tmp_path: Path):
    workspace = tmp_path / "workspace"
    root = workspace / "artifacts"
    root.mkdir(parents=True)
    database = root / ".pico-artifacts.db"
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                content_type TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE artifact_revisions (
                artifact_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_id, revision)
            );
            """
        )
        connection.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "owner", "session", "Legacy", "note", "text/markdown", "draft", 1, "legacy/v1.md", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO artifact_revisions VALUES (?, ?, ?, ?)",
            ("legacy", 1, "legacy/v1.md", timestamp),
        )
    (root / "legacy").mkdir(parents=True)
    (root / "legacy" / "v1.md").write_text("legacy content", encoding="utf-8")

    artifact = ArtifactStore(workspace).get("owner", "legacy")

    assert artifact.verification_status == "unverified"
    assert artifact.source_run_id is None
    assert artifact.source_mission_id is None
