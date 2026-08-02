from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from picobot.runs import RunStore

OWNER_A = "web:browser:owner-a"
OWNER_B = "web:browser:owner-b"
SESSION_A = "web:web:owner-a:session-a"
SESSION_B = "web:web:owner-b:session-b"


def _create(store: RunStore, **overrides):
    values = {
        "owner_id": OWNER_A,
        "session_key": SESSION_A,
        "capability_profile": "personal-work",
        "policy_revision": "personal-work@abc123",
    }
    values.update(overrides)
    return store.create(**values)


def test_runs_persist_across_reloads_and_return_safe_dicts(tmp_path: Path):
    workspace = tmp_path / "workspace"
    created = _create(
        RunStore(workspace),
        provider="litellm",
        model="gpt-5",
        mission_id="mission-1",
    )

    reloaded = RunStore(workspace)
    run = reloaded.get(OWNER_A, created.id)

    assert reloaded.path == workspace / "runs" / "pico-runs.db"
    assert run.id == created.id
    assert run.state == "queued"
    assert run.capability_profile == "personal-work"
    assert run.policy_revision == "personal-work@abc123"
    assert run.provider == "litellm"
    assert run.model == "gpt-5"
    assert run.mission_id == "mission-1"
    assert run.usage == {}
    assert run.to_dict() == {
        "id": run.id,
        "owner_id": OWNER_A,
        "session_key": SESSION_A,
        "mission_id": "mission-1",
        "provider": "litellm",
        "model": "gpt-5",
        "capability_profile": "personal-work",
        "policy_revision": "personal-work@abc123",
        "state": "queued",
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": None,
        "ended_at": None,
        "elapsed_ms": None,
        "usage": {},
        "error_summary": None,
        "result_ref": None,
        "blueprint_step_id": None,
        "task_id": None,
        "tool_activity_count": 0,
        "approvals_count": 0,
        "artifact_count": 0,
    }
    assert not any(
        key in run.to_dict()
        for key in ("reasoning", "thinking_blocks", "arguments", "secret", "token")
    )


