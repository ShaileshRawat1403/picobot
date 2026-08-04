"""Bounded project-awareness observations for local and GitHub sources."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from picobot.projects.brief import ProjectBriefInspector
from picobot.projects.store import Project, ProjectSource


class ProjectAwarenessError(ValueError):
    """A declared source could not be safely refreshed."""


@dataclass(frozen=True)
class ProjectAwareness:
    """A compact, redacted project-source summary suitable for durable evidence."""

    project_id: str
    source_id: str
    summary: dict


class ProjectAwarenessInspector:
    """Inspect explicit sources without recursively reading a project tree."""

    _MAX_COMMITS = 8
    _MAX_CHANGES = 12

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def inspect(self, project: Project, source: ProjectSource) -> ProjectAwareness:
        if source.kind == "local_folder":
            summary = self._local_git_summary(source)
        elif source.kind == "github_repo":
            summary = self._github_summary(source)
        else:
            raise ProjectAwarenessError("Only local folders and GitHub repositories can refresh project awareness")
        return ProjectAwareness(project_id=project.id, source_id=source.id, summary=summary)

    def _local_git_summary(self, source: ProjectSource) -> dict:
        root = self._local_root(source.locator)
        status = self._git(root, ["status", "--porcelain=v1", "--branch"])
        if status is None:
            return {"state": "unavailable", "message": "Local Git state is unavailable."}
        if status.returncode != 0:
            return {"state": "not_git", "message": "The declared folder is not a readable Git worktree."}
        lines = status.stdout.splitlines()
        head = lines[0][3:].strip() if lines and lines[0].startswith("## ") else "not reported"
        changes = [line for line in lines[1:] if self._safe_git_change(line)]
        commits = self._git(root, ["log", f"-n{self._MAX_COMMITS}", "--date=short", "--format=%h%x1f%ad%x1f%s"])
        commit_rows = []
        if commits is not None and commits.returncode == 0:
            for line in commits.stdout.splitlines()[: self._MAX_COMMITS]:
                parts = line.split("\x1f", 2)
                if len(parts) == 3:
                    commit_rows.append(
                        {
                            "id": ProjectBriefInspector._redact(parts[0]),
                            "date": ProjectBriefInspector._redact(parts[1]),
                            "summary": ProjectBriefInspector._redact(parts[2]),
                        }
                    )
        return {
            "state": "ready",
            "branch": ProjectBriefInspector._redact(head),
            "changed_files": [ProjectBriefInspector._redact(line) for line in changes[: self._MAX_CHANGES]],
            "changed_file_count": len(changes),
            "recent_commits": commit_rows,
        }

    def _github_summary(self, source: ProjectSource) -> dict:
        owner, repository = source.locator.split("/", 1)
        metadata = self._github_json(f"https://api.github.com/repos/{owner}/{repository}")
        if not isinstance(metadata, dict):
            return {"state": "unavailable", "message": "Public GitHub metadata is unavailable."}
        commits_payload = self._github_json(
            f"https://api.github.com/repos/{owner}/{repository}/commits?per_page={self._MAX_COMMITS}"
        )
        commits = []
        if isinstance(commits_payload, list):
            for item in commits_payload[: self._MAX_COMMITS]:
                if not isinstance(item, dict) or not isinstance(item.get("commit"), dict):
                    continue
                commit = item["commit"]
                commits.append(
                    {
                        "id": ProjectBriefInspector._redact(str(item.get("sha") or "")[:12]),
                        "date": ProjectBriefInspector._redact(str((commit.get("author") or {}).get("date") or "not reported")),
                        "summary": ProjectBriefInspector._redact(str(commit.get("message") or "").splitlines()[0]),
                    }
                )
        return {
            "state": "ready",
            "repository": f"{owner}/{repository}",
            "branch": ProjectBriefInspector._redact(str(metadata.get("default_branch") or "not reported")),
            "updated_at": ProjectBriefInspector._redact(str(metadata.get("updated_at") or "not reported")),
            "open_issues": int(metadata.get("open_issues_count") or 0),
            "recent_commits": commits,
        }

    def _local_root(self, locator: str) -> Path:
        candidate = Path(locator).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            root = candidate.resolve(strict=True)
        except OSError as exc:
            raise ProjectAwarenessError("The declared local folder is unavailable") from exc
        if not root.is_dir():
            raise ProjectAwarenessError("The declared source is not a local folder")
        return root

    @staticmethod
    def _git(root: Path, arguments: list[str]):
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "GIT_CONFIG_NOSYSTEM": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def _safe_git_change(line: str) -> bool:
        if len(line) < 4:
            return False
        path = line[3:].strip().replace(" -> ", "/")
        return bool(path) and all(ProjectBriefInspector._safe_name(part) for part in Path(path).parts)

    @staticmethod
    def _github_json(url: str) -> object | None:
        request = Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Pico-local-project-awareness"},
        )
        try:
            with urlopen(request, timeout=8) as response:  # nosec B310: fixed public GitHub API origin
                return json.loads(response.read(192_000).decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return None
