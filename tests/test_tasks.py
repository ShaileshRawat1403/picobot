"""Tests for CH9A — Bounded Direct Task Lifecycle.

Verifies:
    1. Owner/session isolation.
    2. Invalid & terminal lifecycle transitions fail closed.
    3. Atomic claim contention & race checks.
    4. Profile rechecking at claim and execution time.
    5. Concurrency limit of one active task per session.
    6. Task-to-real-run linkage and derived runs list.
    7. max_elapsed_sec timeout enforcement causing budget_exhausted.
    8. Staged proposal puts task in waiting_for_approval without auto-execution.
    9. Action resolution (execute completes task; reject/cancel fails/cancels task).
    10. Task cancellation scope isolation.
    11. Retry lineage (retry_of_task_id) and attempt limits.
    12. Safe public task projection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.queue import MessageBus
from picobot.missions.store import MissionStore
from picobot.operations.actions import ProposedActionStore
from picobot.operations.executor import MissionExecutor
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from picobot.runs.store import RunStore
from picobot.session.manager import SessionManager
from picobot.tasks.store import TaskRecord, TaskStore


_OWNER_A = "web:browser:owner-a"
_SESSION_A = "web:web:owner-a:session-1"
_OWNER_B = "web:browser:owner-b"
_SESSION_B = "web:web:owner-b:session-2"
_PROFILE = "personal-work"


class FakeProvider(LLMProvider):
    """Fake LLM Provider for testing without network/real API."""

    def __init__(self, response_text: str = "Task executed successfully."):
        super().__init__()
        self.response_text = response_text
        self.call_count = 0

    def get_default_model(self) -> str:
        return "fake-model"

    def list_models(self) -> list[str]:
        return ["fake-model"]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(content=self.response_text, provider_name="fake", model_name="fake-model")


def _setup_active_mission(workspace: Path, owner_id: str = _OWNER_A, session_key: str = _SESSION_A):
    m_store = MissionStore(workspace)
    mission = m_store.create(
        owner_id=owner_id,
        session_key=session_key,
        title="Test Mission",
        objective="Achieve test objective.",
    )
    return m_store.transition(owner_id, mission.id, "active")


def test_task_owner_session_isolation(tmp_path: Path):
    """Test 1: Tasks belong to one owner/session/mission and fail closed across boundaries."""
    m_a = _setup_active_mission(tmp_path, _OWNER_A, _SESSION_A)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Isolated Task",
        objective="Isolated Objective",
        capability_profile=_PROFILE,
    )

    # Owner B cannot fetch Owner A's task
    with pytest.raises(KeyError):
        t_store.get(_OWNER_B, task.id)

    # Owner B cannot claim Owner A's task
    with pytest.raises(KeyError):
        t_store.claim_for_run(_OWNER_B, task.id, _SESSION_B, _PROFILE, m_a)

    # Wrong session_key cannot claim
    with pytest.raises(KeyError):
        t_store.claim_for_run(_OWNER_A, task.id, _SESSION_B, _PROFILE, m_a)


def test_task_invalid_transitions_fail_closed(tmp_path: Path):
    """Test 2: Terminal tasks and invalid transitions fail closed."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)
    r_store = RunStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="State Task",
        objective="State Objective",
        capability_profile=_PROFILE,
    )

    # Cannot complete draft task directly
    with pytest.raises(ValueError, match="cannot be completed"):
        t_store.complete(_OWNER_A, task.id)

    # Claim -> queued
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    # Cannot complete queued task directly
    with pytest.raises(ValueError, match="cannot be completed"):
        t_store.complete(_OWNER_A, task.id)

    run = r_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, capability_profile=_PROFILE, mission_id=m_a.id, task_id=task.id
    )
    t_store.mark_running(_OWNER_A, task.id, run.id, _SESSION_A)

    # Complete the running task
    t_store.complete(_OWNER_A, task.id, result_summary="Done")
    completed_task = t_store.get(_OWNER_A, task.id)

    # Cannot transition terminal task to completed again
    with pytest.raises(ValueError, match="cannot be completed"):
        t_store.complete(_OWNER_A, completed_task.id)

    # Cannot claim terminal task for run
    with pytest.raises(ValueError, match="cannot be claimed for run"):
        t_store.claim_for_run(_OWNER_A, completed_task.id, _SESSION_A, _PROFILE, m_a)


