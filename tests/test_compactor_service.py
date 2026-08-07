"""CompactionService: window assembly, handoff reuse, cooldown, and safety.

These tests exercise the service directly with a fake ``ContextSummarizer``,
without provider or loop wiring, and assert the durable ``CompactionStore``
records behind every branch.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.context import CompactionRecord, CompactionService
from picobot.context.compactor import _HANDOFF_MARKER
from picobot.context.planner import (
    ContextPlan,
    estimate_messages_tokens,
    plan_context_window,
)
from picobot.providers.base import LLMProvider, LLMResponse

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


def _record(*, created_at: str = "2026-01-01T00:00:00+00:00", **overrides) -> CompactionRecord:
    values = {
        "id": "record-id",
        "owner_id": OWNER,
        "session_key": SESSION,
        "role": "context_compression",
        "outcome": "completed",
        "reason": None,
        "created_at": created_at,
        "history_message_count": 80,
        "source_range": "history[0:72]",
        "compact_start": 0,
        "compact_end": 71,
        "protected_tail_start": 72,
        "tail_start": 72,
        "compacted_message_count": 72,
        "estimated_tokens_before": 100_000,
        "estimated_tokens_after": 1_800,
        "saved_tokens": 100_000 - 1_800,
        "summary": "Reference handoff text.",
        "provider": "fake",
        "model": "fake-model",
        "error_summary": None,
    }
    values.update(overrides)
    return CompactionRecord(**values)


@pytest.mark.asyncio
async def test_none_action_returns_history_unchanged_and_writes_nothing(tmp_path: Path):
    history = _over_budget_history(turns=2, text_length=10)
    service = CompactionService(tmp_path, summarizer=_FakeSummarizer())
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=100_000, current_request_tokens=20
    )
    assert window.plan.action == "none"
    assert window.plan.reason == "below_budget"
    assert window.messages == history
    assert window.handoff is None
    assert window.records_created == ()
    assert service.store.latest(OWNER, SESSION) is None


@pytest.mark.asyncio
async def test_trim_bounds_window_when_protected_tail_cannot_fit_beside_handoff(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=4)
    window = await service.build_window(
        history,
        owner_id=OWNER,
        session_key=SESSION,
        budget_tokens=7_000,
        current_request_tokens=30,
        provider="fake",
        model="fake-model",
    )
    assert window.plan.action == "trim"
    assert window.plan.reason == "not_compactable"
    assert summarizer.calls == 0
    assert window.handoff is None
    assert window.records_created == ()
    assert window.messages == history[window.plan.tail_start :]
    assert estimate_messages_tokens(window.messages) + 30 <= 7_000


@pytest.mark.asyncio
async def test_compact_summarizes_exactly_history_prefix(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=4)
    window = await service.build_window(
        history,
        owner_id=OWNER,
        session_key=SESSION,
        budget_tokens=25_000,
        current_request_tokens=30,
        provider="fake",
        model="fake-model",
    )
    assert window.plan.action == "compact"
    assert window.plan.compact_start == 0
    assert window.plan.compact_end == window.plan.tail_start - 1
    assert window.plan.protected_start == window.plan.tail_start
    assert summarizer.calls == 1
    source = summarizer.sources[0]
    system_prompt = source[:1]
    assert system_prompt[0]["role"] == "system"
    assert source[1:] == history[: window.plan.tail_start]
    assert window.handoff is not None and window.handoff.outcome == "completed"
    assert window.messages[0]["role"] == "assistant"
    assert window.messages[0]["content"].startswith(_HANDOFF_MARKER)
    assert window.messages[1:] == history[window.plan.tail_start :]
    tail_tokens = estimate_messages_tokens(history[window.plan.tail_start :])
    assert tail_tokens + 30 <= 25_000


@pytest.mark.parametrize(
    ("compact_start", "compact_end", "protected_tail_start", "tail_start"),
    [
        (1, 39, 40, 40),  # shifted compact_start
        (0, 38, 40, 40),  # shifted compact_end
        (0, 39, 39, 40),  # shifted protected tail boundary
        (0, 39, 40, 39),  # shifted tail slice
    ],
)
def test_handoff_covers_plan_rejects_any_boundary_mismatch(tmp_path: Path, compact_start, compact_end, protected_tail_start, tail_start):
    record = _record(
        compact_start=compact_start,
        compact_end=compact_end,
        protected_tail_start=protected_tail_start,
        tail_start=tail_start,
    )
    plan = ContextPlan(
        action="compact",
        reason="over_budget",
        compact_start=0,
        compact_end=39,
        protected_start=40,
        tail_start=40,
    )
    assert not CompactionService._handoff_covers_plan(record, plan)


def test_handoff_covers_plan_accepts_exact_coverage(tmp_path: Path):
    record = _record()
    plan = ContextPlan(
        action="compact",
        reason="over_budget",
        compact_start=0,
        compact_end=71,
        protected_start=72,
        tail_start=72,
    )
    assert CompactionService._handoff_covers_plan(record, plan)


@pytest.mark.asyncio
async def test_stale_handoff_is_not_reused_when_the_compacted_prefix_moves(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=4)
    first = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert first.handoff is not None and summarizer.calls == 1

    grown = [*history, _message("user", "new turn " + "z" * 3000), _message("assistant", "reply " + "z" * 3000)]
    second = await service.build_window(
        grown, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert summarizer.calls == 2
    assert second.handoff is not None
    assert second.handoff.id != first.handoff.id
    assert second.plan.tail_start > first.plan.tail_start


@pytest.mark.asyncio
async def test_cooldown_after_failure_records_skipped_with_cooldown_reason(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer(error="provider exploded")
    service = CompactionService(tmp_path, summarizer=summarizer, cooldown_minutes=60)
    first = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert first.records_created[0].outcome == "failed"

    second = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert second.plan.action == "compact"
    assert summarizer.calls == 1, "within cooldown the summarizer must not run again"
    assert second.handoff is None
    assert len(second.records_created) == 1
    skipped = second.records_created[0]
    assert skipped.outcome == "skipped"
    assert "cooldown" in (skipped.reason or "").lower()
    assert second.messages == history[second.plan.tail_start :]


@pytest.mark.asyncio
async def test_no_summarizer_records_skipped(tmp_path: Path):
    history = _over_budget_history()
    service = CompactionService(tmp_path, summarizer=None, cooldown_minutes=0)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert window.plan.action == "compact"
    assert len(window.records_created) == 1
    assert window.records_created[0].outcome == "skipped"
    assert window.records_created[0].reason == "no summarizer configured"
    assert window.handoff is None
    assert window.messages == history[window.plan.tail_start :]


@pytest.mark.asyncio
async def test_summarizer_failure_fails_closed_with_error_record(tmp_path: Path):
    history = _over_budget_history()
    summarizer = _FakeSummarizer(error="The provider dropped the connection")
    service = CompactionService(tmp_path, summarizer=summarizer, cooldown_minutes=0)
    window = await service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert window.plan.action == "compact"
    assert len(window.records_created) == 1
    record = window.records_created[0]
    assert record.outcome == "failed"
    assert record.error_summary is not None
    assert "RuntimeError" in record.error_summary
    assert window.handoff is None
    assert window.messages == history[window.plan.tail_start :]
    assert history == _over_budget_history(), "the source transcript must never change"


def test_within_cooldown_handles_naive_and_aware_timestamps(tmp_path: Path):
    now = datetime.now(timezone.utc)
    recent_aware = now - timedelta(minutes=1)
    old_aware = now - timedelta(minutes=30)
    recent_naive = recent_aware.replace(tzinfo=None)
    old_naive = old_aware.replace(tzinfo=None)

    for created_at, expected in (
        (recent_aware.isoformat(), True),
        (old_aware.isoformat(), False),
        (recent_naive.isoformat(), True),
        (old_naive.isoformat(), False),
    ):
        record = _record(created_at=created_at)
        assert CompactionService._within_cooldown(record, cooldown_minutes=15) is expected

    assert CompactionService._within_cooldown(_record(created_at=recent_aware.isoformat()), cooldown_minutes=0) is False
    assert CompactionService._within_cooldown(_record(created_at="not-a-timestamp"), cooldown_minutes=15) is False


def test_handoff_message_uses_assistant_role_and_marker(tmp_path: Path):
    record = _record()
    message = CompactionService.handoff_message(record)
    assert message["role"] == "assistant"
    assert message["content"].startswith(_HANDOFF_MARKER)
    assert record.summary in message["content"]


@pytest.mark.asyncio
async def test_build_window_never_mutates_history(tmp_path: Path):
    history = _over_budget_history()
    frozen = copy.deepcopy(history)

    compact_service = CompactionService(tmp_path, summarizer=_FakeSummarizer(), cooldown_minutes=0)
    await compact_service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert history == frozen

    trim_service = CompactionService(tmp_path, summarizer=None, cooldown_minutes=0)
    await trim_service.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=7_000, current_request_tokens=30
    )
    assert history == frozen

    failing = CompactionService(tmp_path, summarizer=_FakeSummarizer(error="boom"), cooldown_minutes=0)
    await failing.build_window(
        history, owner_id=OWNER, session_key=SESSION, budget_tokens=25_000, current_request_tokens=30
    )
    assert history == frozen


def _large_history(turns: int = 30, text_length: int = 4000) -> list[dict]:
    history = []
    for i in range(turns):
        history.append(_message("user", f"user-{i} " + "x" * text_length))
        history.append(_message("assistant", f"assistant-{i} " + "y" * text_length))
    return history


def test_request_exceeding_budget_reports_distinct_reason_with_empty_tail():
    history = _large_history()
    one_message_tokens = estimate_messages_tokens(history[:2])
    plan = plan_context_window(
        history,
        budget_tokens=one_message_tokens,
        protected_tail_count=10,
        current_request_tokens=one_message_tokens,
        min_source_messages=4,
        handoff_estimate_tokens=0,
    )
    assert plan.action == "trim"
    assert plan.tail_count == 0
    assert plan.request_exceeds_budget is True
    assert plan.reason == "request_exceeds_budget"


def test_tiny_budget_empty_tail_stays_not_compactable():
    history = _large_history()
    plan = plan_context_window(
        history,
        budget_tokens=200,
        protected_tail_count=10,
        current_request_tokens=30,
        min_source_messages=4,
        handoff_estimate_tokens=0,
    )
    assert plan.action == "trim"
    assert plan.tail_count == 0
    assert plan.request_exceeds_budget is False
    assert plan.reason == "not_compactable"


@pytest.mark.asyncio
async def test_request_exceeding_budget_returns_empty_window_without_summarizing(tmp_path: Path):
    history = _large_history()
    one_message_tokens = estimate_messages_tokens(history[:2])
    summarizer = _FakeSummarizer()
    service = CompactionService(tmp_path, summarizer=summarizer, protected_tail_count=10, cooldown_minutes=0)
    window = await service.build_window(
        history,
        owner_id=OWNER,
        session_key=SESSION,
        budget_tokens=one_message_tokens,
        current_request_tokens=one_message_tokens,
        provider="fake",
        model="fake-model",
    )
    assert window.plan.action == "trim"
    assert window.plan.request_exceeds_budget is True
    assert window.plan.reason == "request_exceeds_budget"
    assert window.messages == []
    assert summarizer.calls == 0
    assert window.handoff is None


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
async def test_empty_window_surfaces_context_warning_on_that_turn(tmp_path: Path):
    workspace = tmp_path / "workspace"
    agent = AgentLoop(
        bus=MessageBus(),
        provider=_RecordingProvider(),
        workspace=workspace,
        context_window_tokens=1_200,
        compaction=CompactionService(workspace, summarizer=_FakeSummarizer(), protected_tail_count=10),
    )
    session = agent.sessions.get_or_create("web:chat-a")
    for i in range(2):
        session.add_message("user", f"user-{i} " + "x" * 4000)
        session.add_message("assistant", f"assistant-{i} " + "y" * 4000)
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="single message " + "z" * 4000
        )
    )
    assert response is not None
    warning = response.metadata.get("context_warning")
    assert isinstance(warning, str) and warning
    assert "alone exceeds the context window" in warning


def test_effective_context_budget_clamps_to_provider_window():
    assert AgentLoop._effective_context_budget(65_536, None) == (65_536, False)
    provider = _RecordingProvider()
    provider.context_window = 4_000
    assert AgentLoop._effective_context_budget(65_536, provider) == (4_000, True)
    provider.context_window = 100_000
    assert AgentLoop._effective_context_budget(65_536, provider) == (65_536, False)
    provider.context_window = 0
    assert AgentLoop._effective_context_budget(65_536, provider) == (65_536, False)


@pytest.mark.asyncio
async def test_loop_clamps_context_budget_to_provider_window(tmp_path: Path):
    workspace = tmp_path / "workspace"
    provider = _RecordingProvider()
    provider.context_window = 4_000
    agent = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        context_window_tokens=25_000,
        compaction=CompactionService(workspace, summarizer=_FakeSummarizer(), protected_tail_count=4),
    )
    session = agent.sessions.get_or_create("web:chat-a")
    for i in range(20):
        session.add_message("user", f"user-{i} " + "x" * 3000)
        session.add_message("assistant", f"assistant-{i} " + "y" * 3000)
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="Condense this"
        )
    )
    assert response is not None
    plan = session.metadata["pico_pending_context_plan"]
    assert plan["context_window_budget"] == 4_000
    evidence = agent.context_evidence.latest("local:owner", "web:chat-a")
    assert evidence is not None and evidence.context_window_budget == 4_000
