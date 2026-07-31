"""Tool for proposing a bounded draft artifact for an active mission.

The model can call this tool to stage a proposed action.  The action
will NOT be executed until a human approves it in the Operations/Missions
UI and explicitly triggers execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from picobot.agent.tools.base import Tool
from picobot.missions.store import MissionStore
from picobot.operations.actions import ProposedActionStore


class SaveMissionArtifactDraftTool(Tool):
    """Propose a bounded draft artifact for an active mission.

    Does NOT create the artifact directly.  Stages a ProposedAction for
    human approval.  Upon approval and human-triggered execution, the
    draft artifact is written to Pico's local artifact store only.
    """

    def __init__(
        self,
        workspace: Path,
        action_store: ProposedActionStore | None = None,
        mission_store: MissionStore | None = None,
    ):
        self.workspace = workspace
        self.action_store = action_store or ProposedActionStore(workspace)
        self.mission_store = mission_store or MissionStore(workspace)
        self._owner_id: str | None = None
        self._session_key: str | None = None
        self._profile_id: str = "personal-work"
        self._queued_run_id: str | None = None
        self._mission_id: str | None = None

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
        self._mission_id = mission_id

    @property
    def name(self) -> str:
        return "save_mission_artifact_draft"

    @property
    def description(self) -> str:
        return (
            "Propose a bounded draft artifact for the current active mission. "
            "This does NOT write the artifact directly — it stages a proposed "
            "action for human review and approval in Operations/Missions UI. "
            "Once approved by a human and executed, the draft artifact is "
            "created inside Pico's local artifact store only (no network, no "
            "shell, no external writes)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the draft artifact (max 240 characters)",
                },
                "content": {
                    "type": "string",
                    "description": "Text content of the draft artifact",
                },
                "summary": {
                    "type": "string",
                    "description": "Human-readable summary shown in the approval UI (max 600 characters)",
                },
            },
            "required": ["title", "content", "summary"],
        }

    async def execute(
        self,
        title: str,
        content: str,
        summary: str,
        **kwargs: Any,
    ) -> str:
        if not self._owner_id or not self._session_key:
            return (
                "Error: Missing turn context for mission action proposal. "
                "The session must be established before using this tool."
            )

        if not self._mission_id:
            return (
                "Error: No active mission is attached to this session. "
                "Attach a mission first."
            )
        try:
            mission = self.mission_store.get(self._owner_id, self._mission_id)
        except KeyError:
            return "Error: The selected mission no longer exists. Attach an active mission first."
        if mission.session_key != self._session_key or mission.state != "active":
            return "Error: The selected mission is no longer active in this session."

        if not all(isinstance(value, str) and value.strip() for value in (title, content, summary)):
            return "Error: title, content, and summary must be non-empty text."

        approved_bp = self.mission_store.get_approved_blueprint(self._owner_id, mission.id)
        if not approved_bp:
            return (
                "Error: The active mission has no human-approved workflow blueprint. "
                "A blueprint must be drafted and approved before executing steps."
            )

        active_step = next((s for s in approved_bp.steps if s.state == "active"), None)
        if not active_step:
            return (
                "Error: The active mission blueprint has no currently active step. "
                "A human must advance the blueprint step before a new action can be proposed."
            )

        payload_data = {
            "title": title.strip(),
            "content": content,
        }
        expected_outcome = f"Creates local draft artifact '{title.strip()[:60]}'."

        fingerprint = self.action_store.compute_fingerprint(
            mission_id=mission.id,
            blueprint_step_id=active_step.step_id,
            capability_id="missions.save_artifact_draft",
            summary=summary.strip(),
            expected_outcome=expected_outcome,
            payload_data=payload_data,
        )

        action = self.action_store.stage(
            owner_id=self._owner_id,
            session_key=self._session_key,
            profile_id=self._profile_id,
            capability_id="missions.save_artifact_draft",
            tool_name="save_mission_artifact_draft",
            target=f"Draft artifact: {title.strip()[:60]}",
            summary=summary.strip()[:600],
            mission_id=mission.id,
            blueprint_step_id=active_step.step_id,
            expected_outcome=expected_outcome,
            payload_data=payload_data,
            payload_fingerprint=fingerprint,
            initiating_run_id=self._queued_run_id,
        )

        return (
            f"Action proposed for human review. Action ID: {action.id}. "
            f"Go to Operations to approve — approving will create a local draft "
            f"artifact titled '{title.strip()[:60]}'."
        )
