"""Approval-bound dispatch for typed actions on one shared browser tab."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from picobot.operations.actions import ProposedActionStore
from picobot.operations.browser_bridge import BrowserBridgeStore


class BrowserActionExecutor:
    """Queue a human-approved browser command and await its safe result."""

    def __init__(self, workspace: Path, *, timeout_s: float = 20.0):
        self.action_store = ProposedActionStore(workspace)
        self.bridge_store = BrowserBridgeStore(workspace)
        self.timeout_s = timeout_s

    async def execute_action(
        self,
        owner_id: str,
        session_key: str,
        action_id: str,
        *,
        payload_fingerprint: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        action = self.action_store._expire_if_needed(
            self.action_store.get(owner_id, action_id, session_key=session_key)
        )
        if action.status != "approved":
            return {"status": "failed", "failure_category": "approval_required", "detail": f"Only an approved action can execute; this action is {action.status}"}
        if profile_id != "browser-action" or action.profile_id != "browser-action":
            return self._refuse(owner_id, session_key, action.id, "profile_mismatch", "Execution refused: switch to Browser action before executing this action.")
        if action.capability_id != "browser.write" or action.tool_name != "browser_action":
            return self._refuse(owner_id, session_key, action.id, "capability_mismatch", "Execution refused: browser capability is not permitted.")
        if action.payload_fingerprint and payload_fingerprint != action.payload_fingerprint:
            return self._refuse(owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal confirmation did not match.")
        try:
            payload = json.loads(action.payload or "{}")
            expected_fingerprint = self.action_store.compute_fingerprint(
                None, None, action.capability_id, action.summary, action.expected_outcome, payload
            )
            if expected_fingerprint != action.payload_fingerprint:
                raise ValueError("payload changed")
            share_id = payload.pop("share_id")
            operation = payload.pop("operation")
            shared = self.bridge_store.get(owner_id, session_key)
            if shared.id != share_id:
                raise ValueError("shared tab changed")
            self.bridge_store.validate_command_payload(operation, payload)
            command = self.bridge_store.queue_command(
                owner_id,
                session_key,
                share_id,
                operation,
                payload,
                action.payload_fingerprint or expected_fingerprint,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._refuse(owner_id, session_key, action.id, "browser_precondition", f"Execution refused: {exc}.")

        deadline = asyncio.get_running_loop().time() + self.timeout_s
        while asyncio.get_running_loop().time() < deadline:
            current = self.bridge_store.command(owner_id, session_key, command.id)
            if current.status in {"succeeded", "failed", "expired", "cancelled"}:
                success = current.status == "succeeded"
                updated = self.action_store.record_execution_result(
                    owner_id,
                    action.id,
                    session_key,
                    success=success,
                    result_summary=current.result_summary or "Browser command completed.",
                    result_ref=f"browser-command:{current.id}",
                    failure_category=current.failure_category,
                )
                return {
                    "status": updated.status,
                    "action_id": updated.id,
                    "command_id": current.id,
                    "result_summary": current.result_summary,
                    "failure_category": current.failure_category,
                }
            await asyncio.sleep(0.1)

        self.bridge_store.cancel_command(owner_id, session_key, command.id, "Browser bridge did not return a result in time.")
        updated = self.action_store.record_execution_result(
            owner_id,
            action.id,
            session_key,
            success=False,
            result_summary="Browser bridge did not return a result in time.",
            result_ref=f"browser-command:{command.id}",
            failure_category="browser_timeout",
        )
        return {"status": updated.status, "action_id": updated.id, "command_id": command.id, "failure_category": "browser_timeout"}

    def _refuse(self, owner_id: str, session_key: str, action_id: str, category: str, detail: str) -> dict[str, Any]:
        try:
            updated = self.action_store.record_execution_result(
                owner_id,
                action_id,
                session_key,
                success=False,
                result_summary=detail,
                failure_category=category,
            )
            return {"status": updated.status, "action_id": updated.id, "failure_category": category, "detail": detail}
        except ValueError:
            return {"status": "failed", "failure_category": category, "detail": detail}
