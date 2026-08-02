"""Approval-bound, bounded actions for the explicitly shared browser tab."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from picobot.agent.tools.base import Tool
from picobot.operations.actions import ProposedActionStore
from picobot.operations.browser_bridge import BrowserBridgeStore


class BrowserActionTool(Tool):
    """Stage navigate/click/type operations; never mutate the browser directly."""

    def __init__(
        self,
        workspace: Path,
        action_store: ProposedActionStore | None = None,
        bridge_store: BrowserBridgeStore | None = None,
    ):
        self.action_store = action_store or ProposedActionStore(workspace)
        self.bridge_store = bridge_store or BrowserBridgeStore(workspace)
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
        return "browser_action"

    @property
    def description(self) -> str:
        return (
            "Propose one bounded navigate, click, or non-sensitive type action "
            "for the explicitly shared browser tab. The action is never sent "
            "until the owner reviews and approves it in Operations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["navigate", "click", "type"]},
                "url": {"type": "string", "description": "Absolute HTTP(S) URL for navigate."},
                "selector": {"type": "string", "description": "Bounded CSS selector for click or type."},
                "text": {"type": "string", "description": "Non-sensitive text for type."},
                "summary": {"type": "string", "description": "Plain-language approval summary."},
                "expected_outcome": {"type": "string", "description": "Expected visible outcome."},
            },
            "required": ["operation", "summary"],
        }

    async def execute(
        self,
        operation: str,
        summary: str,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        expected_outcome: str | None = None,
        **_: Any,
    ) -> str:
        if self._profile_id != "browser-action":
            return "Error: Browser writes require the browser-action profile"
        if not self._owner_id or not self._session_key:
            return "Error: Missing owner/session context for browser action proposal"
        try:
            shared = self.bridge_store.get(self._owner_id, self._session_key)
            payload: dict[str, Any] = {"url": url} if operation == "navigate" else {"selector": selector}
            if operation == "type":
                payload["text"] = text
            target, clean_payload = self.bridge_store.validate_command_payload(operation, payload)
            summary = " ".join(summary.split())
            if not summary or len(summary) > 600:
                raise ValueError("Summary is required and limited to 600 characters")
            expected = " ".join((expected_outcome or f"Browser completes {operation} on the shared tab.").split())
            if len(expected) > 600:
                raise ValueError("Expected outcome is limited to 600 characters")
            capability_id = "browser.write"
            action_payload = {"share_id": shared.id, "operation": operation, **clean_payload}
            fingerprint = self.action_store.compute_fingerprint(
                None, None, capability_id, summary, expected, action_payload
            )
            action = self.action_store.stage(
                owner_id=self._owner_id,
                session_key=self._session_key,
                profile_id="browser-action",
                capability_id=capability_id,
                tool_name=self.name,
                target=f"Shared tab {shared.domain}: {target}",
                summary=summary,
                expected_outcome=expected,
                payload_data=action_payload,
                payload_fingerprint=fingerprint,
                initiating_run_id=self._queued_run_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return f"Error: {exc}"
        return (
            f"Browser action proposed for review. Action ID: {action.id}. "
            "Review and approve it in Operations before execution."
        )
