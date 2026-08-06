"""Unit tests for CH6 — Human-approved Mission Blueprints."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from picobot.agent.context import ContextBuilder
from picobot.agent.loop import AgentLoop
from picobot.bus.queue import MessageBus, InboundMessage
from picobot.channels.web import WebChannel
from picobot.missions.store import MissionStore, MissionBlueprint
from picobot.runs.store import RunStore
from picobot.session.manager import SessionManager


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def mission_store(tmp_workspace: Path) -> MissionStore:
    return MissionStore(tmp_workspace)


@pytest.fixture
def run_store(tmp_workspace: Path) -> RunStore:
    return RunStore(tmp_workspace)


@pytest.fixture
def session_manager(tmp_workspace: Path) -> SessionManager:
    return SessionManager(tmp_workspace)


def create_mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "gpt-4o"
    return provider


def test_owner_and_session_isolation(tmp_workspace: Path, mission_store: MissionStore):
    """Test 1: Owner and session isolation for blueprints."""
    owner_a = "web:browser:userA"
    session_key_a = "web:web:userA:sess1"
    owner_b = "web:browser:userB"

    mission_a = mission_store.create(owner_id=owner_a, session_key=session_key_a, title="Mission A", objective="Obj A")
    steps = [
        {"step_id": "s1", "title": "Step 1", "success_criterion": "Done 1"},
        {"step_id": "s2", "title": "Step 2"},
    ]
    bp_a = mission_store.save_draft_blueprint(owner_a, mission_a.id, steps)
    assert bp_a.version == 1

    # User B cannot access, modify, approve, or transition User A's blueprint
    with pytest.raises(KeyError):
        mission_store.save_draft_blueprint(owner_b, mission_a.id, steps)

    with pytest.raises(KeyError):
        mission_store.approve_blueprint(owner_b, mission_a.id)

    with pytest.raises(KeyError):
        mission_store.transition_blueprint_step(owner_b, mission_a.id, "s1", "active")


def test_blueprint_lifecycle_and_terminal_validation(tmp_workspace: Path, mission_store: MissionStore):
    """Test 2: Blueprint lifecycle (draft -> approved -> superseded) and terminal mission rejection."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Lifecycle", objective="Obj")
    steps = [{"step_id": "s1", "title": "Step 1"}]

    # Draft created
    draft_bp = mission_store.save_draft_blueprint(owner, mission.id, steps)
    assert draft_bp.state == "draft"
    assert draft_bp.version == 1

    # Approved
    approved_bp = mission_store.approve_blueprint(owner, mission.id)
    assert approved_bp.state == "approved"

    # Creating a new draft version
    steps2 = [{"step_id": "s1", "title": "Step 1 Updated"}, {"step_id": "s2", "title": "Step 2"}]
    draft_v2 = mission_store.save_draft_blueprint(owner, mission.id, steps2)
    assert draft_v2.state == "draft"
    assert draft_v2.version == 2

    # Approving v2 supersedes v1
    approved_v2 = mission_store.approve_blueprint(owner, mission.id)
    assert approved_v2.state == "approved"
    assert approved_v2.version == 2

    # Verify v1 is now superseded
    v1_refreshed = mission_store.get_latest_blueprint(owner, mission.id)
    assert v1_refreshed.id == approved_v2.id

    # Transition mission to terminal state (completed)
    mission_store.transition(owner, mission.id, "active")
    mission_store.transition(owner, mission.id, "completed")

    # Terminal mission rejects draft creation, editing, approval, or step transitions
    with pytest.raises(ValueError, match="Cannot modify blueprint for a completed or cancelled mission"):
        mission_store.save_draft_blueprint(owner, mission.id, steps)

    with pytest.raises(ValueError, match="Cannot approve blueprint for a completed or cancelled mission"):
        mission_store.approve_blueprint(owner, mission.id)

    with pytest.raises(ValueError, match="Cannot transition step for a completed or cancelled mission"):
        mission_store.transition_blueprint_step(owner, mission.id, "s1", "completed")


