"""Compaction evidence: durable records, fail-closed fallback, and safety."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.context import CompactionService, CompactionStore
from picobot.context.planner import estimate_messages_tokens
from picobot.memory.store import PersonalMemoryStore
from picobot.providers.base import LLMProvider, LLMResponse
from picobot.session.manager import SessionManager

OWNER = "local:owner"
SESSION = "web:web:session-a"


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def _over_budget_history(turns: int = 40, text_length: int = 3000) -> list[dict]:
    history = []
    for i in range(turns):
        history.append(_message("user", f"user-{i} " + "x" * text_length))
        history.append(_message("assistant", f"assistant-{i} " + "y" * text_length))
    return history


class _FakeSummarizer:
    def __init__(self, summary: str = "Material facts from the earlier conversation.", error: str | None = None):
        self.summary = summary
        self.error = error
        self.calls = 0
        self.sources: list[list[dict]] = []

    async def summarize(self, messages: list[dict], *, max_tokens: int) -> str:
        self.calls += 1
        self.sources.append(list(messages))
        if self.error:
            raise RuntimeError(self.error)
        return self.summary


class _RecordingProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools or []})
        return LLMResponse(content="Done.", provider_name="test-provider", model_name="test-model")


@pytest.mark.asyncio
async def test_below_budget_keeps_full_history_and_writes_no_records(tmp_path: Path):
    history = _over_budget_history(turns=2, text_length=10)
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer())
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=100_000, current_request_tokens=20
    )
    assert window.plan.action == "none"
    assert window.messages == history
    assert window.handoff is None
    assert window.records_created == ()
    assert service.store.latest(OWNER, SESSION) is None


@pytest.mark.asyncio
async def test_successful_compaction_persists_completed_record_and_uses_handoff(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(
        tmp_path, summarizer=summarizer, protected_tail_count=4, cooldown_minutes=30
    )
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000,
        current_request_tokens=30, provider="fake", model="fake-model",
    )
    assert window.plan.action == "compact"
    assert window.messages[0]["role"] == "assistant"
    assert window.messages[0]["content"].startswith("[Compaction Handoff")
    assert window.handoff is not None
    assert window.handoff.outcome == "completed"
    assert window.handoff.provider == "fake"
    assert window.handoff.model == "fake-model"
    assert window.handoff.compacted_message_count >= 1
    assert window.handoff.saved_tokens > 0
    assert len(window.records_created) == 1
    assert summarizer.calls == 1

    reloaded = CompactionStore(tmp_path).latest_completed(OWNER, SESSION)
    assert reloaded is not None and reloaded.id == window.handoff.id
    assert reloaded.summary == "Material facts from the earlier conversation."
    assert reloaded.source_range is not None
    assert history == _over_budget_history()  # source transcript unchanged


@pytest.mark.asyncio
async def test_protected_tail_is_never_passed_to_summarizer(tmp_path: Path):
    history = _over_budget_history(turns=20, text_length=3000)
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=4)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=11_000, current_request_tokens=20
    )
    assert window.plan.action == "compact"
    assert summarizer.sources, "summarizer must have been called"
    source = summarizer.sources[0]
    compacted = [m for m in source if m.get("role") in {"user", "assistant"}]
    last_users = [m for m in history if m["role"] == "user"][-4:]
    for message in last_users:
        assert message not in compacted
        assert message in history[window.plan.tail_start :]


@pytest.mark.asyncio
async def test_summarizer_failure_fails_closed_with_safe_record(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer(error="api_key=sk-super-secret exploded")
    service = CompactionService(tmp_path, summarizer=summarizer, cooldown_minutes=0)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert window.plan.action == "compact"
    assert len(window.records_created) == 1
    record = window.records_created[0]
    assert record.outcome == "failed"
    assert record.error_summary is not None
    assert "sk-super-secret" not in record.error_summary
    # Fail closed: the window is the safe bounded original tail, never a partial summary.
    assert not any(
        str(message.get("content", "")).startswith("[Compaction Handoff")
        for message in window.messages
    )
    assert window.messages == history[window.plan.tail_start :]
    assert window.handoff is None
    assert estimate_messages_tokens(window.messages) + 30 <= 25_000


@pytest.mark.asyncio
async def test_empty_handoff_is_recorded_as_failed(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer(summary="   "), cooldown_minutes=0)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000
    )
    assert window.records_created[0].outcome == "failed"
    assert "empty" in (window.records_created[0].error_summary or "").lower()
    assert window.handoff is None


@pytest.mark.asyncio
async def test_cooldown_reuses_existing_handoff_without_resummarizing(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, cooldown_minutes=60)

    first = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert first.plan.action == "compact"
    assert summarizer.calls == 1

    second = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert second.plan.action == "compact"
    assert summarizer.calls == 1, "within cooldown the handoff must be reused"
    assert second.records_created == ()
    assert second.handoff is not None and second.handoff.id == first.handoff.id
    assert second.messages[0]["content"] == first.messages[0]["content"]

    # Exact coverage is reusable even after the failure cooldown expires.
    # A shifted boundary is tested separately below and must recompress.
    service.cooldown_minutes = 0
    third = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert summarizer.calls == 1
    assert third.handoff is not None and third.handoff.id == first.handoff.id


@pytest.mark.asyncio
async def test_stale_handoff_is_not_reused_when_the_tail_boundary_moves(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=4, cooldown_minutes=60)
    first = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert first.handoff is not None

    grown = [*history, _message("user", "new middle turn " + "z" * 3000), _message("assistant", "new reply " + "z" * 3000)]
    second = await service.build_window(
        grown, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert second.handoff is not None
    assert second.handoff.id != first.handoff.id
    assert summarizer.calls == 2


@pytest.mark.asyncio
async def test_disabled_service_still_bounds_the_prompt_window(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, enabled=False)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=2_000, current_request_tokens=30
    )
    assert window.plan.action == "trim"
    assert estimate_messages_tokens(window.messages) + 30 <= 2_000


@pytest.mark.asyncio
async def test_records_are_owner_and_session_scoped(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer(), cooldown_minutes=0)
    await service.build_window(history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000)
    await service.build_window(
        history, owner_id="telegram:987654321", session_key="telegram:chat-b", budget_tokens=25_000
    )
    assert len(service.store.list(OWNER, SESSION)) == 1
    assert service.store.latest_completed(OWNER, "web:web:other-session") is None
    assert service.store.latest_completed("telegram:987654321", SESSION) is None


@pytest.mark.asyncio
async def test_no_summarizer_records_skipped_and_bounds_safely(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=None, cooldown_minutes=0)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert window.plan.action == "compact"
    assert window.records_created[0].outcome == "skipped"
    assert window.records_created[0].reason == "no summarizer configured"
    assert window.handoff is None
    assert window.messages == history[window.plan.tail_start :]
    assert estimate_messages_tokens(window.messages) + 30 <= 25_000


@pytest.mark.asyncio
async def test_disabled_service_keeps_a_bounded_history_without_records(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer(), enabled=False)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000
    )
    assert window.messages != history
    assert estimate_messages_tokens(window.messages) <= 25_000
    assert window.records_created == ()
    assert service.store.latest(OWNER, SESSION) is None


@pytest.mark.asyncio
async def test_compaction_never_writes_personal_memory(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer())
    await service.build_window(history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000)

    memory = PersonalMemoryStore(tmp_path)
    assert memory.list(OWNER) == []
    assert memory.list("telegram:987654321") == []


def test_record_public_view_is_safe_and_bounded(tmp_path: Path):
    store = CompactionStore(tmp_path)
    failed = store.create(
        owner_id=OWNER,
        session_key=SESSION,
        outcome="failed",
        history_message_count=80,
        compact_start=0,
        compact_end=71,
        protected_tail_start=72,
        tail_start=72,
        compacted_message_count=72,
        estimated_tokens_before=100_000,
        estimated_tokens_after=1_800,
        error_summary="api_key=sk-super-secret quota exceeded",
        reason="summarizer_failed",
    )
    completed = store.create(
        owner_id=OWNER,
        session_key=SESSION,
        outcome="completed",
        history_message_count=80,
        compact_start=0,
        compact_end=71,
        protected_tail_start=72,
        tail_start=72,
        compacted_message_count=72,
        estimated_tokens_before=100_000,
        estimated_tokens_after=1_800,
        summary="Reference handoff text that must never surface in the web UI.",
        provider="fake",
        model="fake-model",
    )

    view = completed.public_view()
    assert view["outcome"] == "completed"
    assert view["compacted_message_count"] == 72
    assert view["saved_tokens"] == 100_000 - 1_800
    assert view["provider"] == "fake"
    assert "summary" not in view
    dumped = json.dumps(view)
    assert "Reference handoff" not in dumped
    assert completed.summary is not None
    assert "sk-hidden" not in completed.summary

    failed_view = failed.public_view()
    assert "sk-super-secret" not in json.dumps(failed_view)
    assert "sk-" not in json.dumps(failed_view)


def test_web_context_response_shows_safe_compaction_block(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    web_owner = "local:owner"
    web_session = "web:web:session_identity_0001"
    manager = SessionManager(workspace)
    session = manager.get_or_create(web_session)
    session.add_message("user", "hello")
    session.metadata["pico_last_context"] = {
        "recorded_at": "now",
        "history_message_count": 2,
        "memory_ids": [],
    }
    manager.save(session)

    store = CompactionStore(workspace)
    store.create(
        owner_id=web_owner,
        session_key=web_session,
        outcome="completed",
        history_message_count=80,
        compact_start=0,
        compact_end=71,
        protected_tail_start=72,
        tail_start=72,
        compacted_message_count=72,
        estimated_tokens_before=100_000,
        estimated_tokens_after=1_800,
        summary="Secret-like handoff text api_key=sk-hidden should not surface.",
        provider="fake",
        model="fake-model",
    )

    channel = WebChannel(SimpleNamespace(), MessageBus())
    monkeypatch.setattr(
        WebChannel,
        "_runtime_config",
        staticmethod(lambda: SimpleNamespace(workspace_path=workspace)),
    )

    body = channel._browser_session_context("browser_identity_0001", "session_identity_0001")
    assert body["compaction"] is not None
    assert body["compaction"]["outcome"] == "completed"
    assert body["compaction"]["saved_tokens"] == 100_000 - 1_800
    assert body["compaction_timeline"][0]["record_id"] == body["compaction"]["record_id"]
    assert "summary" not in body["compaction"]
    assert "summary" not in body["compaction_timeline"][0]
    dumped = json.dumps(body)
    assert "Secret-like" not in dumped
    assert "sk-hidden" not in dumped
    assert "api_key" not in dumped


@pytest.mark.asyncio
async def test_agent_uses_its_active_provider_for_compaction_when_no_auxiliary_is_set(tmp_path: Path):
    workspace = tmp_path / "workspace"
    provider = _RecordingProvider()
    agent = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_window_tokens=25_000,
        compaction=CompactionService(workspace, protected_tail_count=4, cooldown_minutes=60),
    )
    session = agent.sessions.get_or_create("web:chat-a")
    for i in range(40):
        session.add_message("user", f"user-{i} " + "x" * 3000)
        session.add_message("assistant", f"assistant-{i} " + "y" * 3000)
    agent.sessions.save(session)

    await agent._process_message(
        InboundMessage(channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="Summarize")
    )
    completed = CompactionStore(workspace).latest_completed("local:owner", "web:chat-a")
    assert completed is not None
    assert completed.provider == "test-provider"
    assert completed.model == "test-model"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_agent_integrates_compaction_and_resumes_with_latest_handoff(tmp_path: Path):
    workspace = tmp_path / "workspace"
    summarizer = _FakeSummarizer()
    provider = _RecordingProvider()
    agent = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_window_tokens=25_000,
        compaction=CompactionService(
            workspace,
            summarizer=summarizer,
            protected_tail_count=4,
            cooldown_minutes=60,
        ),
    )

    session = agent.sessions.get_or_create("web:chat-a")
    for i in range(40):
        session.add_message("user", f"user-{i} " + "x" * 3000)
        session.add_message("assistant", f"assistant-{i} " + "y" * 3000)
    source_len = len(session.messages)
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="What did we discuss?"
        )
    )
    assert response is not None and response.content == "Done."

    assert summarizer.calls == 1
    first_model_call = provider.calls[0]["messages"]
    assert any(
        str(message.get("content", "")).startswith("[Compaction Handoff")
        for message in first_model_call
    )

    store = CompactionStore(workspace)
    completed = store.latest_completed("local:owner", "web:chat-a")
    assert completed is not None and completed.outcome == "completed"

    # The source transcript is untouched: the handoff is never persisted.
    assert len(session.messages) == source_len + 2
    assert not any(
        str(message.get("content", "")).startswith("[Compaction Handoff")
        for message in session.messages
    )
    assert PersonalMemoryStore(workspace).list("local:owner") == []

    # The new turn shifts the protected-tail boundary, so the old handoff no
    # longer covers every omitted source message and Pico recompacts safely.
    await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="And now?"
        )
    )
    assert summarizer.calls == 2
    second_model_call = provider.calls[1]["messages"]
    assert any(
        str(message.get("content", "")).startswith("[Compaction Handoff")
        for message in second_model_call
    )
    assert len(store.list("local:owner", "web:chat-a")) == 2
