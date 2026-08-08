#!/usr/bin/env python3
"""Tests for the streaming provider layer (stream_chat deltas + abort)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from picobot.providers.base import LLMProvider, LLMResponse, StreamChunk, ToolCallRequest
from picobot.providers.fallback_provider import FallbackProvider
from picobot.providers.litellm_provider import LiteLLMProvider


class StubProvider(LLMProvider):
    """Provider stub with configurable chat() behavior."""

    def __init__(self, name: str, response: LLMResponse, raise_error: Exception | None = None):
        super().__init__(api_key=None, api_base=None)
        self.name = name
        self.response = response
        self.raise_error = raise_error
        self.calls: list[dict] = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096,
                   temperature=0.7, reasoning_effort=None, tool_choice=None) -> LLMResponse:
        self.calls.append({"model": model, "messages": messages})
        if self.raise_error:
            raise self.raise_error
        return self.response

    def get_default_model(self) -> str:
        return f"{self.name}-model"


def _collect(stream):
    """Drain an async generator into a list, running its loop."""
    async def drain():
        return [chunk async for chunk in stream]
    return asyncio.run(drain())


def _stream_chunks(*items):
    """Build an async generator yielding fake LiteLLM chunks."""
    async def gen():
        for item in items:
            yield item
    return gen()


def _litellm_chunk(content="", reasoning=None, finish_reason=None, usage=None, tool_calls=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tool_call_delta(index, tool_id=None, name=None, arguments=""):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=tool_id, function=function)


class TestBaseStreamChat:
    def test_wraps_single_chat_response_as_one_final_chunk(self):
        provider = StubProvider(
            "demo",
            LLMResponse(
                content="Hello there",
                finish_reason="stop",
                usage={"total_tokens": 7},
                provider_name="demo",
                model_name="demo-model",
            ),
        )

        chunks = _collect(provider.stream_chat(messages=[{"role": "user", "content": "hi"}]))

        assert len(chunks) == 1
        assert chunks[0].content_delta == "Hello there"
        assert chunks[0].finish_reason == "stop"
        assert chunks[0].usage == {"total_tokens": 7}
        assert chunks[0].provider_name == "demo"
        assert chunks[0].model_name == "demo-model"
        assert chunks[0].is_final

    def test_passes_through_streaming_arguments(self):
        provider = StubProvider("demo", LLMResponse(content="ok", finish_reason="stop"))

        _collect(
            provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function"}],
                model="demo-model",
                max_tokens=512,
                temperature=0.1,
                reasoning_effort="low",
                tool_choice={"type": "function", "function": {"name": "demo"}},
            )
        )

        assert provider.calls[0]["model"] == "demo-model"
        assert provider.calls[0]["messages"] == [{"role": "user", "content": "hi"}]

    def test_yields_error_chunk_when_chat_raises(self):
        provider = StubProvider("demo", LLMResponse(content="ok"), raise_error=RuntimeError("boom"))

        chunks = _collect(provider.stream_chat(messages=[{"role": "user", "content": "hi"}]))

        assert len(chunks) == 1
        assert chunks[0].finish_reason == "error"
        assert "boom" in chunks[0].content_delta

    def test_cancelled_error_propagates(self):
        provider = StubProvider("demo", LLMResponse(content="ok"))

        async def run():
            gen = provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
            task = asyncio.create_task(gen.__anext__())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())


class TestLiteLLMStreamChat:
    def test_streams_content_deltas_and_final_metadata(self, monkeypatch):
        async def fake_acompletion(**kwargs):
            assert kwargs.get("stream") is True
            assert kwargs.get("model") == "demo-model"
            return _stream_chunks(
                _litellm_chunk(content="Hello"),
                _litellm_chunk(content=" world"),
                _litellm_chunk(
                    content="",
                    finish_reason="stop",
                    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
                ),
            )

        monkeypatch.setattr("picobot.providers.litellm_provider.acompletion", fake_acompletion)
        provider = LiteLLMProvider(api_key="test-key", default_model="demo-model")

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert [c.content_delta for c in chunks] == ["Hello", " world", ""]
        assert chunks[0].is_final is False
        assert chunks[1].is_final is False
        assert chunks[2].is_final is True
        assert chunks[2].finish_reason == "stop"
        assert chunks[2].usage == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}

    def test_accumulates_streamed_tool_calls(self, monkeypatch):
        async def fake_acompletion(**kwargs):
            return _stream_chunks(
                _litellm_chunk(content="Calling tool"),
                _litellm_chunk(
                    tool_calls=[_tool_call_delta(0, tool_id="call-1", name="read_file", arguments='{"path"')]
                ),
                _litellm_chunk(tool_calls=[_tool_call_delta(0, arguments=': "a.txt"}')]),
                _litellm_chunk(finish_reason="tool_calls"),
            )

        monkeypatch.setattr("picobot.providers.litellm_provider.acompletion", fake_acompletion)
        provider = LiteLLMProvider(api_key="test-key", default_model="demo-model")

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        final = chunks[-1]
        assert final.finish_reason == "tool_calls"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "read_file"
        assert final.tool_calls[0].arguments == {"path": "a.txt"}

    def test_yields_error_chunk_when_stream_fails(self, monkeypatch):
        async def fake_acompletion(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("picobot.providers.litellm_provider.acompletion", fake_acompletion)
        provider = LiteLLMProvider(api_key="test-key", default_model="demo-model")

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert len(chunks) == 1
        assert chunks[0].finish_reason == "error"
        assert "connection refused" in chunks[0].content_delta

    def test_cancelled_error_propagates_from_stream(self, monkeypatch):
        async def fake_acompletion(**kwargs):
            async def gen():
                yield _litellm_chunk(content="Hello")
                raise asyncio.CancelledError()

            return gen()

        monkeypatch.setattr("picobot.providers.litellm_provider.acompletion", fake_acompletion)
        provider = LiteLLMProvider(api_key="test-key", default_model="demo-model")

        async def run():
            gen = provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(asyncio.CancelledError):
                async for _ in gen:
                    pass

        asyncio.run(run())


class TestFallbackStreamChat:
    def test_uses_primary_stream_when_it_succeeds(self):
        primary = StubProvider("primary", LLMResponse(content="Primary streamed", finish_reason="stop"))
        fallback = StubProvider("fallback", LLMResponse(content="Should not stream", finish_reason="stop"))
        provider = FallbackProvider(
            primary=primary,
            fallback=fallback,
            primary_model="primary-model",
            fallback_model="fallback-model",
        )

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert chunks[0].content_delta == "Primary streamed"
        assert chunks[0].finish_reason == "stop"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 0
        assert primary.calls[0]["model"] == "primary-model"

    def test_falls_back_when_primary_errors_before_first_delta(self):
        primary = StubProvider(
            "primary",
            LLMResponse(content="Error calling LLM: quota exhausted", finish_reason="error"),
        )
        fallback = StubProvider("fallback", LLMResponse(content="Recovered on fallback", finish_reason="stop"))
        provider = FallbackProvider(
            primary=primary,
            fallback=fallback,
            primary_model="primary-model",
            fallback_model="fallback-model",
        )

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert chunks[0].content_delta == "Recovered on fallback"
        assert chunks[0].finish_reason == "stop"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1
        assert fallback.calls[0]["model"] == "fallback-model"

    def test_does_not_fall_back_after_content_has_flowed(self):
        class FailingMidStream(StubProvider):
            async def stream_chat(self, messages, tools=None, model=None, max_tokens=4096,
                                  temperature=0.7, reasoning_effort=None, tool_choice=None):
                yield StreamChunk(content_delta="Partial answer")
                yield StreamChunk(
                    content_delta="Error calling LLM: network dropped",
                    finish_reason="error",
                )

        primary = FailingMidStream("primary", LLMResponse(content="unused"))
        fallback = StubProvider("fallback", LLMResponse(content="Should not be used", finish_reason="stop"))
        provider = FallbackProvider(primary=primary, fallback=fallback)

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert [c.content_delta for c in chunks] == ["Partial answer", "Error calling LLM: network dropped"]
        assert chunks[-1].finish_reason == "error"
        assert len(fallback.calls) == 0

    def test_empty_primary_stream_is_not_an_error(self):
        class EmptyStream(StubProvider):
            async def stream_chat(self, messages, tools=None, model=None, max_tokens=4096,
                                  temperature=0.7, reasoning_effort=None, tool_choice=None):
                return
                yield  # pragma: no cover

        primary = EmptyStream("primary", LLMResponse(content="unused"))
        fallback = StubProvider("fallback", LLMResponse(content="Should not be used", finish_reason="stop"))
        provider = FallbackProvider(primary=primary, fallback=fallback)

        chunks = _collect(
            provider.stream_chat(messages=[{"role": "user", "content": "hi"}])
        )

        assert chunks == []
        assert len(fallback.calls) == 0


class TestStreamChunk:
    def test_is_final_tracks_finish_reason(self):
        assert StreamChunk(content_delta="hi").is_final is False
        assert StreamChunk(content_delta="hi", finish_reason="stop").is_final is True

    def test_tool_calls_and_usage_default_to_empty(self):
        chunk = StreamChunk()
        assert chunk.tool_calls == []
        assert chunk.usage == {}

    def test_carries_tool_call_requests(self):
        tool = ToolCallRequest(id="abc", name="demo", arguments={"x": 1})
        chunk = StreamChunk(finish_reason="tool_calls", tool_calls=[tool])
        assert chunk.tool_calls[0].name == "demo"
