"""Git repository diff worker.

Pure function over workspace git state returning a typed result with sources.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileDiffItem:
    path: str
    status: str  # 'M', 'A', 'D', 'R', '?'
    additions: int
    deletions: int
    diff_text: str


@dataclass(frozen=True)
class RepoDiffResult:
    branch: str
    is_clean: bool
    total_files_changed: int
    total_additions: int
    total_deletions: int
    file_items: list[FileDiffItem] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "is_clean": self.is_clean,
            "total_files_changed": self.total_files_changed,
            "total_additions": self.total_additions,
            "total_deletions": self.total_deletions,
            "file_items": [
                {
                    "path": item.path,
                    "status": item.status,
                    "additions": item.additions,
                    "deletions": item.deletions,
                }
                for item in self.file_items
            ],
            "sources": self.sources,
        }


def run_repo_diff_worker(workspace: Path, base_ref: str = "HEAD") -> RepoDiffResult:
    """Execute repo diff worker over workspace path."""
    workspace = workspace.resolve()
    if not (workspace / ".git").exists():
        return RepoDiffResult(
            branch="none",
            is_clean=True,
            total_files_changed=0,
            total_additions=0,
            total_deletions=0,
            file_items=[],
            sources=[f"file://{workspace}"],
        )

    try:
        branch_proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        branch = branch_proc.stdout.strip()
    except Exception:
        branch = "unknown"

    try:
        numstat_proc = subprocess.run(
            ["git", "diff", "--numstat", base_ref],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        numstat_lines = [l for l in numstat_proc.stdout.splitlines() if l.strip()]
    except Exception:
        numstat_lines = []

    file_items: list[FileDiffItem] = []
    total_adds = 0
    total_dels = 0

    for line in numstat_lines:
        parts = line.split("\t")
        if len(parts) >= 3:
            adds = int(parts[0]) if parts[0].isdigit() else 0
            dels = int(parts[1]) if parts[1].isdigit() else 0
            path_str = parts[2]
            total_adds += adds
            total_dels += dels

            # Fetch individual diff text snippet
            try:
                diff_proc = subprocess.run(
                    ["git", "diff", base_ref, "--", path_str],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                snippet = diff_proc.stdout[:2000]
            except Exception:
                snippet = ""

            file_items.append(
                FileDiffItem(
                    path=path_str,
                    status="M",
                    additions=adds,
                    deletions=dels,
                    diff_text=snippet,
                )
            )

    return RepoDiffResult(
        branch=branch,
        is_clean=len(file_items) == 0,
        total_files_changed=len(file_items),
        total_additions=total_adds,
        total_deletions=total_dels,
        file_items=file_items,
        sources=[f"git://{workspace}@{branch}"],
    )