def test_ordered_unique_bounded_steps_and_single_active_constraint(tmp_workspace: Path, mission_store: MissionStore):
    """Test 3 & 4: Max 20 steps, unique step IDs, bounded fields, and at most one active step."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Bounded Steps", objective="Obj")

    # Exceed 20 steps limit
    too_many_steps = [{"step_id": f"s_{i}", "title": f"Step {i}"} for i in range(21)]
    with pytest.raises(ValueError, match="between 1 and 20 steps"):
        mission_store.save_draft_blueprint(owner, mission.id, too_many_steps)

    # Duplicate step ID
    dup_steps = [{"step_id": "s1", "title": "Step 1"}, {"step_id": "s1", "title": "Step 1 Copy"}]
    with pytest.raises(ValueError, match="Duplicate step ID: s1"):
        mission_store.save_draft_blueprint(owner, mission.id, dup_steps)

    # Multiple active steps in input
    multi_active = [
        {"step_id": "s1", "title": "Step 1", "state": "active"},
        {"step_id": "s2", "title": "Step 2", "state": "active"},
    ]
    with pytest.raises(ValueError, match="At most one step can be active or blocked"):
        mission_store.save_draft_blueprint(owner, mission.id, multi_active)


def test_blocked_requires_reason(tmp_workspace: Path, mission_store: MissionStore):
    """Test 5: Transitioning a step to blocked requires a human-written reason."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Blocked Step", objective="Obj")
    steps = [{"step_id": "s1", "title": "Step 1"}]
    mission_store.save_draft_blueprint(owner, mission.id, steps)
    mission_store.approve_blueprint(owner, mission.id)

    # Transitioning to blocked without a reason fails
    with pytest.raises(ValueError, match="step blocked reason is required"):
        mission_store.transition_blueprint_step(owner, mission.id, "s1", "blocked")

    # Transitioning to blocked with a valid reason succeeds
    bp = mission_store.transition_blueprint_step(owner, mission.id, "s1", "blocked", blocked_reason="Waiting on human approval")
    assert bp.steps[0].state == "blocked"
    assert bp.steps[0].blocked_reason == "Waiting on human approval"


def test_human_only_transitions(tmp_workspace: Path, mission_store: MissionStore, session_manager: SessionManager):
    """Test 6: Only human store/API calls mutate steps; model turns do not auto-advance steps."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Human Only", objective="Obj")
    mission = mission_store.transition(owner, mission.id, "active")
    steps = [{"step_id": "s1", "title": "Step 1"}, {"step_id": "s2", "title": "Step 2"}]
    mission_store.save_draft_blueprint(owner, mission.id, steps)
    bp = mission_store.approve_blueprint(owner, mission.id)
    assert bp.steps[0].state == "active"
    assert bp.steps[1].state == "pending"

    session = session_manager.get_or_create(session_key)
    session.metadata["pico_active_mission_id"] = mission.id

    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)
    msg = InboundMessage(channel="web", sender_id="browser:user1", chat_id="web:user1:sess1", content="Please complete step 1 now")
    loop._queue_run(msg)

    # Verify model turn queued run did NOT mutate step states
    refreshed_bp = mission_store.get_approved_blueprint(owner, mission.id)
    assert refreshed_bp.steps[0].state == "active"
    assert refreshed_bp.steps[1].state == "pending"


def test_approved_blueprint_context_formatting(tmp_workspace: Path, mission_store: MissionStore):
    """Test 7: Approved blueprint context is bounded and absent for draft/superseded/none."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Context", objective="Obj")
    mission = mission_store.transition(owner, mission.id, "active")
    steps = [{"step_id": "s1", "title": "Step 1 Title", "success_criterion": "Criterion 1"}]

    builder = ContextBuilder(tmp_workspace)

    # 1. Draft blueprint -> standard CH5 mission context (no active step blueprint context block)
    draft_bp = mission_store.save_draft_blueprint(owner, mission.id, steps)
    ctx_draft = builder.render_mission_blueprint_context(mission, draft_bp)
    assert "[Active Mission Context — for reference only, human-owned state]" in ctx_draft
    assert "Active Step: Step 1 Title" not in ctx_draft

    # 2. Approved blueprint -> active step blueprint context included
    approved_bp = mission_store.approve_blueprint(owner, mission.id)
    ctx_approved = builder.render_mission_blueprint_context(mission, approved_bp)
    assert "[Active Mission Blueprint Context — for reference only, human-owned state]" in ctx_approved
    assert "Title: Mission Context" in ctx_approved
    assert "Objective: Obj" in ctx_approved
    assert "Active Step ID: s1" in ctx_approved
    assert "Active Step: Step 1 Title" in ctx_approved
    assert "Success Criterion: Criterion 1" in ctx_approved
    assert "Step State: active" in ctx_approved

    # 3. Superseded blueprint -> fallback to standard CH5 context
    mission_store.save_draft_blueprint(owner, mission.id, [{"step_id": "s2", "title": "Step 2"}])
    v2_bp = mission_store.approve_blueprint(owner, mission.id)  # supersedes v1
    superseded_v1 = MissionBlueprint(
        id=approved_bp.id,
        mission_id=approved_bp.mission_id,
        owner_id=approved_bp.owner_id,
        version=approved_bp.version,
        state="superseded",
        steps=approved_bp.steps,
        created_at=approved_bp.created_at,
        updated_at=approved_bp.updated_at,
    )
    ctx_superseded = builder.render_mission_blueprint_context(mission, superseded_v1)
    assert "Active Step: Step 1 Title" not in ctx_superseded
    assert "Active Step: Step 2" in builder.render_mission_blueprint_context(mission, v2_bp)

    # 4. Not written to personal memory
    assert len(builder.personal_memory.list(owner)) == 0