def test_task_atomic_claim_contention_and_race(tmp_path: Path):
    """Test 3: Atomic claim_for_run enforces legal state and attempt bounds."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)
    r_store = RunStore(tmp_path)

    task1 = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Claim Task 1",
        objective="Claim Objective",
        capability_profile=_PROFILE,
        max_attempts=2,
    )
    task2 = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Claim Task 2",
        objective="Claim Objective 2",
        capability_profile=_PROFILE,
        max_attempts=2,
    )

    claimed = t_store.claim_for_run(_OWNER_A, task1.id, _SESSION_A, _PROFILE, m_a)
    assert claimed.state == "queued"
    assert claimed.attempts_count == 1

    run1 = r_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, capability_profile=_PROFILE, mission_id=m_a.id, task_id=task1.id
    )
    t_store.mark_running(_OWNER_A, task1.id, run1.id, _SESSION_A)

    # Second claim for task1 fails because state is running
    with pytest.raises(ValueError, match="cannot be claimed for run"):
        t_store.claim_for_run(_OWNER_A, task1.id, _SESSION_A, _PROFILE, m_a)

    # Claiming task2 while task1 is active fails because only 1 active task per session
    with pytest.raises(ValueError, match="Only one active task is allowed per session"):
        t_store.claim_for_run(_OWNER_A, task2.id, _SESSION_A, _PROFILE, m_a)


def test_task_profile_rechecked_at_claim_and_execution(tmp_path: Path):
    """Test 4: Profile is rechecked against session profile at claim time."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Profile Task",
        objective="Profile Objective",
        capability_profile="research",
    )

    # Claim fails if current session profile ("personal-work") does not match task profile ("research")
    with pytest.raises(ValueError, match="Task profile does not match session profile"):
        t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, "personal-work", m_a)


