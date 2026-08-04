from __future__ import annotations

import json
from pathlib import Path

from picobot.projects import ProjectBriefInspector, ProjectStore


def test_local_project_brief_is_bounded_and_redacts_secret_like_readme_content(tmp_path: Path):
    workspace = tmp_path / "pico-workspace"
    source_root = tmp_path / "source"
    workspace.mkdir()
    source_root.mkdir()
    (source_root / "README.md").write_text(
        "# Project\n\nAPI_KEY=should-not-appear\nThis is a safe project overview.\n",
        encoding="utf-8",
    )
    (source_root / "pyproject.toml").write_text("[project]\nname='safe'\n", encoding="utf-8")
    (source_root / ".env").write_text("PRIVATE=never-read", encoding="utf-8")
    (source_root / "node_modules").mkdir()

    store = ProjectStore(workspace)
    project = store.create("owner", title="Safe source", kind="software", purpose="Review safely")
    source = store.add_source(
        "owner", project.id, kind="local_folder", label="Checkout", locator=str(source_root)
    )

    brief = ProjectBriefInspector(workspace).inspect(project, source)

    assert "README.md" in brief.content
    assert "pyproject.toml" in brief.content
    assert "[sensitive value redacted]" in brief.content
    assert "should-not-appear" not in brief.content
    assert ".env" not in brief.content
    assert "node_modules" not in brief.content
    assert str(source_root) not in brief.content
    assert "does not grant Pico authority" in brief.content


def test_github_project_brief_uses_only_public_metadata(monkeypatch, tmp_path: Path):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "description": "A public personal workbench",
                    "default_branch": "main",
                    "language": "Python",
                    "updated_at": "2026-08-04T00:00:00Z",
                }
            ).encode()

    monkeypatch.setattr("picobot.projects.brief.urlopen", lambda *_args, **_kwargs: FakeResponse())
    store = ProjectStore(tmp_path)
    project = store.create("owner", title="Pico", kind="software", purpose="Personal workbench")
    source = store.add_source(
        "owner", project.id, kind="github_repo", label="GitHub", locator="ShaileshRawat1403/picobot"
    )

    brief = ProjectBriefInspector(tmp_path).inspect(project, source)

    assert "ShaileshRawat1403/picobot" in brief.content
    assert "Default branch: `main`" in brief.content
    assert "without credentials" in brief.content