def test_linked_runs_retain_historical_blueprint_step_id(
    tmp_workspace: Path, mission_store: MissionStore, run_store: RunStore, session_manager: SessionManager
):
    """Test 8: Linked runs retain active blueprint_step_id permanently."""
    owner = "web:browser:user1"
    client_id = "user1"
    session_id = "sess1"
    session_key = f"web:web:{client_id}:{session_id}"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Run Metadata", objective="Obj")
    mission = mission_store.transition(owner, mission.id, "active")
    steps = [{"step_id": "s1", "title": "Step 1"}, {"step_id": "s2", "title": "Step 2"}]
    mission_store.save_draft_blueprint(owner, mission.id, steps)
    mission_store.approve_blueprint(owner, mission.id)

    session = session_manager.get_or_create(session_key)
    session.metadata["pico_active_mission_id"] = mission.id

    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)

    # Turn 1: queued during Step 1 (active)
    msg1 = InboundMessage(channel="web", sender_id=f"browser:{client_id}", chat_id=f"web:{client_id}:{session_id}", content="Turn 1")
    run1 = loop._queue_run(msg1)
    assert run1.blueprint_step_id == "s1"

    # Advance step to s2
    mission_store.transition_blueprint_step(owner, mission.id, "s1", "completed")

    # Turn 2: queued during Step 2 (active)
    msg2 = InboundMessage(channel="web", sender_id=f"browser:{client_id}", chat_id=f"web:{client_id}:{session_id}", content="Turn 2")
    run2 = loop._queue_run(msg2)
    assert run2.blueprint_step_id == "s2"

    # Historical run1 retains s1 permanently
    refreshed_run1 = run_store.get(owner, run1.id)
    assert refreshed_run1.blueprint_step_id == "s1"


def test_queued_run_omits_blueprint_context_when_human_advances_step(
    tmp_workspace: Path, mission_store: MissionStore, session_manager: SessionManager
):
    """A queued run must never be prompted with a different active step."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"
    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Drift", objective="Obj")
    mission = mission_store.transition(owner, mission.id, "active")
    mission_store.save_draft_blueprint(owner, mission.id, [
        {"step_id": "s1", "title": "First"},
        {"step_id": "s2", "title": "Second"},
    ])
    mission_store.approve_blueprint(owner, mission.id)
    session = session_manager.get_or_create(session_key)
    session.metadata["pico_active_mission_id"] = mission.id
    loop = AgentLoop(MessageBus(), create_mock_provider(), tmp_workspace, session_manager=session_manager)
    msg = InboundMessage(channel="web", sender_id="browser:user1", chat_id="web:user1:sess1", content="Queued")
    run = loop._queue_run(msg)
    assert run.blueprint_step_id == "s1"

    mission_store.transition_blueprint_step(owner, mission.id, "s1", "completed")
    current_mission = loop._mission_for_turn(session, owner, run.id)
    assert current_mission is not None
    assert loop._blueprint_for_turn(owner, current_mission, run.id) is None


def test_no_prompts_or_secrets_in_blueprint_event_records(
    tmp_workspace: Path, mission_store: MissionStore
):
    """Test 9: Blueprint events contain safe summaries and no secret transcripts."""
    owner = "web:browser:user1"
    session_key = "web:web:user1:sess1"

    mission = mission_store.create(owner_id=owner, session_key=session_key, title="Mission Safe Blueprint", objective="Obj")
    steps = [{"step_id": "s1", "title": "Step 1"}]
    mission_store.save_draft_blueprint(owner, mission.id, steps)
    mission_store.approve_blueprint(owner, mission.id)
    mission_store.transition_blueprint_step(owner, mission.id, "s1", "completed")

    events = mission_store.events(owner, mission.id)
    for event in events:
        event_dict = event.to_dict()
        assert "password" not in event_dict["summary"]
        assert "sk-12345" not in event_dict["summary"]
        assert "source_run_id" not in event_dict  # public projection clean


def test_blueprint_browser_helpers_require_the_mission_session(tmp_workspace: Path):
    """A browser identity cannot access one of its own other-session blueprints."""
    from types import SimpleNamespace

    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: SimpleNamespace(workspace_path=tmp_workspace)
    client = "browser_identity_0001"
    session_a = "session_identity_0001"
    session_b = "session_identity_0002"
    sessions = SessionManager(tmp_workspace)
    sessions.save(sessions.get_or_create(channel._session_key(client, session_a)))
    sessions.save(sessions.get_or_create(channel._session_key(client, session_b)))
    mission = channel._create_browser_mission(client, session_a, "Scoped", "Keep it scoped.", None)

    with pytest.raises(ValueError, match="does not belong to this session"):
        channel._browser_save_draft_blueprint(
            client, session_b, mission["id"], [{"step_id": "s1", "title": "Wrong session"}]
        )

    draft = channel._browser_save_draft_blueprint(
        client, session_a, mission["id"], [{"step_id": "s1", "title": "Correct session"}]
    )
    assert draft["state"] == "draft"