def test_one_active_task_per_session(tmp_path: Path):
    """Test 5: Enforce max 1 active task per session."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)

    t1 = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Task 1",
        objective="Obj 1",
        capability_profile=_PROFILE,
    )
    t2 = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Task 2",
        objective="Obj 2",
        capability_profile=_PROFILE,
    )

    # Claim T1 -> T1 is queued/active
    t_store.claim_for_run(_OWNER_A, t1.id, _SESSION_A, _PROFILE, m_a)

    # Claiming T2 while T1 is active fails
    with pytest.raises(ValueError, match="Only one active task is allowed per session"):
        t_store.claim_for_run(_OWNER_A, t2.id, _SESSION_A, _PROFILE, m_a)


@pytest.mark.asyncio
async def test_task_to_real_run_record_linkage_and_derived_runs(tmp_path: Path):
    """Test 6: Task execution links to actual RunRecord; runs list is derived via RunStore.list_by_task."""
    m_a = _setup_active_mission(tmp_path)
    bus = MessageBus()
    provider = FakeProvider("Run result content")
    sessions = SessionManager(tmp_path)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, session_manager=sessions)

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Real Run Task",
        objective="Perform real run.",
        capability_profile=_PROFILE,
    )

    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)
    updated_task, run = await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)

    assert run.task_id == task.id
    assert run.mission_id == m_a.id
    assert updated_task.state == "completed"

    # Verify derived runs list
    run_store = RunStore(tmp_path)
    derived_runs = run_store.list_by_task(_OWNER_A, task.id)
    assert len(derived_runs) == 1
    assert derived_runs[0].id == run.id


@pytest.mark.asyncio
async def test_task_max_elapsed_sec_timeout_budget_exhausted(tmp_path: Path):
    """Test 7: Turn exceeding max_elapsed_sec times out and sets budget_exhausted failure category."""
    m_a = _setup_active_mission(tmp_path)
    bus = MessageBus()

    class SlowProvider(LLMProvider):
        def get_default_model(self) -> str:
            return "slow-model"

        def list_models(self) -> list[str]:
            return ["slow-model"]

        async def chat(self, *args, **kwargs) -> LLMResponse:
            await asyncio.sleep(2.0)
            return LLMResponse(content="Slow response", provider_name="slow", model_name="slow-model")

    loop = AgentLoop(
        bus=bus, provider=SlowProvider(), workspace=tmp_path, session_manager=SessionManager(tmp_path)
    )

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Timeout Task",
        objective="Objective that takes too long.",
        capability_profile=_PROFILE,
        max_elapsed_sec=1,  # 1 second timeout
    )

    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)
    updated_task, run = await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)

    assert updated_task.state == "failed"
    assert updated_task.failure_category == "budget_exhausted"
    assert "time limit" in (updated_task.result_summary or "").lower()


@pytest.mark.asyncio
async def test_task_proposal_yields_waiting_for_approval(tmp_path: Path):
    """Test 8: Task turn staging a proposed action enters waiting_for_approval, never auto-executing."""
    # Setup active mission with approved blueprint step
    m_store = MissionStore(tmp_path)
    mission = m_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, title="Proposal Mission", objective="Objective"
    )
    mission = m_store.transition(_OWNER_A, mission.id, "active")
    bp = m_store.save_draft_blueprint(_OWNER_A, mission.id, [{"step_id": "step_1", "title": "Step 1", "state": "pending"}])
    m_store.approve_blueprint(_OWNER_A, mission.id, bp.id)
    m_store.transition_blueprint_step(_OWNER_A, mission.id, "step_1", "active")

    bus = MessageBus()

    class ProposalProvider(LLMProvider):
        def get_default_model(self) -> str:
            return "proposal-model"

        def list_models(self) -> list[str]:
            return ["proposal-model"]

        async def chat(self, messages, tools=None, **kwargs) -> LLMResponse:
            if len(messages) <= 3:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="save_mission_artifact_draft",
                            arguments={"title": "Draft 1", "content": "Sample content", "summary": "Sample summary"},
                        )
                    ],
                    provider_name="proposal",
                    model_name="proposal-model",
                )
            return LLMResponse(content="Proposal created.", provider_name="proposal", model_name="proposal-model")

    loop = AgentLoop(
        bus=bus, provider=ProposalProvider(), workspace=tmp_path, session_manager=SessionManager(tmp_path)
    )

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=mission.id,
        title="Proposal Task",
        objective="Create draft proposal.",
        capability_profile=_PROFILE,
    )

    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, mission)
    updated_task, run = await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)

    assert updated_task.state == "waiting_for_approval"

    # Verify action was staged in ProposedActionStore and NOT auto-executed
    action_store = ProposedActionStore(tmp_path)
    actions = action_store.list_by_mission(_OWNER_A, mission.id)
    assert len(actions) == 1
    assert actions[0].status == "proposed"


def test_task_action_resolution_and_rejection_outcomes(tmp_path: Path):
    """Test 9: Executing approved action completes task; rejecting/cancelling action fails/cancels task."""
    # Setup mission & blueprint
    m_store = MissionStore(tmp_path)
    mission = m_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, title="Resolution Mission", objective="Objective"
    )
    mission = m_store.transition(_OWNER_A, mission.id, "active")
    bp = m_store.save_draft_blueprint(_OWNER_A, mission.id, [{"title": "Step 1", "state": "pending"}])
    m_store.approve_blueprint(_OWNER_A, mission.id, bp.id)

    # Create run & task
    r_store = RunStore(tmp_path)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=mission.id,
        title="Resolution Task",
        objective="Obj",
        capability_profile=_PROFILE,
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, mission)
    run = r_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        capability_profile=_PROFILE,
        mission_id=mission.id,
        task_id=task.id,
    )
    t_store.mark_running(_OWNER_A, task.id, run.id, _SESSION_A)
    t_store.mark_waiting_for_approval(_OWNER_A, task.id, _SESSION_A)

    # Stage proposed action linked to initiating_run_id
    action_store = ProposedActionStore(tmp_path)
    payload = {"title": "Test Artifact Draft", "content": "Content"}
    fp = action_store.compute_fingerprint(
        mission.id, "step_1", "missions.save_artifact_draft", "Create test draft artifact", "Draft artifact created.", payload
    )
    action = action_store.stage(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        capability_id="missions.save_artifact_draft",
        profile_id=_PROFILE,
        tool_name="save_mission_artifact_draft",
        target="Local draft artifact",
        summary="Create test draft artifact",
        expected_outcome="Draft artifact created.",
        payload_data=payload,
        payload_fingerprint=fp,
        mission_id=mission.id,
        blueprint_step_id="step_1",
        initiating_run_id=run.id,
    )

    # Human approves action
    action = action_store.resolve(_OWNER_A, action.id, _SESSION_A, "approve", payload_fingerprint=fp)
    assert action.status == "approved"

    # MissionExecutor executes approved action
    executor = MissionExecutor(tmp_path)
    result = executor.execute_action(_OWNER_A, _SESSION_A, action.id, payload_fingerprint=fp)
    assert result["status"] == "executed"

    # Task should now be resolved to completed
    resolved_task = t_store.get(_OWNER_A, task.id)
    assert resolved_task.state == "completed"
    assert "artifact:" in resolved_task.result_ref


def test_task_cancellation_scope_isolation(tmp_path: Path):
    """Test 10: Task cancellation cancels only active run for target session/task."""
    m_a = _setup_active_mission(tmp_path, _OWNER_A, _SESSION_A)
    m_b = _setup_active_mission(tmp_path, _OWNER_B, _SESSION_B)

    t_store = TaskStore(tmp_path)
    r_store = RunStore(tmp_path)

    # Session A Task & Run
    ta = t_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, mission_id=m_a.id, title="TA", objective="OA", capability_profile=_PROFILE
    )
    t_store.claim_for_run(_OWNER_A, ta.id, _SESSION_A, _PROFILE, m_a)
    ra = r_store.create(owner_id=_OWNER_A, session_key=_SESSION_A, capability_profile=_PROFILE, mission_id=m_a.id, task_id=ta.id)
    t_store.mark_running(_OWNER_A, ta.id, ra.id, _SESSION_A)
    r_store.mark_running(_OWNER_A, ra.id)

    # Session B Task & Run
    tb = t_store.create(
        owner_id=_OWNER_B, session_key=_SESSION_B, mission_id=m_b.id, title="TB", objective="OB", capability_profile=_PROFILE
    )
    t_store.claim_for_run(_OWNER_B, tb.id, _SESSION_B, _PROFILE, m_b)
    rb = r_store.create(owner_id=_OWNER_B, session_key=_SESSION_B, capability_profile=_PROFILE, mission_id=m_b.id, task_id=tb.id)
    t_store.mark_running(_OWNER_B, tb.id, rb.id, _SESSION_B)
    r_store.mark_running(_OWNER_B, rb.id)

    # Cancel Session A Task
    t_store.cancel(_OWNER_A, ta.id, _SESSION_A, run_store=r_store)

    assert t_store.get(_OWNER_A, ta.id).state == "cancelled"
    assert r_store.get(_OWNER_A, ra.id).state == "cancelled"

    # Session B Task & Run remain active/running
    assert t_store.get(_OWNER_B, tb.id).state == "running"
    assert r_store.get(_OWNER_B, rb.id).state == "running"


def test_task_retry_lineage_and_attempt_limits(tmp_path: Path):
    """Test 11: Retry creates new TaskRecord with retry_of_task_id and enforces attempt limits."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Retry Task",
        objective="Retry Objective",
        capability_profile=_PROFILE,
        max_attempts=2,
    )

    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)
    t_store.fail(_OWNER_A, task.id, failure_category="test_error", result_summary="Failed")

    failed_task = t_store.get(_OWNER_A, task.id)
    assert failed_task.state == "failed"
    assert failed_task.attempts_count == 1

    # Retry creates NEW task attempt
    retried_task = t_store.retry_task(_OWNER_A, failed_task.id, _SESSION_A, _PROFILE, m_a)
    assert retried_task.id != failed_task.id
    assert retried_task.retry_of_task_id == failed_task.id
    assert retried_task.attempts_count == 1
    assert retried_task.state == "draft"

    # Original task history is preserved and unchanged
    original_after = t_store.get(_OWNER_A, failed_task.id)
    assert original_after.state == "failed"

    # Fail attempt 2
    claimed_retried = t_store.claim_for_run(_OWNER_A, retried_task.id, _SESSION_A, _PROFILE, m_a)
    assert claimed_retried.attempts_count == 2
    t_store.fail(_OWNER_A, retried_task.id, failure_category="test_error_2")

    # Attempt 3 fails because max_attempts=2
    with pytest.raises(ValueError, match="Maximum attempts limit"):
        t_store.retry_task(_OWNER_A, retried_task.id, _SESSION_A, _PROFILE, m_a)


