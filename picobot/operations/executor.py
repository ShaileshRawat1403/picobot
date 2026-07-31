"""Governed Mission Action Executor.

Only executes human-approved proposed actions after re-verifying all
authority checks.  Blueprint step advancement is intentionally NOT
automatic — that remains a human action.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from picobot.artifacts.store import ArtifactStore
from picobot.missions.store import MissionStore
from picobot.operations.actions import ProposedActionStore
from picobot.operations.registry import CapabilityRegistry


class MissionExecutor:
    """Safely executes human-approved proposed actions."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.action_store = ProposedActionStore(workspace)
        self.mission_store = MissionStore(workspace)
        self.artifact_store = ArtifactStore(workspace)

    def execute_action(
        self,
        owner_id: str,
        session_key: str,
        action_id: str,
        *,
        payload_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Execute a human-approved proposed action after re-verifying all authority checks.

        Returns a safe metadata dict.  Never exposes credentials, raw payload
        strings, internal stack traces, or private provider errors.
        """
        action = self.action_store.get(owner_id, action_id, session_key=session_key)
        action = self.action_store._expire_if_needed(action)

        # 1. Lifecycle guard
        if action.status != "approved":
            return {
                "status": "failed",
                "failure_category": "execution_error",
                "detail": f"Only an approved action can execute; this action is {action.status}",
            }

        # 2. Payload fingerprint guard
        if action.payload_fingerprint and payload_fingerprint != action.payload_fingerprint:
            return self._refuse(
                owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal confirmation did not match."
            )
        try:
            payload = json.loads(action.payload or "null")
            recomputed_fingerprint = self.action_store.compute_fingerprint(
                action.mission_id,
                action.blueprint_step_id,
                action.capability_id,
                action.summary,
                action.expected_outcome,
                payload,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._refuse(
                owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal payload is invalid."
            )
        if not action.payload_fingerprint or action.payload_fingerprint != recomputed_fingerprint:
            return self._refuse(
                owner_id, session_key, action.id, "payload_mismatch", "Execution refused: proposal payload changed."
            )

        # 3. Mission & Blueprint authority checks
        if not action.mission_id or not action.blueprint_step_id:
            return self._refuse(
                owner_id, session_key, action.id, "mission_context_missing", "Execution refused: mission context is missing."
            )
        refusal = self._check_mission_authority(owner_id, session_key, action)
        if refusal:
            return refusal

        capability = CapabilityRegistry().capability_for_tool(action.tool_name)
        if (
            capability is None
            or capability.id != action.capability_id
            or action.profile_id not in capability.profiles
        ):
            return self._refuse(
                owner_id, session_key, action.id, "capability_mismatch", "Execution refused: capability is not permitted for this profile."
            )

        # 4. Capability dispatch
        return self._dispatch(owner_id, session_key, action)

    # ------------------------------------------------------------------
    # Authority checking helpers
    # ------------------------------------------------------------------

    def _check_mission_authority(
        self,
        owner_id: str,
        session_key: str,
        action: Any,
    ) -> dict[str, Any] | None:
        """Return a failure dict if mission authority checks fail, else None."""
        try:
            mission = self.mission_store.get(owner_id, action.mission_id)
        except KeyError:
            return self._refuse(owner_id, session_key, action.id, "mission_not_found", "Execution refused: mission no longer exists.")

        if mission.session_key != session_key:
            return self._refuse(owner_id, session_key, action.id, "mission_session_mismatch", "Execution refused: mission does not belong to this session.")

        if mission.state != "active":
            return self._refuse(owner_id, session_key, action.id, "mission_not_active", "Execution refused: mission is not active.")

        approved_bp = self.mission_store.get_approved_blueprint(owner_id, mission.id)
        if not approved_bp:
            return self._refuse(owner_id, session_key, action.id, "blueprint_mismatch", "Execution refused: mission has no approved blueprint.")

        active_step = next(
            (s for s in approved_bp.steps if s.state in {"active", "blocked"}), None
        )
        if (
            not active_step
            or active_step.step_id != action.blueprint_step_id
            or active_step.state == "blocked"
        ):
            return self._refuse(owner_id, session_key, action.id, "blueprint_mismatch", "Execution refused: active blueprint step has changed or is blocked.")

        return None  # authority checks passed

    # ------------------------------------------------------------------
    # Capability dispatcher
    # ------------------------------------------------------------------

    def _dispatch(
        self, owner_id: str, session_key: str, action: Any
    ) -> dict[str, Any]:
        if action.capability_id == "missions.save_artifact_draft":
            return self._exec_save_artifact_draft(owner_id, session_key, action)

        return self._refuse(owner_id, session_key, action.id, "execution_error", "Execution refused: capability is unavailable.")

    # ------------------------------------------------------------------
    # Proof executor: save_mission_artifact_draft
    # ------------------------------------------------------------------

    def _exec_save_artifact_draft(
        self, owner_id: str, session_key: str, action: Any
    ) -> dict[str, Any]:
        """Write a bounded draft artifact inside Pico's local artifact store.

        No network, shell, browser write, external integration, or filesystem
        access outside Pico's normal artifact path is performed here.
        """
        try:
            payload = json.loads(action.payload or "{}")
            title = (payload.get("title") or "Untitled Draft").strip()
            content = payload.get("content") or ""

            # ArtifactStore.create takes session_key, not session_id
            artifact = self.artifact_store.create(
                owner_id=owner_id,
                session_key=session_key,
                title=title,
                content=content,
                kind="draft",  # always draft; kind param not used to prevent abuse
            )

            result_ref = f"artifact:{artifact.id}"
            result_summary = f"Draft artifact '{artifact.title[:60]}' created."

            updated_action = self.action_store.record_execution_result(
                owner_id,
                action.id,
                session_key,
                success=True,
                result_summary=result_summary,
                result_ref=result_ref,
            )

            # Record bounded evidence in MissionStore if mission is linked.
            # Blueprint step is NOT automatically advanced.
            if action.mission_id:
                try:
                    self.mission_store.add_checkpoint(
                        owner_id,
                        action.mission_id,
                        kind="evidence",
                        summary=(
                            f"Action [{action.id[:8]}] executed for step "
                            f"[{action.blueprint_step_id}]: "
                            f"Draft artifact '{artifact.title[:60]}' created."
                        ),
                    )
                except Exception:
                    # Evidence recording failure must not roll back a successful execution.
                    pass

            return {
                "status": "executed",
                "action_id": updated_action.id,
                "artifact_id": artifact.id,
                "result_ref": result_ref,
                "result_summary": result_summary,
            }

        except Exception:
            return self._refuse(
                owner_id, session_key, action.id, "execution_error", "Execution failed in local artifact store."
            )
    def _refuse(
        self,
        owner_id: str,
        session_key: str,
        action_id: str,
        failure_category: str,
        detail: str,
    ) -> dict[str, Any]:
        """Persist one safe terminal refusal for an approved action."""
        try:
            self.action_store.record_execution_result(
                owner_id,
                action_id,
                session_key,
                success=False,
                failure_category=failure_category,
                result_summary=detail,
            )
        except ValueError:
            # A concurrent human cancellation wins. Do not leak internal state.
            pass
        return {"status": "failed", "failure_category": failure_category, "detail": detail}
