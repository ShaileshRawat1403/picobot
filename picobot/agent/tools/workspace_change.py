"""Approval-bound workspace change proposal tool."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from picobot.agent.tools.base import Tool
from picobot.operations.actions import ProposedActionStore


class ProposeWorkspaceChangeTool(Tool):
    """Stage a bounded file or command change without executing it."""

    _MAX_CONTENT = 120_000
    _MAX_COMMAND = 2_000
    _SENSITIVE_VALUE_RE = re.compile(
        r"(?:api[_-]?key|password|secret|authorization|bearer|token)\s*[:=]|\bsk-[A-Za-z0-9]",
        re.IGNORECASE,
    )
    _BLOCKED_PARTS = {".git", ".picobot"}

    def __init__(self, workspace: Path, action_store: ProposedActionStore | None = None):
        self.workspace = workspace.resolve()
        self.action_store = action_store or ProposedActionStore(workspace)
        self._owner_id: str | None = None
        self._session_key: str | None = None
        self._profile_id = "personal-work"
        self._queued_run_id: str | None = None

    def set_turn_context(
        self,
        *,
        owner_id: str,
        session_key: str,
        profile_id: str = "personal-work",
        queued_run_id: str | None = None,
        mission_id: str | None = None,
    ) -> None:
        self._owner_id = owner_id
        self._session_key = session_key
        self._profile_id = profile_id
        self._queued_run_id = queued_run_id

    @property
    def name(self) -> str:
        return "propose_workspace_change"

    @property
    def description(self) -> str:
        return (
            "Draft a workspace file or command change for owner approval. "
            "This tool never writes files or runs commands; the proposal must "
            "be reviewed and explicitly executed from Operations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["write_file", "edit_file", "exec"],
                    "description": "The bounded change to propose.",
                },
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path for write_file or edit_file.",
                },
                "content": {
                    "type": "string",
                    "description": "Complete file content for write_file.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact existing text to replace for edit_file.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text for edit_file.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every matching occurrence for edit_file (default false).",
                },
                "command": {
                    "type": "string",
                    "description": "Workspace command for exec (max 2000 characters).",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional workspace-relative working directory for exec.",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 600,
                    "description": "Command timeout in seconds (default 60).",
                },
                "summary": {
                    "type": "string",
                    "description": "Plain-language approval summary (max 600 characters).",
                },
                "expected_outcome": {
                    "type": "string",
                    "description": "What should be true after execution (max 600 characters).",
                },
            },
            "required": ["operation", "summary"],
        }

    def _path(self, raw_path: object) -> str:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("A workspace file path is required")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Workspace changes must stay inside Pico's configured workspace") from exc
        if any(part in self._BLOCKED_PARTS for part in relative.parts):
            raise ValueError("Workspace changes cannot target Pico runtime or Git metadata")
        if any(
            part == ".env" or part.startswith(".env.") or part.lower() in {"credentials", "secrets"}
            or part.lower().endswith((".pem", ".key"))
            for part in relative.parts
        ):
            raise ValueError("Workspace changes cannot target credential or secret files")
        return str(relative)

    @classmethod
    def _text(cls, value: object, label: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")
        if len(value) > limit:
            raise ValueError(f"{label} is limited to {limit} characters")
        if cls._SENSITIVE_VALUE_RE.search(value):
            raise ValueError(f"{label} appears to contain a credential or secret")
        return value

    async def execute(
        self,
        operation: str,
        summary: str,
        path: str | None = None,
        content: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        command: str | None = None,
        working_dir: str | None = None,
        timeout: int = 60,
        expected_outcome: str | None = None,
        **kwargs: Any,
    ) -> str:
        if self._profile_id != "workspace-build":
            return "Error: Workspace changes require the workspace-build profile"
        if not self._owner_id or not self._session_key:
            return "Error: Missing owner/session context for workspace change proposal"
        if operation not in {"write_file", "edit_file", "exec"}:
            return "Error: Unsupported workspace change operation"

        try:
            summary = self._text(summary, "Summary", 600)
            expected = (
                self._text(expected_outcome, "Expected outcome", 600)
                if expected_outcome is not None
                else None
            )
            payload: dict[str, Any]
            if operation == "write_file":
                relative_path = self._path(path)
                payload = {
                    "path": relative_path,
                    "content": self._text(content, "File content", self._MAX_CONTENT),
                }
                target = f"Write workspace file: {relative_path}"
                default_outcome = f"Writes {relative_path}."
            elif operation == "edit_file":
                relative_path = self._path(path)
                payload = {
                    "path": relative_path,
                    "old_text": self._text(old_text, "Existing text", self._MAX_CONTENT),
                    "new_text": self._text(new_text, "Replacement text", self._MAX_CONTENT),
                    "replace_all": bool(replace_all),
                }
                target = f"Edit workspace file: {relative_path}"
                default_outcome = f"Edits {relative_path}."
            else:
                command_text = self._text(command, "Command", self._MAX_COMMAND)
                relative_dir = self._path(working_dir or ".")
                payload = {
                    "command": command_text,
                    "working_dir": relative_dir,
                    "timeout": max(1, min(int(timeout), 600)),
                }
                target = f"Run workspace command: {command_text[:160]}"
                default_outcome = "Runs the approved command inside the configured workspace."

            expected = expected or default_outcome
            capability_id = "workspace.propose_change"
            fingerprint = self.action_store.compute_fingerprint(
                None, None, capability_id, summary, expected, payload
            )
            action = self.action_store.stage(
                owner_id=self._owner_id,
                session_key=self._session_key,
                profile_id="workspace-build",
                capability_id=capability_id,
                tool_name=operation,
                target=target,
                summary=summary,
                expected_outcome=expected,
                payload_data=payload,
                payload_fingerprint=fingerprint,
                initiating_run_id=self._queued_run_id,
            )
        except (TypeError, ValueError) as exc:
            return f"Error: {exc}"

        return (
            f"Workspace change proposed for review. Action ID: {action.id}. "
            "Review and approve it in Operations before execution."
        )
