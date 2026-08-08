#!/usr/bin/env python3
"""Tests for agent-loop streaming integration (deltas, tool hints, abort)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus import MessageBus
from picobot.providers.base import LLMProvider, LLMResponse, StreamChunk, ToolCallRequest


class _StreamingStub(LLMProvider):
    """Provider whose stream_chat yields rounds of chunks, one per loop pass."""

    def __init__(self, stream_rounds: list[list[StreamChunk]], chat_responses: list[LLMResponse] | None = None):
        super().__init__()
        self.stream_rounds = [list(round_) for round_ in stream_rounds]
        self.chat_responses = list(chat_responses or [])
        self.stream_calls = 0
        self.chat_calls = 0

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        index = self.chat_calls
        self.chat_calls += 1
        if index < len(self.chat_responses):
            return self.chat_responses[index]
        return LLMResponse(content="Non-stream fallback", finish_reason="stop")

    async def stream_chat(self, messages, tools=None, model=None, max_tokens=4096,
                          temperature=0.7, reasoning_effort=None, tool_choice=None):
        index = self.stream_calls
        self.stream_calls += 1
        for chunk in self.stream_rounds[min(index, len(self.stream_rounds) - 1)]:
            yield chunk


class _DeltaCollector:
    def __init__(self):
        self.deltas: list[str] = []

    async def __call__(self, content: str) -> None:
        self.deltas.append(content)


def _make_agent(provider, tmp_path: Path) -> AgentLoop:
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path / "workspace")


@pytest.mark.asyncio
async def test_run_agent_loop_streams_deltas_and_builds_response(tmp_path):
    provider = _StreamingStub(
        [
            [
                StreamChunk(content_delta="Hello "),
                StreamChunk(content_delta="world"),
                StreamChunk(
                    content_delta="",
                    finish_reason="stop",
                    usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                    provider_name="test-provider",
                    model_name="test-model",
                ),
            ]
        ]
    )
    agent = _make_agent(provider, tmp_path)

    collector = _DeltaCollector()
    final_content, tools_used, msgs, meta = await agent._run_agent_loop(
        [{"role": "user", "content": "hi"}],
        on_delta=collector,
    )

    assert collector.deltas == ["Hello ", "world"]
    assert final_content == "Hello world"
    assert provider.chat_calls == 0
    assert meta["_usage"] == {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
    assert meta["_failed"] is False
    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_streamed_tool_calls_emit_hint_but_not_duplicate_thought(tmp_path):
    tool = ToolCallRequest(id="tool-1", name="web_search", arguments={"query": "streaming"})
    provider = _StreamingStub(
        [
            [
                StreamChunk(content_delta="Let me look this up"),
                StreamChunk(finish_reason="tool_calls", tool_calls=[tool]),
            ],
            [
                StreamChunk(content_delta="Found the answer."),
                StreamChunk(content_delta="", finish_reason="stop"),
            ],
        ]
    )
    agent = _make_agent(provider, tmp_path)

    collector = _DeltaCollector()
    progress: list[tuple[str, bool]] = []

    async def on_progress(content, *, tool_hint=False):
        progress.append((content, tool_hint))

    final_content, tools_used, msgs, meta = await agent._run_agent_loop(
        [{"role": "user", "content": "search for X"}],
        on_delta=collector,
        on_progress=on_progress,
        allowed_tools=set(),
    )

    # The preamble flowed as deltas, so no duplicate thought bubble is pushed.
    assert collector.deltas == ["Let me look this up", "Found the answer."]
    assert len(progress) == 1
    assert progress[0] == ('web_search("streaming")', True)
    assert tools_used == ["web_search"]
    assert final_content == "Found the answer."


@pytest.mark.asyncio
async def test_non_streaming_path_keeps_thought_and_hint(tmp_path):
    tool = ToolCallRequest(id="tool-1", name="web_search", arguments={"query": "x"})
    provider = _StreamingStub(
        [[]],
        chat_responses=[
            LLMResponse(
                content="Reasoning aloud",
                finish_reason="tool_calls",
                tool_calls=[tool],
                provider_name="test-provider",
                model_name="test-model",
            ),
            LLMResponse(content="Final non-stream answer", finish_reason="stop"),
        ],
    )
    agent = _make_agent(provider, tmp_path)

    progress: list[tuple[str, bool]] = []

    async def on_progress(content, *, tool_hint=False):
        progress.append((content, tool_hint))

    final_content, _tools, _msgs, _meta = await agent._run_agent_loop(
        [{"role": "user", "content": "search"}],
        on_progress=on_progress,
        allowed_tools=set(),
    )

    assert provider.chat_calls == 2
    assert final_content == "Final non-stream answer"
    assert progress[0] == ("Reasoning aloud", False)
    assert progress[1] == ('web_search("x")', True)


@pytest.mark.asyncio
async def test_stream_cancelled_propagates_to_run_agent_loop(tmp_path):
    class AbortProvider(_StreamingStub):
        async def stream_chat(self, messages, tools=None, model=None, max_tokens=4096,
                              temperature=0.7, reasoning_effort=None, tool_choice=None):
            yield StreamChunk(content_delta="Partial answer")
            raise asyncio.CancelledError()

    agent = _make_agent(AbortProvider([[]]), tmp_path)

    collector = _DeltaCollector()

    async def run():
        await agent._run_agent_loop(
            [{"role": "user", "content": "hi"}],
            on_delta=collector,
        )

    with pytest.raises(asyncio.CancelledError):
        await run()

    # What arrived before the abort was already forwarded.
    assert collector.deltas == ["Partial answer"]


@pytest.mark.asyncio
async def test_streamed_error_marks_run_failed(tmp_path):
    provider = _StreamingStub(
        [[StreamChunk(content_delta="Error calling LLM: overloaded", finish_reason="error")]]
    )
    agent = _make_agent(provider, tmp_path)

    final_content, _tools, _msgs, meta = await agent._run_agent_loop(
        [{"role": "user", "content": "hi"}],
        on_delta=_DeltaCollector(),
    )

    assert final_content == "Error calling LLM: overloaded"
    assert meta["_failed"] is True
