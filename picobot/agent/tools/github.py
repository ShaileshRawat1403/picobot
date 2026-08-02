"""Read-only GitHub pull-request review tool."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any

from picobot.agent.tools.base import Tool


class GitHubPullRequestTool(Tool):
    """Inspect a pull request through the owner's authenticated ``gh`` CLI.

    This intentionally exposes no mutation operations.  Pico can read a PR,
    its checks, or its diff, but cannot comment, approve, merge, or push.
    """

    name = "github_pr"
    description = (
        "Read a GitHub pull request using the local authenticated GitHub CLI. "
        "Inspect its overview, checks, or bounded diff; no comments, approvals, merges, or pushes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["overview", "checks", "diff"],
                "description": "Read the PR overview, CI/check status, or code diff",
            },
            "repo": {
                "type": "string",
                "description": "GitHub repository in owner/name form",
                "minLength": 3,
                "maxLength": 200,
            },
            "number": {
                "type": "integer",
                "description": "Pull request number",
                "minimum": 1,
                "maximum": 10_000_000,
            },
        },
        "required": ["operation", "repo", "number"],
        "additionalProperties": False,
    }

    _REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    _MAX_OUTPUT = 16_000
    _MAX_DIFF = 12_000
    _TIMEOUT_SECONDS = 20

    @staticmethod
    def _bounded(text: str, limit: int) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n\n[Output truncated by Pico.]"

    @classmethod
    def _validate_request(cls, repo: str, number: int) -> str | None:
        if not isinstance(repo, str) or not cls._REPO_RE.fullmatch(repo):
            return "Error: repo must use GitHub owner/name form."
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            return "Error: number must be a positive pull request number."
        return None

    async def _run(self, args: list[str]) -> tuple[int, str, str]:
        executable = shutil.which("gh")
        if not executable:
            return 127, "", "GitHub CLI (gh) is not installed or not on Pico's PATH."
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return 124, "", "GitHub CLI request timed out."
        except OSError:
            return 127, "", "GitHub CLI could not be started."
        return process.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode(
            "utf-8", errors="replace"
        )

    async def execute(self, operation: str, repo: str, number: int, **_: Any) -> str:
        validation_error = self._validate_request(repo, number)
        if validation_error:
            return validation_error

        if operation == "overview":
            args = [
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "title,state,author,baseRefName,headRefName,isDraft,reviewDecision,mergeable,url,updatedAt,body",
            ]
        elif operation == "checks":
            args = ["pr", "checks", str(number), "--repo", repo, "--json", "name,state,bucket,workflow,link"]
        elif operation == "diff":
            args = ["pr", "diff", str(number), "--repo", repo]
        else:
            return "Error: operation must be one of overview, checks, or diff."

        return_code, stdout, stderr = await self._run(args)
        if return_code != 0:
            # gh's diagnostics are useful, but keep them bounded and never
            # return a potentially long environment/authentication dump.
            detail = self._bounded(stderr or stdout or "GitHub CLI request failed.", 800)
            detail = re.sub(
                r"(?i)(?:token|secret|password|authorization|bearer)[=: ]+[^\s]+",
                "[REDACTED]",
                detail,
            )
            return f"Error: GitHub PR {operation} failed for {repo}#{number}: {detail}"

        if operation == "overview":
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                return "Error: GitHub returned an invalid pull request overview."
            fields = [
                ("Title", payload.get("title", "")),
                ("State", payload.get("state", "")),
                ("Author", (payload.get("author") or {}).get("login", "")),
                ("Base", payload.get("baseRefName", "")),
                ("Head", payload.get("headRefName", "")),
                ("Draft", payload.get("isDraft", False)),
                ("Review decision", payload.get("reviewDecision") or "Pending"),
                ("Mergeable", payload.get("mergeable", "")),
                ("Updated", payload.get("updatedAt", "")),
                ("URL", payload.get("url", "")),
            ]
            lines = [f"GitHub PR: {repo}#{number}"]
            lines.extend(f"{label}: {value}" for label, value in fields if value != "")
            body = payload.get("body") or ""
            if body:
                lines.extend(["", "Description:", self._bounded(str(body), 5_000)])
            return self._bounded("\n".join(lines), self._MAX_OUTPUT)

        if operation == "checks":
            try:
                checks = json.loads(stdout)
            except json.JSONDecodeError:
                return "Error: GitHub returned invalid pull request checks."
            if not isinstance(checks, list) or not checks:
                return f"GitHub PR checks: {repo}#{number}\nNo checks reported."
            lines = [f"GitHub PR checks: {repo}#{number}"]
            for check in checks[:100]:
                if not isinstance(check, dict):
                    continue
                name = check.get("name", "Unnamed check")
                state = check.get("state", "")
                bucket = check.get("bucket", "")
                workflow = check.get("workflow", "")
                link = check.get("link", "")
                suffix = f" ({workflow})" if workflow else ""
                if link:
                    suffix += f"\n  {link}"
                lines.append(f"- {name}: {state} [{bucket}]{suffix}")
            return self._bounded("\n".join(lines), self._MAX_OUTPUT)

        return self._bounded(f"GitHub PR diff: {repo}#{number}\n\n{stdout}", self._MAX_DIFF)
