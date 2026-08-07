"""Contract tests for reassembling a project for someone returning to it.

The resume has two halves that must never blur: captured reasoning (only what
was explicitly written down, grouped by kind) and observed repository state
(never written into memory).  These tests pin the boundaries between them.
"""

from pathlib import Path

import pytest

from picobot.memory.store import PersonalMemoryStore
from picobot.projects.resume import RESUME_KINDS, build_project_resume
from picobot.projects.store import ProjectStore

OWNER = "resume-owner"


def _memory(store: PersonalMemoryStore, *, project_id: str, value: str, kind: str, why: str | None = None):
    return store.create(
        owner_id=OWNER,
        value=value,
        kind=kind,  # type: ignore[arg-type]
        scope="project",
        project_id=project_id,
        hook=None,
        why=why,
    )


def _project(tmp_path: Path) -> tuple[ProjectStore, PersonalMemoryStore, str]:
    projects = ProjectStore(tmp_path)
    project = projects.create(OWNER, title="Pico", kind="software", purpose="A brain for work")
    return projects, PersonalMemoryStore(tmp_path), project.id


def test_resume_returns_only_requested_project_memories(tmp_path: Path):
    _, store, this_project = _project(tmp_path)
    other = ProjectStore(tmp_path).create(OWNER, title="Other", kind="software", purpose="B")

    _memory(store, project_id=this_project, value="Draft the onboarding", kind="next_step")
    _memory(store, project_id=this_project, value="Keep the CLI default", kind="decision", why="Owners use it")
    _memory(store, project_id=other.id, value="Ship the mobile build", kind="next_step")
    store.create(owner_id=OWNER, value="Tea is out", kind="fact", scope="personal")

    resume = build_project_resume(
        project_id=this_project,
        owner_id=OWNER,
        memory_store=store,
    )

    assert resume.project_id == this_project
    assert [entry.value for entry in resume.captured["next_step"]] == ["Draft the onboarding"]
    assert [entry.value for entry in resume.captured["decision"]] == ["Keep the CLI default"]
    assert [entry.why for entry in resume.captured["decision"]] == ["Owners use it"]
    assert all(entry.kind == "next_step" for entry in resume.captured["next_step"])
    assert "open_question" not in resume.to_dict()["captured"]
    assert "constraint" not in resume.to_dict()["captured"]


def test_resume_kinds_follow_resume_order_and_are_capped(tmp_path: Path):
    _, store, project = _project(tmp_path)
    for index in range(7):
        _memory(store, project_id=project, value=f"Question {index}", kind="open_question")

    resume = build_project_resume(
        project_id=project,
        owner_id=OWNER,
        memory_store=store,
    )

    assert list(resume.captured) == list(RESUME_KINDS)
    assert resume.captured["next_step"] == []
    assert len(resume.captured["open_question"]) == 5


def test_resume_accepts_only_valid_kinds(tmp_path: Path):
    _, store, _ = _project(tmp_path)
    with pytest.raises(ValueError, match="Unsupported memory kind"):
        store.list(OWNER, kinds=("bogus",))


def test_resume_entry_falls_back_to_value_when_hook_is_absent(tmp_path: Path):
    _, store, project = _project(tmp_path)
    memory = _memory(store, project_id=project, value="Evaluate the API first", kind="next_step")
    assert memory.hook is None

    resume = build_project_resume(
        project_id=project,
        owner_id=OWNER,
        memory_store=store,
    )

    entry = resume.captured["next_step"][0]
    assert entry.hook == "Evaluate the API first"
    assert entry.value == "Evaluate the API first"


def test_resume_last_captured_at_is_newest_captured_entry(tmp_path: Path):
    _, store, project = _project(tmp_path)
    earlier = _memory(store, project_id=project, value="First open question", kind="open_question")
    latest = _memory(store, project_id=project, value="Final decision", kind="decision")

    resume = build_project_resume(
        project_id=project,
        owner_id=OWNER,
        memory_store=store,
    )

    assert resume.last_captured_at == latest.created_at
    assert resume.last_captured_at >= earlier.created_at


def test_resume_observed_is_passed_through_and_never_inferred(tmp_path: Path):
    _, store, project = _project(tmp_path)
    observed = [{"kind": "local_folder", "state": "unavailable", "message": "Gone."}]

    resume = build_project_resume(
        project_id=project,
        owner_id=OWNER,
        memory_store=store,
        observed=observed,
    )

    assert resume.observed == observed
    assert resume.has_captured is False
    payload = resume.to_dict()
    assert payload["captured"] == {}
    assert payload["last_captured_at"] is None