def test_runs_are_owner_and_session_scoped(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run_a = _create(store)
    run_b = _create(store, owner_id=OWNER_B, session_key=SESSION_B)

    assert [run.id for run in store.list(OWNER_A)] == [run_a.id]
    assert [run.id for run in store.list(OWNER_B)] == [run_b.id]
    assert [run.id for run in store.list(OWNER_A, session_key=SESSION_A)] == [run_a.id]
    assert store.list(OWNER_A, session_key="web:web:owner-a:other") == []
    with pytest.raises(KeyError, match="not found"):
        store.get(OWNER_B, run_a.id)
    with pytest.raises(KeyError, match="not found"):
        store.mark_running(OWNER_B, run_a.id)
    with pytest.raises(KeyError, match="not found"):
        store.cancel(OWNER_B, run_a.id)


def test_run_state_machine_transitions_and_terminal_behavior(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run = _create(store)

    with pytest.raises(ValueError, match="cannot transition"):
        store.complete(OWNER_A, run.id)

    running = store.mark_running(OWNER_A, run.id)
    assert running.state == "running"
    assert running.started_at is not None
    assert running.ended_at is None

    completed = store.complete(
        OWNER_A,
        run.id,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        result_ref="message:m-1",
        tool_activity_count=2,
        provider="litellm",
        model="gpt-5",
    )
    assert completed.state == "completed"
    assert completed.ended_at is not None
    assert completed.elapsed_ms is not None
    assert completed.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert completed.result_ref == "message:m-1"
    assert completed.tool_activity_count == 2
    assert completed.provider == "litellm"

    with pytest.raises(ValueError, match="cannot transition"):
        store.cancel(OWNER_A, run.id)


def test_run_failure_requires_safe_summary_and_strips_no_secrets(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run = _create(store)
    run = store.mark_running(OWNER_A, run.id)

    with pytest.raises(ValueError, match="error summary is required"):
        store.fail(OWNER_A, run.id, error_summary=None)

    failed = store.fail(
        OWNER_A,
        run.id,
        error_summary="  api_key=sk-secret line dropped;   quota exhausted  ",
    )
    assert failed.state == "failed"
    assert failed.ended_at is not None
    assert failed.error_summary is not None
    assert "sk-secret" not in (failed.error_summary or "")


def test_run_approval_wait_and_resume(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run = _create(store)
    run = store.mark_running(OWNER_A, run.id)
    waiting = store.wait_for_approval(OWNER_A, run.id)
    assert waiting.state == "waiting_for_approval"

    resumed = store.resume(OWNER_A, run.id)
    assert resumed.state == "running"
    assert resumed.started_at is not None

    done = store.complete(OWNER_A, run.id)
    assert done.state == "completed"


def test_active_run_isolation_and_cancel(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    active = _create(store)
    queued = _create(store)
    other = _create(store, owner_id=OWNER_B, session_key=SESSION_B)
    store.mark_running(OWNER_A, active.id)
    store.mark_running(OWNER_B, other.id)

    cancelled = store.cancel_active(OWNER_A, SESSION_A)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert store.get_active(OWNER_A, SESSION_A).id == queued.id
    assert store.get(OWNER_A, queued.id).state == "queued"
    assert store.get(OWNER_B, other.id).state == "running"
    assert store.cancel_active(OWNER_A, SESSION_A).id == queued.id


def test_usage_is_validated_and_bounded(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run = _create(store)
    run = store.mark_running(OWNER_A, run.id)
    with pytest.raises(ValueError, match="Unsupported run usage key"):
        store.complete(OWNER_A, run.id, usage={"prompt_tokens": 1, "unknown_metric": 2})
    with pytest.raises(ValueError, match="non-negative integer"):
        store.complete(OWNER_A, run.id, usage={"prompt_tokens": -1})
    with pytest.raises(ValueError, match="non-negative integer"):
        store.complete(OWNER_A, run.id, tool_activity_count=-2)


def test_turn_receipt_reports_observable_work_only(tmp_path: Path):
    store = RunStore(tmp_path / "workspace")
    run = _create(store)
    run = store.mark_running(OWNER_A, run.id)
    run = store.complete(
        OWNER_A,
        run.id,
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        tool_activity_count=1,
        result_ref="message:m-9",
    )
    receipt = run.turn_receipt()

    assert receipt["state"] == "completed"
    assert receipt["elapsed_ms"] is not None
    assert receipt["tool_activity_count"] == 1
    assert receipt["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert receipt["result_ref"] == "message:m-9"
    assert receipt["run_id"] == run.id
    assert set(receipt) <= {
        "run_id",
        "state",
        "created_at",
        "started_at",
        "ended_at",
        "elapsed_ms",
        "provider",
        "model",
        "mission_id",
        "capability_profile",
        "policy_revision",
        "usage",
        "error_summary",
        "result_ref",
        "blueprint_step_id",
        "task_id",
        "tool_activity_count",
        "approvals_count",
        "artifact_count",
    }


class _RecordingProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tool-1", name="list_skills", arguments={})],
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                provider_name="test-provider",
                model_name="test-model",
            )
        return LLMResponse(
            content="Done.",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            provider_name="test-provider",
            model_name="test-model",
        )


class _ErrorProvider(LLMProvider):
    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        return LLMResponse(
            content="Error calling LLM: quota exhausted with sk-secret-inline",
            finish_reason="error",
        )


class _SlowProvider(LLMProvider):
    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        await asyncio.sleep(30)
        return LLMResponse(content="never")


@pytest.mark.asyncio
async def test_agent_turn_records_completed_run_with_trusted_usage(tmp_path: Path):
    provider = _RecordingProvider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    response = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="List skills"
        )
    )

    assert response is not None and response.content == "Done."
    run = agent.runs.list("web:browser:owner-a", session_key="web:chat-a")[0]
    assert run.state == "completed"
    assert run.usage == {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}
    assert run.tool_activity_count == 1
    assert run.capability_profile == "personal-work"
    assert run.policy_revision.startswith("personal-work@")
    assert response.metadata["run_id"] == run.id
    assert response.metadata["run_receipt"]["state"] == "completed"
    assert response.metadata["run_receipt"]["usage"] == run.usage


@pytest.mark.asyncio
async def test_status_and_recap_report_durable_session_state_without_private_content(tmp_path: Path):
    agent = AgentLoop(bus=MessageBus(), provider=_RecordingProvider(), workspace=tmp_path)
    session = agent.sessions.get_or_create("web:chat-a")
    session.add_message("user", "Private prompt that must not appear in status")
    session.add_message("assistant", "Private answer that must not appear in status")
    agent.sessions.save(session)
    queued = agent.runs.create(
        owner_id="web:browser:owner-a",
        session_key="web:chat-a",
        capability_profile="personal-work",
        policy_revision="personal-work@test",
    )

    status = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="/status"
        )
    )
    recap = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="/recap"
        )
    )

    assert status is not None and status.content.startswith("picobot status\n")
    assert recap is not None and recap.content.startswith("picobot session recap\n")
    for content in (status.content, recap.content):
        assert "Profile: Personal work (personal-work)" in content
        assert f"Latest run: queued ({queued.id[:8]})" in content
        assert "Approvals waiting: 0" in content
        assert "Private prompt" not in content
        assert "Private answer" not in content


@pytest.mark.asyncio
async def test_prequeued_run_is_reused_by_its_turn(tmp_path: Path):
    agent = AgentLoop(bus=MessageBus(), provider=_RecordingProvider(), workspace=tmp_path)
    message = InboundMessage(
        channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="List skills"
    )
    queued = agent._queue_run(message)

    assert queued.state == "queued"
    response = await agent._process_message(message, queued_run_id=queued.id)

    runs = agent.runs.list("web:browser:owner-a", session_key="web:chat-a")
    assert len(runs) == 1
    assert runs[0].id == queued.id
    assert runs[0].state == "completed"
    assert response is not None and response.metadata["run_id"] == queued.id


