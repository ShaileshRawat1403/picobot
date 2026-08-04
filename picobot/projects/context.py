"""Bounded read-only project context for one Pico turn.

The resolver turns an explicitly selected Project Registry record into a small
brief. It does not inspect files, fetch URLs, or grant the model new tools.
Those are separate, governed capabilities.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from picobot.projects.store import ProjectStore


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    title: str
    kind: str
    purpose: str
    inspected_at: str | None
    sources: tuple[dict[str, str], ...]
    relationships: tuple[dict[str, str], ...]
    awareness: tuple[dict[str, str], ...]

    def to_dict(self) -> dict:
        return asdict(self)

    def prompt(self) -> str:
        lines = [
            "# Oriented Pico project",
            f"Project: {self.title} ({self.kind})",
            f"Purpose: {self.purpose}",
            f"Last project inspection: {self.inspected_at or 'not recorded'}.",
        ]
        if self.sources:
            lines.append(
                "Declared sources: " + "; ".join(
                    f"{item['label']} ({item['kind']})" for item in self.sources
                ) + "."
            )
        if self.relationships:
            lines.append(
                "Related projects: " + "; ".join(
                    f"{item['relation']} {item['title']}" for item in self.relationships
                ) + "."
            )
        if self.awareness:
            lines.append(
                "Latest explicit source signals: " + "; ".join(
                    f"{item['label']}: {item['summary']}" for item in self.awareness
                ) + "."
            )
        lines.append(
            "This is declared context, not permission to inspect or act on a source. "
            "Use only tools already available in the active capability profile."
        )
        return "\n".join(lines)


class ProjectContextResolver:
    """Resolve at most one active project into an owner-scoped turn brief."""

    _MAX_SOURCES = 8
    _MAX_RELATIONSHIPS = 8

    def __init__(self, workspace: Path):
        self.store = ProjectStore(workspace)

    def resolve(self, owner_id: str, project_id: str | None) -> ProjectContext | None:
        if not project_id:
            return None
        try:
            project = self.store.get(owner_id, project_id)
        except KeyError:
            return None
        if project.status != "active":
            return None

        sources = tuple(
            {"kind": source.kind, "label": source.label}
            for source in self.store.sources(owner_id, project.id)[: self._MAX_SOURCES]
        )
        related: list[dict[str, str]] = []
        for link in self.store.links(owner_id, project.id):
            related_id = link.related_project_id if link.project_id == project.id else link.project_id
            try:
                related_project = self.store.get(owner_id, related_id)
            except KeyError:
                continue
            related.append({"relation": link.relation, "title": related_project.title})
            if len(related) >= self._MAX_RELATIONSHIPS:
                break
        awareness: list[dict[str, str]] = []
        seen_sources: set[str] = set()
        for snapshot in self.store.snapshots(owner_id, project.id, limit=12):
            if snapshot.source_id in seen_sources:
                continue
            seen_sources.add(snapshot.source_id)
            summary = snapshot.summary
            if summary.get("state") != "ready":
                detail = str(summary.get("message") or "source unavailable")
            else:
                branch = str(summary.get("branch") or "branch not reported")
                commits = summary.get("recent_commits")
                commit = commits[0].get("summary") if isinstance(commits, list) and commits and isinstance(commits[0], dict) else None
                changed = summary.get("changed_file_count")
                parts = [branch]
                if isinstance(changed, int):
                    parts.append(f"{changed} local changes")
                if isinstance(commit, str) and commit:
                    parts.append(f"latest: {commit[:180]}")
                detail = " · ".join(parts)
            awareness.append({"label": snapshot.source_label, "summary": detail[:320]})
            if len(awareness) >= 3:
                break
        return ProjectContext(
            project_id=project.id,
            title=project.title,
            kind=project.kind,
            purpose=project.purpose,
            inspected_at=project.inspected_at,
            sources=sources,
            relationships=tuple(related),
            awareness=tuple(awareness),
        )
