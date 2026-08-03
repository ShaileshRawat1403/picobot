import copy

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.providers.base import LLMProvider, LLMResponse
from picobot.session.manager import Session
from picobot.session.orientation import (
    ORIENTATION_METADATA_KEY,
    get_orientation,
    set_orientation,
)


class _Provider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return LLMResponse(content="A bounded answer.")

    def get_default_model(self) -> str:
        return "test-model"


def test_session_orientation_is_bounded_and_has_a_redacted_receipt():
    orientation = set_orientation(
        {
            "project_id": "project-1",
            "objective": "Make project context explicit.",
            "role_lens_id": "systems_designer",
            "challenge_policy_id": "active",
            "temporary_constraints": ["No new connectors", "Keep the approval boundary"],
            "expected_result": "An inspectable orientation receipt",
        }
    )
    assert orientation.project_id == "project-1"
    assert orientation.evidence_view() == {
        "project_id": "project-1",
        "role_lens_id": "systems_designer",
        "challenge_policy_id": "active",
        "has_objective": True,
        "temporary_constraint_count": 2,
        "has_expected_result": True,
    }
    assert "Make project context" not in str(orientation.evidence_view())

    with pytest.raises(ValueError, match="role lens"):
        set_orientation({"role_lens_id": "operator"})
    assert get_orientation({ORIENTATION_METADATA_KEY: {"role_lens_id": "operator"}}).role_lens_id is None


@pytest.mark.asyncio
async def test_orientation_is_prompted_and_recorded_without_private_prose(tmp_path):
    provider = _Provider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    session = agent.sessions.get_or_create("telegram:chat-1")
    session.metadata[ORIENTATION_METADATA_KEY] = set_orientation(
        {
            "project_id": "project-1",
            "objective": "Turn the workbench into a durable solo-builder tool.",
            "role_lens_id": "founder",
            "challenge_policy_id": "active",
            "temporary_constraints": ["Do not add a connector catalogue"],
            "expected_result": "A narrow product contract",
        }
    ).to_metadata()
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(channel="telegram", sender_id="alice", chat_id="chat-1", content="What should we do?")
    )

    assert response is not None
    prompt = str(provider.calls[0][0]["content"])
    assert "# Session orientation" in prompt
    assert "Role lens: founder." in prompt
    assert "Concern, Evidence, Implication, Smaller path" in prompt
    assert "cannot grant tools" in prompt
    evidence = agent.context_evidence.get_for_run("telegram:alice", response.metadata["run_id"])
    assert evidence.orientation == {
        "project_id": "project-1",
        "role_lens_id": "founder",
        "challenge_policy_id": "active",
        "has_objective": True,
        "temporary_constraint_count": 1,
        "has_expected_result": True,
    }
    assert "solo-builder" not in str(evidence.public_view())


def test_queued_turn_uses_the_orientation_submission_snapshot(tmp_path):
    agent = AgentLoop(bus=MessageBus(), provider=_Provider(), workspace=tmp_path)
    session = agent.sessions.get_or_create("telegram:chat-1")
    session.metadata[ORIENTATION_METADATA_KEY] = set_orientation({"role_lens_id": "writer"}).to_metadata()
    run_id = "run-snapshot"
    agent._queued_orientation_snapshots[run_id] = agent._session_orientation(session).to_metadata()
    session.metadata[ORIENTATION_METADATA_KEY] = set_orientation({"role_lens_id": "builder"}).to_metadata()
    assert agent._orientation_for_submitted_turn(session, run_id).role_lens_id == "writer"
