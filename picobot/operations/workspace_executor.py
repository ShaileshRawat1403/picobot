"""Executor for explicitly approved, workspace-scoped changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from picobot.agent.tools.filesystem import EditFileTool, WriteFileTool
from picobot.agent.tools.shell import ExecTool
from picobot.operations.actions import ProposedActionStore
from picobot.operations.registry import CapabilityRegistry


class WorkspaceExecutor:
    """Execute one approved workspace action after all authority checks."""

    _PROFILE = "workspace-build"
    _TOOLS = {"write_file", "edit_file", "exec"}
    _CAPABILITIES = {"write_file", "edit_file", "exec"}
    _BLOCKED_PARTS = {".git", ".picobot"}

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.action_store = ProposedActionStore(workspace)
        self.write_tool = WriteFileTool(workspace=self.workspace, allowed_dir=self.workspace)
        self.edit_tool = EditFileTool(workspace=self.workspace, allowed_dir=self.workspace)
        self.exec_tool = ExecTool(
            working_dir=str(self.workspace),
            restrict_to_workspace=True,
        )

    async def execute_action(
        self,
        owner_id: str,
        session_key: str,
        action_id: str,
        *,
        payload_fingerprint: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            action = self.action_store.get(owner_id, action_id, session_key=session_key)
        except KeyError:
            return {"status": "failed", "failure_category": "not_found", "detail": "Workspace action was not found for this session."}
        action = self.action_store._expire_if_needed(action)

        if action.status != "approved":
            return {"status": "failed", "failure_category": "execution_error", "detail": f"Only an approved action can execute; this action is {action.status}"}
        if profile_id != self._PROFILE or action.profile_id != self._PROFILE:
            # A profile mismatch is a precondition failure, not a terminal
            # action outcome.  The owner can switch profiles and retry the
            # still-approved proposal without losing the approval.
            return {"status": "failed", "failure_category": "profile_mismatch", "detail": "Execution refused: switch to Workspace build before executing this action."}
        if action.tool_name not in self._TOOLS:
            return self._refuse(owner_id, session_key, action.id, "capability_mismatch", "Execution refused: workspace capability is unavailable.")
        if action.payload_fingerprint and payload_fingerprint != action.payload_fingerprint:
            return self._refuse(owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal confirmation did not match.")

        try:
            payload = json.loads(action.payload or "null")
            recomputed = self.action_store.compute_fingerprint(
                action.mission_id,
                action.blueprint_step_id,
                action.capability_id,
                action.summary,
                action.expected_outcome,
                payload,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._refuse(owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal payload is invalid.")
        if not action.payload_fingerprint or recomputed != action.payload_fingerprint:
            return self._refuse(owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal payload changed.")

        capability = CapabilityRegistry().capability_for_tool("propose_workspace_change")
        if (
            capability is None
            or capability.id != action.capability_id
            or action.profile_id not in capability.profiles
            or action.tool_name not in self._CAPABILITIES
        ):
            return self._refuse(owner_id, session_key, action.id, "capability_mismatch", "Execution refused: capability is not permitted for this profile.")

        try:
            result = await self._dispatch(action.tool_name, payload)
        except Exception:
            return self._refuse(owner_id, session_key, action.id, "execution_error", "Workspace change failed safely.")
        if result.startswith("Error"):
            return self._refuse(owner_id, session_key, action.id, "execution_error", "Workspace change failed safely.")

        result_ref = self._result_ref(action.tool_name, payload)
        summary = {
            "write_file": "Approved workspace file was written.",
            "edit_file": "Approved workspace file was edited.",
            "exec": "Approved workspace command completed.",
        }[action.tool_name]
        try:
            updated = self.action_store.record_execution_result(
                owner_id,
                action.id,
                session_key,
                success=True,
                result_summary=summary,
                result_ref=result_ref,
            )
        except ValueError:
            return {"status": "failed", "failure_category": "execution_error", "detail": "Workspace action state changed before completion was recorded."}
        self._resolve_linked_task(owner_id, session_key, action, success=True, result_summary=summary, result_ref=result_ref)
        return {
            "status": "executed",
            "action_id": updated.id,
            "result_ref": result_ref,
            "result_summary": summary,
        }

    async def _dispatch(self, tool_name: str, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return "Error: invalid workspace action payload"
        if tool_name == "write_file":
            path = self._safe_path(payload.get("path"))
            return await self.write_tool.execute(path=path, content=payload.get("content", ""))
        if tool_name == "edit_file":
            path = self._safe_path(payload.get("path"))
            return await self.edit_tool.execute(
                path=path,
                old_text=payload.get("old_text", ""),
                new_text=payload.get("new_text", ""),
                replace_all=bool(payload.get("replace_all", False)),
            )
        if tool_name == "exec":
            working_dir = self._safe_path(payload.get("working_dir", "."))
            return await self.exec_tool.execute(
                command=payload.get("command", ""),
                working_dir=str(self.workspace / working_dir),
                timeout=int(payload.get("timeout", 60)),
            )
        return "Error: unsupported workspace action"

    def _safe_path(self, raw_path: object) -> str:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Workspace action path is required")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        relative = resolved.relative_to(self.workspace)
        if any(part in self._BLOCKED_PARTS for part in relative.parts):
            raise ValueError("Workspace action targeted protected runtime metadata")
        if any(
            part == ".env" or part.startswith(".env.") or part.lower() in {"credentials", "secrets"}
            or part.lower().endswith((".pem", ".key"))
            for part in relative.parts
        ):
            raise ValueError("Workspace action targeted protected credential material")
        return str(relative)

    def _result_ref(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name in {"write_file", "edit_file"}:
            return f"workspace:{self._safe_path(payload.get('path'))}"
        return "workspace-command"

    def _refuse(self, owner_id: str, session_key: str, action_id: str, category: str, detail: str) -> dict[str, Any]:
        try:
            self.action_store.record_execution_result(
                owner_id,
                action_id,
                session_key,
                success=False,
                failure_category=category,
                result_summary=detail,
            )
        except ValueError:
            pass
        return {"status": "failed", "failure_category": category, "detail": detail}

    def _resolve_linked_task(
        self,
        owner_id: str,
        session_key: str,
        action: Any,
        *,
        success: bool,
        result_summary: str,
        result_ref: str | None = None,
    ) -> None:
        if not getattr(action, "initiating_run_id", None):
            return
        try:
            from picobot.runs.store import RunStore
            from picobot.tasks.store import TaskStore

            run = RunStore(self.workspace).get(owner_id, action.initiating_run_id)
            if run and getattr(run, "task_id", None):
                task_store = TaskStore(self.workspace)
                if success:
                    task_store.complete(
                        owner_id,
                        run.task_id,
                        result_summary=result_summary,
                        result_ref=result_ref,
                        session_key=session_key,
                    )
                else:
                    task_store.fail(
                        owner_id,
                        run.task_id,
                        failure_category="execution_error",
                        result_summary=result_summary,
                        session_key=session_key,
                    )
        except Exception:
            pass
