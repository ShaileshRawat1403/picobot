"""Tests for CH8 — governed mission execution.

Verifies the full lifecycle:
    1. Stage a proposed action linked to a mission and blueprint step.
    2. Human approves the action.
    3. MissionExecutor re-validates all authority checks.
    4. Executor dispatches the safe internal save_mission_artifact_draft capability.
    5. Result is recorded against the action; mission gets an evidence checkpoint.
    6. Blueprint step is NOT automatically advanced.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from picobot.agent.tools.mission_artifact import SaveMissionArtifactDraftTool
from picobot.artifacts.store import ArtifactStore
from picobot.missions.store import MissionStore
from picobot.operations.actions import ProposedActionStore
from picobot.operations.executor import MissionExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWNER = "web:browser:test-owner"
_SESSION = "web:web:test-owner:session-a"
_PROFILE = "mission-work"


def _make_active_mission_with_approved_bp(mission_store: MissionStore) -> tuple:
    """Return (mission, blueprint, active_step)."""
    mission = mission_store.create(
        owner_id=_OWNER,
        session_key=_SESSION,
        title="Test Execution Mission",
        objective="Prove the governed execution contract works end-to-end.",
        current_step="Draft the report.",
    )
    mission = mission_store.transition(
        _OWNER, mission.id, "active", blocked_reason=None
    )
    bp = mission_store.save_draft_blueprint(
        _OWNER,
        mission.id,
        [{"step_id": "step_1", "title": "Draft report", "state": "pending"}],
    )
    bp = mission_store.approve_blueprint(_OWNER, mission.id, blueprint_id=bp.id)
    # After approval the first step should be active
    active_step = next(s for s in bp.steps if s.state == "active")
    return mission, bp, active_step


def _stage_artifact_action(
    action_store: ProposedActionStore,
    mission_id: str,
    step_id: str,
    title: str = "Draft report v1",
    content: str = "# Draft\n\nContent here.",
) -> object:
    payload_data = {"title": title, "content": content}
    fingerprint = action_store.compute_fingerprint(
        mission_id=mission_id,
        blueprint_step_id=step_id,
        capability_id="missions.save_artifact_draft",
        summary=f"Save draft: {title}",
        expected_outcome=f"Creates local draft artifact '{title}'.",
        payload_data=payload_data,
    )
    return action_store.stage(
        owner_id=_OWNER,
        session_key=_SESSION,
        profile_id=_PROFILE,
        capability_id="missions.save_artifact_draft",
        tool_name="save_mission_artifact_draft",
        target=f"Draft artifact: {title}",
        summary=f"Save draft: {title}",
        mission_id=mission_id,
        blueprint_step_id=step_id,
        expected_outcome=f"Creates local draft artifact '{title}'.",
        payload_data=payload_data,
        payload_fingerprint=fingerprint,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_execute_lifecycle_creates_artifact_and_evidence(tmp_path: Path) -> None:
    """Full happy path: propose → approve → execute → artifact + checkpoint exist."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)
    artifact_store = ArtifactStore(workspace)
    executor = MissionExecutor(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)

    action = _stage_artifact_action(action_store, mission.id, active_step.step_id)
    assert action.status == "proposed"

    # Human approves
    approved = action_store.resolve(
        _OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint
    )
    assert approved.status == "approved"

    # Execute
    result = executor.execute_action(
        _OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint
    )
    assert result["status"] == "executed"
    artifact_id = result["artifact_id"]

    # Artifact exists in the store
    artifact = artifact_store.get(_OWNER, artifact_id)
    assert artifact.title == "Draft report v1"
    assert artifact.status == "draft"
    assert artifact.source_run_id is None
    assert artifact.source_mission_id == mission.id

    # Action was marked executed
    final_action = action_store.get(_OWNER, action.id, session_key=_SESSION)
    assert final_action.status == "executed"
    assert final_action.result_ref == f"artifact:{artifact_id}"

    # Evidence checkpoint was written to mission
    checkpoints = mission_store.checkpoints(_OWNER, mission.id)
    assert any("artifact" in cp.summary.lower() for cp in checkpoints)

    # Blueprint step is still active (NOT automatically advanced)
    bp = mission_store.get_approved_blueprint(_OWNER, mission.id)
    assert bp is not None
    still_active = next((s for s in bp.steps if s.step_id == active_step.step_id), None)
    assert still_active is not None
    assert still_active.state == "active"


