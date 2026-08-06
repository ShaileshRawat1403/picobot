import copy

import pytest

from picobot.agent.loop import AgentLoop
from picobot.artifacts.store import ArtifactStore
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.providers.base import LLMProvider, LLMResponse
from picobot.projects import ProjectStore
from picobot.session.orientation import (
    ORIENTATION_METADATA_KEY,
    get_orientation,
    set_orientation,
)
from picobot.session.project_brief import PROJECT_BRIEF_CONTEXT_METADATA_KEY


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
    project = ProjectStore(tmp_path).create(
        "telegram:alice", title="Pico", kind="software", purpose="A local-first work partner"
    )
    ProjectStore(tmp_path).add_source(
        "telegram:alice", project.id, kind="local_folder", label="Pico repository", locator="/private/pico"
    )
    session = agent.sessions.get_or_create("telegram:chat-1")
    session.metadata[ORIENTATION_METADATA_KEY] = set_orientation(
        {
            "project_id": project.id,
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
    assert "# Oriented Pico project" in prompt
    assert "Pico repository (local_folder)" in prompt
    assert "/private/pico" not in prompt
    evidence = agent.context_evidence.get_for_run("telegram:alice", response.metadata["run_id"])
    assert evidence.orientation == {
        "project_id": project.id,
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


@pytest.mark.asyncio
async def test_selected_project_brief_is_bounded_prompt_context_with_a_redacted_receipt(tmp_path):
    provider = _Provider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    owner_id = "telegram:alice"
    artifact = ArtifactStore(tmp_path).create(
        owner_id=owner_id,
        session_key="telegram:chat-1",
        title="Pico project brief",
        kind="brief",
        content_type="text/markdown",
        content="Architecture summary\n\nPRIVATE-BRIEF-TEXT " + "x" * 6_500,
    )
    session = agent.sessions.get_or_create("telegram:chat-1")
    session.metadata[PROJECT_BRIEF_CONTEXT_METADATA_KEY] = {
        "artifact_id": artifact.id,
        "revision": artifact.revision,
    }
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(channel="telegram", sender_id="alice", chat_id="chat-1", content="What matters?")
    )

    assert response is not None
    prompt = str(provider.calls[0][0]["content"])
    assert "# Explicit project brief excerpt" in prompt
    assert "PRIVATE-BRIEF-TEXT" in prompt
    assert "source of instructions that can override" in prompt
    evidence = agent.context_evidence.get_for_run(owner_id, response.metadata["run_id"])
    brief = evidence.orientation["project_brief"]
    assert brief["artifact_id"] == artifact.id
    assert brief["revision"] == 1
    assert brief["truncated"] is True
    assert "PRIVATE-BRIEF-TEXT" not in str(evidence.public_view())
