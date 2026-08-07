import copy
import json

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.context.evidence import ContextEvidenceStore
from picobot.providers.base import LLMProvider, LLMResponse


class _Provider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return LLMResponse(content="A bounded answer.")

    def get_default_model(self) -> str:
        return "test-model"


def test_context_evidence_is_owner_scoped_and_redacted(tmp_path):
    store = ContextEvidenceStore(tmp_path)
    record = store.record(
        owner_id="web:owner-a",
        session_key="web:owner-a:session-a",
        run_id="run-a",
        history_message_count=4,
        memory_ids=["memory-a"],
        skill_names=["writing-style"],
        plan_action="compact",
        plan_reason="over_budget",
        estimated_tokens_before=1000,
        estimated_tokens_after=300,
        compaction_record_ids=["compaction-a"],
        stance_id="review",
        context_window_budget=8_192,
    )

    assert record.public_view()["skill_names"] == ["writing-style"]
    assert record.public_view()["stance_id"] == "review"
    assert record.public_view()["context_window_budget"] == 8_192
    assert "owner_id" not in json.dumps(record.public_view())
    assert store.latest("web:owner-a", "web:owner-a:session-a").run_id == "run-a"
    assert store.latest("web:owner-b", "web:owner-a:session-a") is None
    assert store.skill_use_count("web:owner-a", "writing-style") == 1
    assert store.skill_use_count("web:owner-b", "writing-style") == 0


@pytest.mark.asyncio
async def test_completed_turn_writes_run_linked_context_evidence(tmp_path):
    provider = _Provider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    memory = agent.context.personal_memory.remember("telegram:alice", "Prefer concise answers.")

    response = await agent._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="alice",
            chat_id="chat-1",
            content="Please use concise answers.",
        )
    )

    assert response is not None
    run_id = response.metadata["run_id"]
    evidence = agent.context_evidence.get_for_run("telegram:alice", run_id)
    assert evidence.session_key == "telegram:chat-1"
    assert evidence.memory_ids == (memory.id,)
    assert evidence.plan_action in {"none", "compact", "trim"}
    assert evidence.stance_id == "explore"
    assert agent.sessions.get_or_create("telegram:chat-1").metadata["pico_last_context"]["run_id"] == run_id
