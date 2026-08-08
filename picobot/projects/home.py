"""One view across every project, for someone who runs several at once.

Pico opens on an empty composer, which assumes the question you arrived with is
already formed.  Running six projects, the first question is usually the other
one: which of these is waiting on me, and is what I wrote down last time still
true?

This assembles that answer from records Pico already keeps.  Nothing here is
inferred and nothing is stored: every figure is recomputed on request, because
a cached summary of moving parts is wrong shortly after it is written.

Staleness is measured by drift rather than by age.  A decision from three
months ago against a repository nobody touched is still good; one from three
weeks ago against a repository that moved sixty commits probably is not.  Both
numbers are already known, they were simply never compared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

#: Commits since the last capture, beyond which captured reasoning is worth
#: re-reading before it is trusted.  Deliberately generous: the point is to
#: flag genuine drift, not to nag about ordinary progress.
_DRIFT_REVIEW = 40
_DRIFT_WATCH = 10

#: How far back the activity summary looks.
_ACTIVITY_DAYS = 7


@dataclass(frozen=True)
class ProjectCard:
    """One project, reduced to what decides whether you open it."""

    id: str
    title: str
    kind: str
    next_step: str | None
    last_captured_at: str | None
    days_since_capture: int | None
    commits_since_capture: int | None
    drift: str
    sources: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "next_step": self.next_step,
            "last_captured_at": self.last_captured_at,
            "days_since_capture": self.days_since_capture,
            "commits_since_capture": self.commits_since_capture,
            "drift": self.drift,
            "sources": self.sources,
        }


@dataclass(frozen=True)
class HomeSummary:
    projects: list[ProjectCard] = field(default_factory=list)
    tokens_this_week: int = 0
    runs_this_week: int = 0
    tokens_by_model: dict[str, int] = field(default_factory=dict)
    uncaptured_sessions: int | None = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "projects": [card.to_dict() for card in self.projects],
            "tokens_this_week": self.tokens_this_week,
            "runs_this_week": self.runs_this_week,
            "tokens_by_model": self.tokens_by_model,
            "uncaptured_sessions": self.uncaptured_sessions,
            "activity_days": _ACTIVITY_DAYS,
        }


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify_drift(commits_since: int | None, days_since: int | None) -> str:
    """Describe how far a project has moved since it was last written down.

    Age alone is a poor signal, so it only matters when the repository cannot
    be read: an unreadable source with an old capture is worth a second look,
    while an old capture against an unchanged repository is not.
    """
    if commits_since is None:
        return "watch" if days_since is not None and days_since > 60 else "unknown"
    if commits_since >= _DRIFT_REVIEW:
        return "review"
    if commits_since >= _DRIFT_WATCH:
        return "watch"
    return "current"


def _commits_since(observed: list[dict[str, Any]], since: datetime | None) -> int | None:
    """Count commits recorded after a capture.

    Returns ``None`` when no source could be read, which is different from
    zero: "nothing changed" and "we could not look" must not render alike.
    """
    if since is None:
        return None
    counted: int | None = None
    for source in observed:
        commits = source.get("recent_commits")
        if not isinstance(commits, list):
            continue
        counted = counted or 0
        for commit in commits:
            when = _parse(commit.get("date") if isinstance(commit, dict) else None)
            if when is not None and when > since:
                counted += 1
    return counted


def build_home_summary(
    *,
    owner_id: str,
    project_store: Any,
    memory_store: Any,
    runs_store: Any = None,
    session_manager: Any = None,
    observe: Any = None,
) -> HomeSummary:
    """Assemble the cross-project view.

    ``observe`` is an optional callable taking a project id and returning the
    same observed-source dicts the resume view uses.  It is injected rather
    than imported so a caller can skip repository reads when only the counts
    are needed.
    """
    cards: list[ProjectCard] = []
    now = datetime.now(timezone.utc)

    for project in project_store.list(owner_id):
        captured = memory_store.list(
            owner_id, project_id=project.id, status="confirmed", limit=50
        )
        latest = None
        next_step = None
        for item in captured:
            created = _parse(item.created_at)
            if created and (latest is None or created > latest):
                latest = created
            if next_step is None and item.kind == "next_step":
                next_step = item.hook or item.value

        days = (now - latest).days if latest else None
        observed: list[dict[str, Any]] = []
        if observe is not None:
            try:
                observed = observe(project.id) or []
            except Exception:
                observed = []
        commits = _commits_since(observed, latest)

        cards.append(
            ProjectCard(
                id=project.id,
                title=project.title,
                kind=project.kind,
                next_step=next_step,
                last_captured_at=latest.isoformat() if latest else None,
                days_since_capture=days,
                commits_since_capture=commits,
                drift=classify_drift(commits, days),
                sources=len(observed),
            )
        )

    # Projects never captured sort first: they are the ones most likely to have
    # been connected and then forgotten.
    cards.sort(key=lambda c: (c.last_captured_at is not None, c.last_captured_at or ""))

    tokens = 0
    runs_counted = 0
    by_model: dict[str, int] = {}
    if runs_store is not None:
        cutoff = now - timedelta(days=_ACTIVITY_DAYS)
        for run in _recent_runs(runs_store, owner_id):
            created = _parse(getattr(run, "created_at", None))
            if created is None or created < cutoff:
                continue
            runs_counted += 1
            usage = getattr(run, "usage", None) or {}
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if not isinstance(total, int) or total <= 0:
                continue
            tokens += total
            model = getattr(run, "model", None) or "unknown"
            by_model[model] = by_model.get(model, 0) + total

    return HomeSummary(
        projects=cards,
        tokens_this_week=tokens,
        runs_this_week=runs_counted,
        tokens_by_model=by_model,
        uncaptured_sessions=_uncaptured(session_manager),
    )


#: ``RunStore.list`` rejects anything above this and raises.
_RUN_PAGE = 100


def _recent_runs(runs_store: Any, owner_id: str) -> list[Any]:
    """Return recent runs for this owner.

    Deliberately a direct call with no fallback chain.  An earlier version
    probed several method names inside a broad ``except``, which meant a
    ``ValueError`` from passing ``limit=200`` was swallowed and the activity
    figures silently read zero forever.  A wrong call should fail where it can
    be seen: the route turns it into a 400 carrying the message, which is
    recoverable, while a silent zero is a number the owner would have believed.
    """
    return list(runs_store.list(owner_id, limit=_RUN_PAGE))


def _uncaptured(session_manager: Any) -> int | None:
    """Count sessions that held real work and were never captured.

    ``None`` means the sessions could not be read, which is not the same as
    none being uncaptured: showing a confident zero for a failed read is the
    same mistake as counting zero commits on a repository nobody could open.
    """
    if session_manager is None:
        return None
    try:
        entries = session_manager.list_sessions()
    except OSError:
        return None
    total = 0
    for entry in entries:
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = None
        if isinstance(metadata, dict) and metadata.get("pico_captured") is True:
            continue
        count = entry.get("message_count") if isinstance(entry, dict) else None
        if isinstance(count, int) and count >= 2:
            total += 1
    return total