@pytest.mark.asyncio
async def test_agent_turn_records_failed_run_with_safe_summary(tmp_path: Path):
    agent = AgentLoop(bus=MessageBus(), provider=_ErrorProvider(), workspace=tmp_path)

    response = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="Break it"
        )
    )

    run = agent.runs.list("web:browser:owner-a", session_key="web:chat-a")[0]
    assert run.state == "failed"
    assert run.error_summary is not None
    assert "sk-secret-inline" not in run.error_summary
    assert run.ended_at is not None
    assert response is not None
    assert "sk-secret-inline" not in response.content
    assert response.metadata["run_receipt"]["state"] == "failed"


@pytest.mark.asyncio
async def test_agent_turn_cancellation_persists_cancelled_run(tmp_path: Path):
    agent = AgentLoop(bus=MessageBus(), provider=_SlowProvider(), workspace=tmp_path)
    session = agent.sessions.get_or_create("web:web:owner-a:session-a")

    task = asyncio.create_task(
        agent._run_turn(
            owner_id=OWNER_A,
            session=session,
            profile=agent._session_profile(session),
            allowed_tools=set(),
            messages=[{"role": "user", "content": "Run slowly"}],
            activity_context={
                "owner_id": OWNER_A,
                "session_key": session.key,
                "profile_id": "personal-work",
            },
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    runs = agent.runs.list(OWNER_A, session_key=session.key)
    assert len(runs) == 1
    assert runs[0].state == "cancelled"
    assert agent.runs.list_active(OWNER_A, session.key) == []
    assert runs[0].ended_at is not None


@pytest.mark.asyncio
async def test_agent_runs_are_session_isolated(tmp_path: Path):
    agent = AgentLoop(bus=MessageBus(), provider=_RecordingProvider(), workspace=tmp_path)

    await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="One"
        )
    )
    await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-b", chat_id="chat-b", content="Two"
        )
    )

    assert len(agent.runs.list("web:browser:owner-a", session_key="web:chat-a")) == 1
    assert len(agent.runs.list("web:browser:owner-b", session_key="web:chat-b")) == 1
    assert [run.session_key for run in agent.runs.list("web:browser:owner-a")] == ["web:chat-a"]
    assert [run.session_key for run in agent.runs.list("web:browser:owner-b")] == ["web:chat-b"]


def test_browser_runs_endpoint_exposes_only_owner_receipts_and_active_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    client_a = "browser_identity_0001"
    client_b = "browser_identity_0002"
    session_id = "session_identity_0001"

    store = RunStore(workspace)
    active = store.create(
        owner_id=channel._memory_owner(client_a),
        session_key=channel._session_key(client_a, session_id),
        capability_profile="personal-work",
        policy_revision="personal-work@abc",
    )
    store.mark_running(channel._memory_owner(client_a), active.id)
    other = store.create(
        owner_id=channel._memory_owner(client_b),
        session_key=channel._session_key(client_b, session_id),
        capability_profile="personal-work",
    )
    store.mark_running(channel._memory_owner(client_b), other.id)

    own = channel._browser_runs(client_a, session_id)
    assert [run["run_id"] for run in own["runs"]] == [active.id]
    assert own["active"] is not None
    assert own["active"]["state"] == "running"
    assert own["active"]["run_id"] == active.id
    assert all(
        set(run) <= {
            "run_id",
            "state",
            "created_at",
            "started_at",
            "ended_at",
            "elapsed_ms",
                "provider",
                "model",
                "mission_id",
                "capability_profile",
            "policy_revision",
            "usage",
            "error_summary",
            "result_ref",
            "blueprint_step_id",
            "task_id",
            "tool_activity_count",
            "approvals_count",
            "artifact_count",
        }
        for run in own["runs"]
    )
    assert "transcript" not in str(own).lower()
    assert "reasoning" not in str(own).lower()
    assert channel._browser_runs(client_b, session_id)["runs"][0]["run_id"] == other.id


def test_stop_control_cancels_only_the_sessions_active_run(tmp_path: Path):
    async def scenario():
        agent = AgentLoop(bus=MessageBus(), provider=_SlowProvider(), workspace=tmp_path)
        owner = "web:browser:owner-a"
        session_key = "web:web:owner-a:session-a"
        run = agent.runs.create(
            owner_id=owner, session_key=session_key, capability_profile="personal-work"
        )
        agent.runs.mark_running(owner, run.id)
        queued = agent.runs.create(
            owner_id=owner, session_key=session_key, capability_profile="personal-work"
        )
        other = agent.runs.create(
            owner_id="web:browser:owner-b",
            session_key="web:web:owner-b:session-b",
            capability_profile="personal-work",
        )
        agent.runs.mark_running("web:browser:owner-b", other.id)

        msg = InboundMessage(
            channel="web",
            sender_id="browser:owner-a",
            chat_id="web:owner-a:session-a",
            content="/stop",
        )
        await agent._handle_stop(msg)

        assert agent.runs.get(owner, run.id).state == "cancelled"
        assert agent.runs.get(owner, queued.id).state == "queued"
        assert agent.runs.get("web:browser:owner-b", other.id).state == "running"
        outbound = await asyncio.wait_for(agent.bus.consume_outbound(), timeout=1)
        assert "cancelled" in outbound.content
        assert run.id[:8] in outbound.content

    asyncio.run(scenario())
