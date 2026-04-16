#!/usr/bin/env python3
"""Tests for provider fallback behavior."""

from __future__ import annotations

import asyncio

from picobot.cli.commands import _make_provider
from picobot.config.schema import Config
from picobot.providers.base import LLMProvider, LLMResponse
from picobot.providers.fallback_provider import FallbackProvider


class DummyProvider(LLMProvider):
    """Minimal provider stub for fallback tests."""

    def __init__(self, name: str, response: LLMResponse):
        super().__init__(api_key=None, api_base=None)
        self.name = name
        self.response = response
        self.calls: list[dict] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "tool_choice": tool_choice,
            }
        )
        return self.response

    def get_default_model(self) -> str:
        return f"{self.name}-model"


class TestFallbackProvider:
    def test_uses_fallback_when_primary_errors(self):
        primary = DummyProvider(
            "primary",
            LLMResponse(content="Error calling LLM: quota exhausted", finish_reason="error"),
        )
        fallback = DummyProvider(
            "fallback",
            LLMResponse(content="Recovered on fallback", finish_reason="stop"),
        )
        provider = FallbackProvider(
            primary=primary,
            fallback=fallback,
            primary_model="gemini-2.5-pro",
            fallback_model="openai-codex/gpt-5.1-codex",
        )

        response = asyncio.run(
            provider.chat_with_retry(
                messages=[{"role": "user", "content": "hello"}],
                tools=[{"type": "function", "function": {"name": "demo"}}],
            )
        )

        assert response.content == "Recovered on fallback"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1
        assert primary.calls[0]["model"] == "gemini-2.5-pro"
        assert fallback.calls[0]["model"] == "openai-codex/gpt-5.1-codex"

    def test_skips_fallback_when_primary_succeeds(self):
        primary = DummyProvider(
            "primary",
            LLMResponse(content="Primary worked", finish_reason="stop"),
        )
        fallback = DummyProvider(
            "fallback",
            LLMResponse(content="Should not be used", finish_reason="stop"),
        )
        provider = FallbackProvider(primary=primary, fallback=fallback)

        response = asyncio.run(provider.chat(messages=[{"role": "user", "content": "hello"}]))

        assert response.content == "Primary worked"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 0


class TestProviderBootstrap:
    def test_make_provider_wraps_gemini_oauth_with_default_codex_fallback(self, monkeypatch):
        primary = DummyProvider("gemini", LLMResponse(content="ok", finish_reason="stop"))
        fallback_instances: list[DummyProvider] = []

        def fake_create_provider(**kwargs):
            return primary

        def fake_codex_provider(default_model: str):
            instance = DummyProvider("codex", LLMResponse(content="ok", finish_reason="stop"))
            instance.default_model = default_model
            fallback_instances.append(instance)
            return instance

        monkeypatch.setattr("picobot.providers.gemini_oauth_provider.create_provider", fake_create_provider)
        monkeypatch.setattr("picobot.providers.openai_codex_provider.OpenAICodexProvider", fake_codex_provider)

        config = Config.model_validate(
            {
                "agents": {
                    "defaults": {
                        "model": "gemini-2.5-pro",
                        "provider": "gemini_oauth",
                    }
                }
            }
        )

        provider = _make_provider(config)

        assert isinstance(provider, FallbackProvider)
        assert provider.primary is primary
        assert provider.primary_model == "gemini-2.5-pro"
        assert provider.fallback_model == "openai-codex/gpt-5.1-codex"
        assert len(fallback_instances) == 1
