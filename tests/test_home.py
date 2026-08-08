"""Contract tests for the cross-project home summary.

The home view is a briefing, not a store: every figure is recomputed on
request, staleness is judged by drift rather than age, and "could not read" and
"nothing changed" must never render alike.
"""

from datetime import datetime, timezone

import pytest

from picobot.memory.store import PersonalMemoryStore
from picobot.projects.home import (
    _commits_since,
    _DRIFT_REVIEW,
    _recent_runs,
    _uncaptured,
    build_home_summary,
    classify_drift,
    HomeSummary,
)
from picobot.projects.store import ProjectStore
from picobot.runs.store import RunStore

OWNER = "home-owner"


def _project(tmp_path, title: str = "Pico") -> ProjectStore:
    projects = ProjectStore(tmp_path)
    projects.create(OWNER, title=title, kind="software", purpose="A brain for work")
    return projects


def _capture(store: PersonalMemoryStore, project_id: str, value: str, kind: str = "decision"):
    return store.create(
        owner_id=OWNER,
        value=value,
        kind=kind,  # type: ignore[arg-type]
        scope="project",
        project_id=project_id,
        hook=None,
    )


def test_classify_drift_treats_commits_as_the_primary_signal():
    # An old capture against an unchanged repository is still good.
    assert classify_drift(0, 41) == "current"
    assert classify_drift(_DRIFT_REVIEW, 0) == "review"
    assert classify_drift(12, 90) == "watch"


def test_classify_drift_unreadable_source_depends_on_capture_age():
    # Age matters only when the repository cannot be read.
    assert classify_drift(None, 61) == "watch"
    assert classify_drift(None, 9) == "unknown"


def test_commits_since_returns_none_when_no_source_is_readable():
    assert _commits_since([], datetime.now(timezone.utc)) is None
    observed = [{"kind": "local_folder", "state": "unavailable", "message": "Gone."}]
    assert _commits_since(observed, datetime.now(timezone.utc)) is None
    # A readable source with zero commits is zero, not None.
    observed = [{"kind": "local_folder", "state": "ready", "recent_commits": []}]
    assert _commits_since(observed, datetime.now(timezone.utc)) == 0


def test_commits_since_counts_only_commits_after_the_capture(tmp_path):
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    observed = [
        {
            "kind": "local_folder",
            "state": "ready",
            "recent_commits": [
                {"date": "2024-01-05", "id": "a", "summary": "Moved forward"},
                {"date": "2023-12-20", "id": "b", "summary": "Before capture"},
            ],
        }
    ]
    assert _commits_since(observed, since) == 1
    # A commit with an unparseable date is ignored, not counted.
    broken = [{"kind": "local_folder", "state": "ready", "recent_commits": [{"date": "not-a-date", "id": "x", "summary": "?"}]}]
    assert _commits_since(broken, since) == 0


def test_projects_without_a_capture_sort_first(tmp_path):
    projects = ProjectStore(tmp_path)
    captured = projects.create(OWNER, title="Captured", kind="software", purpose="Has notes")
    projects.create(OWNER, title="Fresh", kind="research", purpose="Never written down")
    memory = PersonalMemoryStore(tmp_path)
    _capture(memory, captured.id, "Keep the CLI default", kind="decision")

    summary = build_home_summary(
        owner_id=OWNER,
        project_store=projects,
        memory_store=memory,
        observe=lambda project_id: [],
    )

    titles = [card.title for card in summary.projects]
    assert titles == ["Fresh", "Captured"]
    assert summary.projects[0].last_captured_at is None


def test_run_without_usage_counts_in_runs_but_adds_zero_tokens(tmp_path):
    _project(tmp_path)
    runs = RunStore(tmp_path)
    run = runs.create(
        owner_id=OWNER,
        session_key="home-session",
        capability_profile="bounded",
        model="pico-test",
    )
    runs.mark_running(OWNER, run.id)
    runs.complete(OWNER, run.id, usage=None)

    summary = build_home_summary(
        owner_id=OWNER,
        project_store=ProjectStore(tmp_path),
        memory_store=PersonalMemoryStore(tmp_path),
        runs_store=runs,
        observe=lambda project_id: [],
    )

    assert summary.runs_this_week == 1
    assert summary.tokens_this_week == 0
    assert summary.tokens_by_model == {}


def test_run_with_usage_accumulates_tokens_by_model(tmp_path):
    _project(tmp_path)
    runs = RunStore(tmp_path)
    run = runs.create(
        owner_id=OWNER,
        session_key="home-session",
        capability_profile="bounded",
        model="pico-test",
    )
    runs.mark_running(OWNER, run.id)
    runs.complete(OWNER, run.id, usage={"total_tokens": 1234})

    summary = build_home_summary(
        owner_id=OWNER,
        project_store=ProjectStore(tmp_path),
        memory_store=PersonalMemoryStore(tmp_path),
        runs_store=runs,
        observe=lambda project_id: [],
    )

    assert summary.runs_this_week == 1
    assert summary.tokens_this_week == 1234
    assert summary.tokens_by_model == {"pico-test": 1234}


def test_unreadable_source_does_not_fail_the_whole_summary(tmp_path):
    project = ProjectStore(tmp_path).create(
        OWNER, title="Pico", kind="software", purpose="Moved folder"
    )

    def observe(project_id):
        raise RuntimeError("folder disappeared")

    summary = build_home_summary(
        owner_id=OWNER,
        project_store=ProjectStore(tmp_path),
        memory_store=PersonalMemoryStore(tmp_path),
        observe=observe,
    )

    assert len(summary.projects) == 1
    assert summary.projects[0].id == project.id
    assert summary.projects[0].commits_since_capture is None
    assert summary.projects[0].sources == 0


def test_next_step_is_drawn_from_captured_entries_only(tmp_path):
    projects = ProjectStore(tmp_path)
    project = projects.create(OWNER, title="Pico", kind="software", purpose="Plan next")
    memory = PersonalMemoryStore(tmp_path)
    _capture(memory, project.id, "Draft the onboarding", kind="next_step")

    summary = build_home_summary(
        owner_id=OWNER,
        project_store=projects,
        memory_store=memory,
        observe=lambda project_id: [],
    )

    assert summary.projects[0].next_step == "Draft the onboarding"


def test_recent_runs_propagates_a_store_error_instead_of_returning_empty():
    # Regression guard: a wrong call (e.g. limit > RunStore's page cap) used to
    # be swallowed and read as zero activity forever.
    class ExplodingStore:
        def list(self, owner_id, limit=None):
            raise ValueError("limit must be <= 100")

    with pytest.raises(ValueError, match="limit"):
        _recent_runs(ExplodingStore(), OWNER)


def test_uncaptured_returns_none_when_sessions_cannot_be_read():
    class Unreadable:
        def list_sessions(self):
            raise OSError("sessions dir is gone")

    assert _uncaptured(Unreadable()) is None


def test_uncaptured_returns_zero_when_there_are_genuinely_none():
    class Empty:
        def list_sessions(self):
            return []

    assert _uncaptured(Empty()) == 0


def test_to_dict_carries_uncaptured_none_through():
    summary = HomeSummary(uncaptured_sessions=None)
    assert summary.to_dict()["uncaptured_sessions"] is None
