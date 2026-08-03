from pathlib import Path

import pytest

from picobot.projects import ProjectStore


def test_project_registry_is_owner_scoped_and_archivable(tmp_path: Path):
    store = ProjectStore(tmp_path)
    pico = store.create(
        "owner-a", title="Pico", kind="software", purpose="Personal work partner",
        capabilities=["read_workspace", "create_artifacts"],
    )
    assert pico.status == "active"
    assert pico.capabilities == ["create_artifacts", "read_workspace"]
    assert store.list("owner-b") == []
    archived = store.update(
        "owner-a", pico.id, title="Pico", kind="software", purpose="Personal work partner",
        capabilities=pico.capabilities, status="archived",
    )
    assert archived.archived_at is not None
    assert store.list("owner-a") == []
    assert store.list("owner-a", include_archived=True) == [archived]


def test_project_sources_relationships_and_freshness_are_explicit(tmp_path: Path):
    store = ProjectStore(tmp_path)
    pico = store.create("owner", title="Pico", kind="software", purpose="Workbench")
    dax = store.create("owner", title="DAX", kind="software", purpose="Governed execution")
    source = store.add_source("owner", pico.id, kind="github_repo", label="Repository", locator="owner/pico")
    link = store.link("owner", pico.id, dax.id, relation="uses", summary="Optional adapter")
    assert store.sources("owner", pico.id) == [source]
    assert store.links("owner", pico.id) == [link]
    assert store.mark_inspected("owner", pico.id).inspected_at is not None


@pytest.mark.parametrize(
    ("kind", "locator"),
    [("url", "https://user:secret@example.com"), ("github_repo", "https://github.com/owner/repo")],
)
def test_project_registry_rejects_unsafe_or_ambiguous_sources(tmp_path: Path, kind: str, locator: str):
    store = ProjectStore(tmp_path)
    project = store.create("owner", title="Research", kind="research", purpose="Source review")
    with pytest.raises(ValueError):
        store.add_source("owner", project.id, kind=kind, label="Source", locator=locator)


def test_project_relationship_cannot_cross_owner_boundary(tmp_path: Path):
    store = ProjectStore(tmp_path)
    own = store.create("owner-a", title="A", kind="personal", purpose="A")
    other = store.create("owner-b", title="B", kind="personal", purpose="B")
    with pytest.raises(KeyError):
        store.link("owner-a", own.id, other.id, relation="informs")