def test_execute_refuses_non_approved_action(tmp_path: Path) -> None:
    """A proposed (not yet approved) action cannot be executed."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)
    executor = MissionExecutor(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)
    action = _stage_artifact_action(action_store, mission.id, active_step.step_id)
    assert action.status == "proposed"

    result = executor.execute_action(
        _OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint
    )
    # Executor returns a structured failure dict (never raises)
    assert result["status"] == "failed"
    assert result["failure_category"] in ("execution_error", "mission_not_active", "blueprint_mismatch")


def test_execute_refuses_when_mission_not_active(tmp_path: Path) -> None:
    """Execution is refused when the linked mission is no longer active."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)
    executor = MissionExecutor(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)
    action = _stage_artifact_action(action_store, mission.id, active_step.step_id)
    action_store.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)

    # Complete the mission before executing the action
    mission_store.transition(_OWNER, mission.id, "completed", blocked_reason=None)

    result = executor.execute_action(_OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint)
    assert result["status"] == "failed"
    assert result["failure_category"] == "mission_not_active"


def test_execute_refuses_when_no_approved_blueprint(tmp_path: Path) -> None:
    """Execution is refused when mission has no approved blueprint at execution time."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)
    executor = MissionExecutor(workspace)

    # Create mission and approve a blueprint, then supersede it without approving another
    mission = mission_store.create(
        owner_id=_OWNER,
        session_key=_SESSION,
        title="No-Blueprint Mission",
        objective="Test blueprint guard.",
    )
    mission = mission_store.transition(_OWNER, mission.id, "active", blocked_reason=None)
    bp = mission_store.save_draft_blueprint(
        _OWNER,
        mission.id,
        [{"step_id": "step_1", "title": "Draft report", "state": "pending"}],
    )
    bp = mission_store.approve_blueprint(_OWNER, mission.id, blueprint_id=bp.id)
    active_step = next(s for s in bp.steps if s.state == "active")

    action = _stage_artifact_action(action_store, mission.id, active_step.step_id)
    action_store.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)

    # Supersede the blueprint by saving and approving another one
    bp2 = mission_store.save_draft_blueprint(
        _OWNER,
        mission.id,
        [{"step_id": "step_x", "title": "New step", "state": "pending"}],
    )
    mission_store.approve_blueprint(_OWNER, mission.id, blueprint_id=bp2.id)

    # Now the original step_id no longer matches the active step of the approved blueprint
    result = executor.execute_action(_OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint)
    assert result["status"] == "failed"
    assert result["failure_category"] == "blueprint_mismatch"


def test_cancel_action_prevents_execution(tmp_path: Path) -> None:
    """A cancelled action cannot be executed."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)
    executor = MissionExecutor(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)
    action = _stage_artifact_action(action_store, mission.id, active_step.step_id)
    action_store.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)
    action_store.cancel(_OWNER, action.id, _SESSION)

    cancelled = action_store.get(_OWNER, action.id, session_key=_SESSION)
    assert cancelled.status == "cancelled"

    result = executor.execute_action(_OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint)
    assert result["status"] == "failed"