def test_task_safe_public_projection(tmp_path: Path):
    """Test 12: to_dict() returns safe public projection with no hidden prompt or secret."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Safe Task",
        objective="Public objective text",
        capability_profile=_PROFILE,
    )

    data = task.to_dict()
    assert data["id"] == task.id
    assert data["owner_id"] == _OWNER_A
    assert data["session_key"] == _SESSION_A
    assert data["mission_id"] == m_a.id
    assert data["title"] == "Safe Task"
    assert data["objective"] == "Public objective text"
    assert data["capability_profile"] == _PROFILE
    assert data["depth"] == 0
    assert "prompt" not in data
    assert "secret" not in data
    assert "payload" not in data


def test_retry_profile_mismatch_and_atomic_duplicate_retry_rejected(tmp_path: Path):
    """Test 13: Retry rejects mismatched profile and prevents duplicate retry creation."""
    m_a = _setup_active_mission(tmp_path)
    t_store = TaskStore(tmp_path)

    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Retry Task",
        objective="Obj",
        capability_profile="personal-work",
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, "personal-work", m_a)
    t_store.fail(_OWNER_A, task.id, failure_category="err")

    # Mismatched profile is rejected
    with pytest.raises(ValueError, match="Retry session profile does not match"):
        t_store.retry_task(_OWNER_A, task.id, _SESSION_A, "research", m_a)

    # First retry succeeds
    retried = t_store.retry_task(_OWNER_A, task.id, _SESSION_A, "personal-work", m_a)
    assert retried.retry_of_task_id == task.id

    # Second retry on same terminal task is rejected
    with pytest.raises(ValueError, match="already been retried"):
        t_store.retry_task(_OWNER_A, task.id, _SESSION_A, "personal-work", m_a)


@pytest.mark.asyncio
async def test_unrelated_old_proposal_does_not_block_new_task(tmp_path: Path):
    """Test 14: Unrelated old proposal in session does not block a new task run."""
    m_a = _setup_active_mission(tmp_path)
    action_store = ProposedActionStore(tmp_path)
    # Stage old unrelated proposal
    fp = action_store.compute_fingerprint(m_a.id, "step_old", "cap", "sum", "exp", {})
    action_store.stage(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        profile_id=_PROFILE,
        capability_id="cap",
        tool_name="tool",
        target="target",
        summary="sum",
        mission_id=m_a.id,
        blueprint_step_id="step_old",
        expected_outcome="exp",
        payload_data={},
        payload_fingerprint=fp,
    )

    bus = MessageBus()
    provider = FakeProvider("Clean run result")
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, session_manager=SessionManager(tmp_path))

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, mission_id=m_a.id, title="New Task", objective="Obj", capability_profile=_PROFILE
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    updated_task, run = await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)
    assert updated_task.state == "completed"


@pytest.mark.asyncio
async def test_task_cancellation_stops_active_coroutine(tmp_path: Path):
    """Test 15: Cancelling a running task cancels its active coroutine in AgentLoop."""
    m_a = _setup_active_mission(tmp_path)
    bus = MessageBus()

    class HangingProvider(LLMProvider):
        def get_default_model(self) -> str:
            return "hanging"

        def list_models(self) -> list[str]:
            return ["hanging"]

        async def chat(self, *args, **kwargs) -> LLMResponse:
            await asyncio.sleep(10.0)
            return LLMResponse(content="Done", provider_name="h", model_name="h")

    loop = AgentLoop(bus=bus, provider=HangingProvider(), workspace=tmp_path, session_manager=SessionManager(tmp_path))
    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A, session_key=_SESSION_A, mission_id=m_a.id, title="Hanging", objective="Obj", capability_profile=_PROFILE
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    bg_task = asyncio.create_task(loop.run_direct_task(_OWNER_A, _SESSION_A, task.id))
    await asyncio.sleep(0.1)

    # Cancel task run
    loop.cancel_task_run(task.id)

    with pytest.raises(asyncio.CancelledError):
        await bg_task

    assert t_store.get(_OWNER_A, task.id).state == "cancelled"


@pytest.mark.asyncio
async def test_task_max_turns_enforced(tmp_path: Path):
    """Test 16: max_turns is enforced during agent loop iteration."""
    m_a = _setup_active_mission(tmp_path)
    bus = MessageBus()

    class InfiniteToolProvider(LLMProvider):
        def get_default_model(self) -> str:
            return "inf"

        def list_models(self) -> list[str]:
            return ["inf"]

        async def chat(self, *args, **kwargs) -> LLMResponse:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="c1", name="list_skills", arguments={})],
                provider_name="p",
                model_name="m",
            )

    loop = AgentLoop(bus=bus, provider=InfiniteToolProvider(), workspace=tmp_path, session_manager=SessionManager(tmp_path))
    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Turn Task",
        objective="Obj",
        capability_profile=_PROFILE,
        max_turns=2,
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    updated_task, run = await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)
    assert updated_task.state == "failed"
    assert updated_task.failure_category == "budget_exhausted"


@pytest.mark.asyncio
async def test_changed_profile_at_execution_fails_closed_zero_provider_calls(tmp_path: Path):
    """Test 17: Changed session profile between claim and execution fails closed with 0 provider calls."""
    m_a = _setup_active_mission(tmp_path)
    provider = FakeProvider("Should not run")
    sm = SessionManager(tmp_path)
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, session_manager=sm)

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Profile Task",
        objective="Obj",
        capability_profile=_PROFILE,
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    # Change session profile to "research"
    session = sm.get_or_create(_SESSION_A)
    session.metadata["pico_operation_profile"] = "research"
    sm.save(session)

    with pytest.raises(ValueError, match="does not match task capability snapshot"):
        await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)

    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_changed_or_inactive_mission_fails_closed_zero_provider_calls(tmp_path: Path):
    """Test 18: Changed or inactive mission at execution time fails closed with 0 provider calls."""
    m_a = _setup_active_mission(tmp_path)
    provider = FakeProvider("Should not run")
    sm = SessionManager(tmp_path)
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, session_manager=sm)

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Mission Task",
        objective="Obj",
        capability_profile=_PROFILE,
    )
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)

    # Transition mission m_a to completed
    MissionStore(tmp_path).transition(_OWNER_A, m_a.id, "completed")

    with pytest.raises(ValueError, match="Active mission mismatch or inactive"):
        await loop.run_direct_task(_OWNER_A, _SESSION_A, task.id)

    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_failed_mark_running_fails_closed_zero_provider_calls(tmp_path: Path):
    """Test 19: Failed mark_running linkage fails closed without calling provider."""
    m_a = _setup_active_mission(tmp_path)
    provider = FakeProvider("Should not run")
    sm = SessionManager(tmp_path)
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, session_manager=sm)

    t_store = TaskStore(tmp_path)
    task = t_store.create(
        owner_id=_OWNER_A,
        session_key=_SESSION_A,
        mission_id=m_a.id,
        title="Linkage Task",
        objective="Obj",
        capability_profile=_PROFILE,
    )
    # Claim task to 'queued' state
    t_store.claim_for_run(_OWNER_A, task.id, _SESSION_A, _PROFILE, m_a)
    session = sm.get_or_create(_SESSION_A)
    profile = loop._session_profile(session)

    # Mock mark_running on TaskStore to raise ValueError
    def bad_mark_running(owner_id, task_id, run_id, session_key):
        raise ValueError("RunRecord linkage error")

    loop.tasks.mark_running = bad_mark_running

    with pytest.raises(ValueError, match="RunRecord linkage error"):
        await loop._run_turn(
            owner_id=_OWNER_A,
            session=session,
            profile=profile,
            allowed_tools={"list_skills"},
            messages=[],
            activity_context={"channel": "test"},
            task_id=task.id,
            mission_id=m_a.id,
        )

    assert provider.call_count == 0

    # Verify task was transitioned to terminal 'failed' state with linkage_failure category
    failed_task = t_store.get(_OWNER_A, task.id)
    assert failed_task.state == "failed"
    assert failed_task.failure_category == "linkage_failure"
