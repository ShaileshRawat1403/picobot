"""LLM provider wrapper that falls back to a secondary provider on failure."""

from __future__ import annotations

from loguru import logger

from picobot.providers.base import LLMProvider, LLMResponse


class FallbackProvider(LLMProvider):
    """Wrap a primary provider and retry on a fallback provider when needed."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ):
        super().__init__(api_key=None, api_base=None)
        self.primary = primary
        self.fallback = fallback
        self.primary_model = primary_model or primary.get_default_model()
        self.fallback_model = fallback_model or fallback.get_default_model()

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
        return await self._dispatch(
            use_retry=False,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

    async def chat_with_retry(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        max_tokens: object = LLMProvider._SENTINEL,
        temperature: object = LLMProvider._SENTINEL,
        reasoning_effort: object = LLMProvider._SENTINEL,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        if max_tokens is self._SENTINEL:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        return await self._dispatch(
            use_retry=True,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

    async def _dispatch(
        self,
        *,
        use_retry: bool,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict | None,
    ) -> LLMResponse:
        primary_model = model or self.primary_model
        primary_response = await self._call_provider(
            self.primary,
            primary_model,
            use_retry=use_retry,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

        if not self._should_fallback(primary_response):
            primary_response.provider_name = primary_response.provider_name or getattr(self.primary, "__class__", type(self.primary)).__name__
            primary_response.model_name = primary_response.model_name or primary_model
            return primary_response

        logger.warning(
            "Primary provider failed for model {}. Falling back to {}",
            primary_model,
            self.fallback_model,
        )

        fallback_response = await self._call_provider(
            self.fallback,
            self.fallback_model,
            use_retry=use_retry,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

        if fallback_response.finish_reason != "error":
            fallback_response.provider_name = fallback_response.provider_name or getattr(self.fallback, "__class__", type(self.fallback)).__name__
            fallback_response.model_name = fallback_response.model_name or self.fallback_model
            return fallback_response

        combined = "\n\n".join(
            part for part in [primary_response.content, fallback_response.content] if part
        )
        return LLMResponse(
            content=combined or "Primary and fallback providers both failed.",
            finish_reason="error",
            provider_name=fallback_response.provider_name or primary_response.provider_name,
            model_name=fallback_response.model_name or self.fallback_model,
        )

    async def _call_provider(
        self,
        provider: LLMProvider,
        model: str,
        *,
        use_retry: bool,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict | None,
    ) -> LLMResponse:
        method = provider.chat_with_retry if use_retry else provider.chat
        return await method(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )

    @staticmethod
    def _should_fallback(response: LLMResponse) -> bool:
        if response.finish_reason != "error":
            return False
        if response.tool_calls:
            return False
        return True

    def get_default_model(self) -> str:
        return self.primary_model