def test_list_by_mission_returns_actions(tmp_path: Path) -> None:
    """list_by_mission returns actions scoped to the given mission."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)

    # Stage two actions for same mission
    a1 = _stage_artifact_action(action_store, mission.id, active_step.step_id, title="Draft A")
    a2 = _stage_artifact_action(action_store, mission.id, active_step.step_id, title="Draft B")

    results = action_store.list_by_mission(_OWNER, mission.id)
    ids = {r.id for r in results}
    assert a1.id in ids
    assert a2.id in ids

    # Actions for a different owner should not appear
    other_results = action_store.list_by_mission("web:browser:other", mission.id)
    assert not other_results


def test_to_dict_never_exposes_payload(tmp_path: Path) -> None:
    """to_dict must never include the raw payload field."""
    workspace = tmp_path / "pico"
    action_store = ProposedActionStore(workspace)
    mission_store = MissionStore(workspace)

    mission, _, active_step = _make_active_mission_with_approved_bp(mission_store)
    action = _stage_artifact_action(
        action_store, mission.id, active_step.step_id, content="SUPER_SECRET_CONTENT"
    )

    d = action.to_dict()
    assert "payload" not in d
    # The raw content must NOT appear in any value
    serialized = json.dumps(d)
    assert "SUPER_SECRET_CONTENT" not in serialized


def test_approval_requires_the_exact_proposal_fingerprint(tmp_path: Path) -> None:
    workspace = tmp_path / "pico"
    actions = ProposedActionStore(workspace)
    mission, _, step = _make_active_mission_with_approved_bp(MissionStore(workspace))
    action = _stage_artifact_action(actions, mission.id, step.step_id)

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        actions.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint="not-the-proposal")

    assert actions.get(_OWNER, action.id, session_key=_SESSION).status == "proposed"


def test_approved_action_expires_before_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "pico"
    actions = ProposedActionStore(workspace)
    mission, _, step = _make_active_mission_with_approved_bp(MissionStore(workspace))
    action = _stage_artifact_action(actions, mission.id, step.step_id)
    actions.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)
    with actions._connect() as connection:
        connection.execute(
            "UPDATE proposed_actions SET expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), action.id),
        )

    result = MissionExecutor(workspace).execute_action(
        _OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint
    )
    assert result["status"] == "failed"
    assert actions.get(_OWNER, action.id, session_key=_SESSION).status == "expired"


def test_payload_tampering_is_refused_before_artifact_write(tmp_path: Path) -> None:
    workspace = tmp_path / "pico"
    actions = ProposedActionStore(workspace)
    mission, _, step = _make_active_mission_with_approved_bp(MissionStore(workspace))
    action = _stage_artifact_action(actions, mission.id, step.step_id)
    actions.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)
    with actions._connect() as connection:
        connection.execute(
            "UPDATE proposed_actions SET payload = ? WHERE id = ?",
            (json.dumps({"title": "Tampered", "content": "Not approved"}), action.id),
        )

    result = MissionExecutor(workspace).execute_action(
        _OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint
    )
    assert result == {
        "status": "failed",
        "failure_category": "payload_mismatch",
        "detail": "Execution refused: proposal payload changed.",
    }
    assert actions.get(_OWNER, action.id, session_key=_SESSION).status == "failed"
    assert not ArtifactStore(workspace).list(_OWNER)


def test_mission_session_and_profile_are_rechecked_at_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "pico"
    actions = ProposedActionStore(workspace)
    missions = MissionStore(workspace)
    executor = MissionExecutor(workspace)
    foreign_session = "web:web:test-owner:session-b"
    mission = missions.create(
        owner_id=_OWNER,
        session_key=foreign_session,
        title="Other session mission",
        objective="Must never execute from session A.",
    )
    mission = missions.transition(_OWNER, mission.id, "active", blocked_reason=None)
    blueprint = missions.save_draft_blueprint(
        _OWNER, mission.id, [{"step_id": "step_1", "title": "Draft", "state": "pending"}]
    )
    blueprint = missions.approve_blueprint(_OWNER, mission.id, blueprint_id=blueprint.id)
    step = next(item for item in blueprint.steps if item.state == "active")
    action = _stage_artifact_action(actions, mission.id, step.step_id)
    actions.resolve(_OWNER, action.id, _SESSION, "approve", payload_fingerprint=action.payload_fingerprint)

    result = executor.execute_action(_OWNER, _SESSION, action.id, payload_fingerprint=action.payload_fingerprint)
    assert result["failure_category"] == "mission_session_mismatch"

    local_mission, _, local_step = _make_active_mission_with_approved_bp(missions)
    profile_mismatch = actions.stage(
        owner_id=_OWNER,
        session_key=_SESSION,
        profile_id="not-a-profile",
        capability_id="missions.save_artifact_draft",
        tool_name="save_mission_artifact_draft",
        target="Draft artifact: denied",
        summary="Save denied draft",
        mission_id=local_mission.id,
        blueprint_step_id=local_step.step_id,
        expected_outcome="Creates a local draft artifact.",
        payload_data={"title": "Denied", "content": "No write"},
    )
    actions.resolve(
        _OWNER, profile_mismatch.id, _SESSION, "approve", payload_fingerprint=profile_mismatch.payload_fingerprint
    )
    result = executor.execute_action(
        _OWNER, _SESSION, profile_mismatch.id, payload_fingerprint=profile_mismatch.payload_fingerprint
    )
    assert result["failure_category"] == "capability_mismatch"


@pytest.mark.asyncio
async def test_proposal_tool_uses_the_selected_mission_not_first_active(tmp_path: Path) -> None:
    workspace = tmp_path / "pico"
    actions = ProposedActionStore(workspace)
    missions = MissionStore(workspace)
    first, _, _ = _make_active_mission_with_approved_bp(missions)
    selected, _, _ = _make_active_mission_with_approved_bp(missions)
    tool = SaveMissionArtifactDraftTool(workspace, action_store=actions, mission_store=missions)
    tool.set_turn_context(
        owner_id=_OWNER,
        session_key=_SESSION,
        profile_id=_PROFILE,
        mission_id=selected.id,
    )

    response = await tool.execute("Draft", "Content", "Save a draft for the selected mission.")
    assert response.startswith("Action proposed")
    assert not actions.list_by_mission(_OWNER, first.id)
    staged = actions.list_by_mission(_OWNER, selected.id)
    assert len(staged) == 1
    assert staged[0].capability_id == "missions.save_artifact_draft"
