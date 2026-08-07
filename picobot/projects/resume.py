"""Reassemble what a project was, for someone returning to it cold.

Coming back to a project after weeks costs an hour of rebuilding context, and
most of that hour is spent recovering things the repository never held: why an
approach was abandoned, what the next step was going to be, what was still
uncertain.  Commits, branches and diffs survive on their own; reasoning does
not.

So a resume has two halves, and they are deliberately kept apart:

- **Captured** — what the owner explicitly wrote down when they stopped.  Not
  recoverable from anywhere else, and never inferred here.
- **Observed** — current repository state, re-read live every time.

Observed state is never copied into memory.  A stored summary of a repository
starts drifting the moment it is written, while the repository itself is always
true; re-reading costs one subprocess call and cannot go stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Kinds that carry reasoning worth resurfacing on return, in the order a
#: returning owner needs them: where you were headed, what stopped you, then
#: the decisions that explain both.
RESUME_KINDS: tuple[str, ...] = ("next_step", "open_question", "decision", "constraint")

#: A resume is a briefing, not an archive.  Past a handful of entries per kind
#: it stops being something you read on arrival.
_MAX_PER_KIND = 5


@dataclass(frozen=True)
class ResumeEntry:
    """One captured memory, reduced to what a returning owner needs."""

    id: str
    kind: str
    hook: str
    value: str
    why: str | None
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "hook": self.hook,
            "value": self.value,
            "why": self.why,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class ProjectResume:
    """Captured reasoning and observed repository state, kept distinct."""

    project_id: str
    captured: dict[str, list[ResumeEntry]] = field(default_factory=dict)
    observed: list[dict[str, Any]] = field(default_factory=list)
    last_captured_at: str | None = None

    @property
    def has_captured(self) -> bool:
        return any(self.captured.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "captured": {
                kind: [entry.to_dict() for entry in entries]
                for kind, entries in self.captured.items()
                if entries
            },
            "observed": self.observed,
            "last_captured_at": self.last_captured_at,
            "has_captured": self.has_captured,
        }


def build_project_resume(
    *,
    project_id: str,
    owner_id: str,
    memory_store: Any,
    observed: list[dict[str, Any]] | None = None,
) -> ProjectResume:
    """Assemble a resume for one project.

    ``observed`` is supplied by the caller rather than gathered here, so that
    reading a repository stays the responsibility of the awareness inspector
    that already owns subprocess and network access.
    """
    captured: dict[str, list[ResumeEntry]] = {}
    latest: str | None = None
    for kind in RESUME_KINDS:
        items = memory_store.list(
            owner_id,
            project_id=project_id,
            kinds=(kind,),
            status="confirmed",
            limit=_MAX_PER_KIND,
        )
        entries = [
            ResumeEntry(
                id=item.id,
                kind=item.kind,
                # A memory saved before hooks existed still has to render, so
                # fall back to its value rather than showing an empty line.
                hook=item.hook or item.value,
                value=item.value,
                why=item.why,
                captured_at=item.created_at,
            )
            for item in items
        ]
        captured[kind] = entries
        for entry in entries:
            if latest is None or entry.captured_at > latest:
                latest = entry.captured_at

    return ProjectResume(
        project_id=project_id,
        captured=captured,
        observed=list(observed or []),
        last_captured_at=latest,
    )
