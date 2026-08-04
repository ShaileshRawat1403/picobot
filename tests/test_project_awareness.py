import subprocess
from pathlib import Path

import pytest

from picobot.projects import ProjectAwarenessInspector, ProjectContextResolver, ProjectStore


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True)


def test_local_project_awareness_keeps_history_and_redacts_sensitive_changes(tmp_path: Path):
    workspace, checkout = tmp_path / "workspace", tmp_path / "checkout"
    workspace.mkdir()
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "config", "user.email", "pico@example.test")
    _git(checkout, "config", "user.name", "Pico Test")
    (checkout / "README.md").write_text("# Project\n", encoding="utf-8")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-m", "Establish project direction")
    (checkout / "notes.md").write_text("current work\n", encoding="utf-8")
    (checkout / ".env").write_text("TOKEN=never-expose", encoding="utf-8")

    store = ProjectStore(workspace)
    project = store.create("owner", title="Project", kind="software", purpose="Build it")
    source = store.add_source("owner", project.id, kind="local_folder", label="Checkout", locator=str(checkout))
    awareness = ProjectAwarenessInspector(workspace).inspect(project, source)

    assert awareness.summary["state"] == "ready"
    assert awareness.summary["recent_commits"][0]["summary"] == "Establish project direction"
    assert any("notes.md" in value for value in awareness.summary["changed_files"])
    assert not any(".env" in value for value in awareness.summary["changed_files"])


def test_snapshot_is_owner_scoped_tracks_material_change_and_enriches_context(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project = store.create("owner", title="Pico", kind="software", purpose="A brain for work")
    source = store.add_source("owner", project.id, kind="github_repo", label="GitHub", locator="owner/pico")
    first = store.record_snapshot(
        "owner", project.id, source.id,
        summary={"state": "ready", "branch": "main", "recent_commits": [{"summary": "Initial direction"}]},
    )
    second = store.record_snapshot(
        "owner", project.id, source.id,
        summary={"state": "ready", "branch": "main", "recent_commits": [{"summary": "Initial direction"}]},
    )

    assert first.changed is True
    assert second.changed is False
    assert len(store.snapshots("owner", project.id)) == 2
    context = ProjectContextResolver(tmp_path).resolve("owner", project.id)
    assert context is not None
    assert context.awareness == ({"label": "GitHub", "summary": "main · latest: Initial direction"},)
    assert "Latest explicit source signals: GitHub: main · latest: Initial direction." in context.prompt()
    with pytest.raises(KeyError):
        store.snapshots("other-owner", project.id)


def test_github_project_awareness_uses_public_metadata_and_recent_history(monkeypatch, tmp_path: Path):
    store = ProjectStore(tmp_path)
    project = store.create("owner", title="Pico", kind="software", purpose="A brain for work")
    source = store.add_source("owner", project.id, kind="github_repo", label="GitHub", locator="owner/pico")

    def fake_github_json(url: str):
        if url.endswith("/owner/pico"):
            return {"default_branch": "main", "updated_at": "2026-08-04T00:00:00Z", "open_issues_count": 3}
        return [{"sha": "abc1234567890", "commit": {"author": {"date": "2026-08-03"}, "message": "Improve project awareness"}}]

    monkeypatch.setattr(ProjectAwarenessInspector, "_github_json", staticmethod(fake_github_json))
    awareness = ProjectAwarenessInspector(tmp_path).inspect(project, source)

    assert awareness.summary["repository"] == "owner/pico"
    assert awareness.summary["open_issues"] == 3
    assert awareness.summary["recent_commits"] == [
        {"id": "abc123456789", "date": "2026-08-03", "summary": "Improve project awareness"}
    ]
