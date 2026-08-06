"""Explicit, bounded project-source review for the local workbench.

The project registry itself intentionally grants no access.  This module is a
separate, owner-initiated read-only inspection step that emits a small safe
brief suitable for a durable Pico artifact.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from picobot.projects.store import Project, ProjectSource


class ProjectBriefError(ValueError):
    """The selected source cannot be safely reviewed."""


@dataclass(frozen=True)
class ProjectBrief:
    """A compact, redacted result of one explicit source inspection."""

    project_id: str
    source_id: str
    source_kind: str
    source_label: str
    content: str


class ProjectBriefInspector:
    """Read a declared source without recursively scanning or executing it."""

    _MAX_SIGNALS = 24
    _MAX_README_CHARS = 4_000
    _BLOCKED_NAMES = {".git", ".venv", "node_modules", "__pycache__", "credentials", "secrets"}
    _SECRET_LINE = re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|token|auth(?:orization)?|password|private[_-]?key|secret)\s*[:=]"
    )
    _TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,})\b")

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def inspect(self, project: Project, source: ProjectSource) -> ProjectBrief:
        if source.kind == "local_folder":
            body = self._local_summary(source)
        elif source.kind == "github_repo":
            body = self._github_summary(source)
        else:
            raise ProjectBriefError("Only local folders and GitHub repositories can be reviewed as project sources")
        content = "\n".join(
            [
                f"# Project brief: {project.title}",
                "",
                "## Purpose",
                project.purpose,
                "",
                "## Declared source",
                f"- {source.label} ({source.kind})",
                "",
                "## Read-only review",
                body,
                "",
                "## Boundary",
                "This is an explicit, read-only source review. It does not grant Pico authority to modify, execute, or recursively inspect the project.",
            ]
        )
        return ProjectBrief(
            project_id=project.id,
            source_id=source.id,
            source_kind=source.kind,
            source_label=source.label,
            content=content,
        )

    def _local_summary(self, source: ProjectSource) -> str:
        root = self._local_root(source.locator)
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise ProjectBriefError("The declared local folder is unavailable") from exc
        safe_entries = [item for item in entries if self._safe_name(item.name)]
        signals = [f"- {'Directory' if item.is_dir() else 'File'}: `{item.name}`" for item in safe_entries[: self._MAX_SIGNALS]]
        if len(safe_entries) > self._MAX_SIGNALS:
            signals.append(f"- {len(safe_entries) - self._MAX_SIGNALS} additional safe top-level entries omitted")
        readme = self._readme_excerpt(safe_entries)
        git = self._git_summary(root)
        sections = ["### Safe top-level signals", *(signals or ["- No safe top-level signals were found."])]
        if readme:
            sections.extend(["", "### README excerpt", readme])
        sections.extend(["", "### Git state", git])
        return "\n".join(sections)

    def _github_summary(self, source: ProjectSource) -> str:
        owner, repository = source.locator.split("/", 1)
        request = Request(
            f"https://api.github.com/repos/{owner}/{repository}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Pico-local-project-brief"},
        )
        try:
            with urlopen(request, timeout=8) as response:  # nosec B310: fixed public GitHub API origin
                payload = json.loads(response.read(128_000).decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return "Public GitHub metadata is unavailable. The repository reference remains declared context only."
        if not isinstance(payload, dict):
            return "Public GitHub metadata is unavailable. The repository reference remains declared context only."
        description = self._redact(str(payload.get("description") or "No repository description."))
        branch = self._redact(str(payload.get("default_branch") or "not reported"))
        updated = self._redact(str(payload.get("updated_at") or "not reported"))
        language = self._redact(str(payload.get("language") or "not reported"))
        return "\n".join(
            [
                f"- Repository: `{owner}/{repository}`",
                f"- Description: {description}",
                f"- Default branch: `{branch}`",
                f"- Primary language: {language}",
                f"- GitHub last reported update: {updated}",
                "- Retrieved from GitHub's public metadata endpoint without credentials.",
            ]
        )

    def _local_root(self, locator: str) -> Path:
        candidate = Path(locator).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            root = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectBriefError("The declared local folder is unavailable") from exc
        if not root.is_dir():
            raise ProjectBriefError("The declared source is not a local folder")
        return root

    def _readme_excerpt(self, entries: list[Path]) -> str | None:
        readme = next((item for item in entries if item.name.lower() in {"readme", "readme.md", "readme.txt"}), None)
        if readme is None or readme.is_symlink() or not readme.is_file():
            return None
        try:
            raw = readme.read_text(encoding="utf-8", errors="replace")[: self._MAX_README_CHARS]
        except OSError:
            return None
        excerpt = self._redact(raw).strip()
        if not excerpt:
            return None
        return excerpt + ("\n\n_Excerpt truncated._" if len(raw) >= self._MAX_README_CHARS else "")

    def _git_summary(self, root: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "status", "--short", "--branch"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "GIT_CONFIG_NOSYSTEM": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return "Git state unavailable."
        if result.returncode != 0:
            return "Not a readable Git worktree."
        lines = [line for line in result.stdout.splitlines() if self._safe_git_line(line)]
        if not lines:
            return "No safe Git state was reported."
        return "\n".join(f"- {self._redact(line)}" for line in lines[:12])

    @classmethod
    def _safe_name(cls, name: str) -> bool:
        lowered = name.casefold()
        return not (
            name.startswith(".")
            or lowered in cls._BLOCKED_NAMES
            or lowered == ".env"
            or lowered.startswith(".env.")
            or lowered.endswith((".pem", ".key"))
        )

    @classmethod
    def _safe_git_line(cls, line: str) -> bool:
        path_part = line[3:].strip().replace(" -> ", "/")
        return bool(path_part) and all(cls._safe_name(part) for part in Path(path_part).parts)

    @classmethod
    def _redact(cls, value: str) -> str:
        lines = []
        for line in value.splitlines():
            if cls._SECRET_LINE.search(line):
                lines.append("[sensitive value redacted]")
            else:
                lines.append(cls._TOKEN.sub("[token redacted]", line))
        return "\n".join(lines)
