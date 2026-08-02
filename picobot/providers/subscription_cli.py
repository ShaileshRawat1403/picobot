"""Provider-owned CLI transports for subscription connections.

Pico does not read or replay subscription tokens. These adapters invoke the
official local CLI, which owns authentication, refresh, terms, and transport.
They intentionally do not expose Pico tools to the subscription CLI process;
governed execution should use an API connection with Pico's normal tool loop.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from picobot.providers.base import LLMProvider, LLMResponse


class SubscriptionCLIProvider(LLMProvider):
    """Call an official provider CLI without handling its credentials."""

    def __init__(self, provider_name: str, default_model: str, timeout_s: float = 180.0):
        super().__init__(api_key=None, api_base=None)
        if provider_name not in {"openai_codex", "gemini_oauth"}:
            raise ValueError("Unsupported subscription provider")
        self.provider_name = provider_name
        self.default_model = default_model
        self.timeout_s = timeout_s
        self.executable = shutil.which("codex" if provider_name == "openai_codex" else "gemini")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        selected_model = model or self.default_model
        if tools:
            return self._error(
                "Subscription CLI connections do not expose Pico tools; select an API provider for governed execution.",
                selected_model,
            )
        if not self.executable:
            return self._error(self._missing_cli_message(), selected_model)

        prompt = _render_messages(messages)
        command = self._command(selected_model, prompt)
        try:
            stdout, returncode = await self._run(command)
        except asyncio.TimeoutError:
            return self._error("The provider CLI timed out before returning a response.", selected_model)
        except OSError:
            return self._error("Pico could not start the provider's official CLI.", selected_model)

        content, usage = self._parse_output(stdout)
        if returncode != 0 or not content:
            return self._error("The provider CLI did not return a usable response.", selected_model)
        return LLMResponse(
            content=content,
            finish_reason="stop",
            usage=usage,
            provider_name=self.provider_name,
            model_name=selected_model,
        )

    async def _run(self, command: list[str]) -> tuple[str, int]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise
        return stdout.decode("utf-8", errors="replace"), process.returncode or 0

    def _command(self, model: str, prompt: str) -> list[str]:
        if self.provider_name == "openai_codex":
            selected = model.replace("openai-codex/", "").replace("openai_codex/", "")
            return [
                self.executable or "codex",
                "exec",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--model",
                selected,
                prompt,
            ]
        return [
            self.executable or "gemini",
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
            "--approval-mode",
            "plan",
            "--skip-trust",
            "--model",
            model,
        ]

    def _parse_output(self, stdout: str) -> tuple[str | None, dict[str, int]]:
        if self.provider_name == "openai_codex":
            return _parse_codex_jsonl(stdout)
        return _parse_gemini_json(stdout)

    def _missing_cli_message(self) -> str:
        if self.provider_name == "openai_codex":
            return "Install the official Codex CLI and run `codex login` first."
        return "Install the official Gemini CLI and sign in with Google first."

    def _error(self, message: str, model: str) -> LLMResponse:
        return LLMResponse(
            content=message,
            finish_reason="error",
            provider_name=self.provider_name,
            model_name=model,
        )

    def get_default_model(self) -> str:
        return self.default_model


def _render_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("text")
            )
        if not isinstance(content, str):
            content = str(content or "")
        if content:
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _parse_codex_jsonl(stdout: str) -> tuple[str | None, dict[str, int]]:
    latest: str | None = None
    usage: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") in {"agent_message", "assistant_message"}:
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    latest = text
        if event.get("type") == "turn.completed":
            raw_usage = event.get("usage") or {}
            usage = {
                key: int(raw_usage[key])
                for key in ("input_tokens", "output_tokens")
                if isinstance(raw_usage.get(key), (int, float))
            }
    return latest, usage


def _parse_gemini_json(stdout: str) -> tuple[str | None, dict[str, int]]:
    # The official CLI's stream-json mode emits one event per line. Keep the
    # parser tolerant of the older single-object JSON mode as well so a CLI
    # upgrade cannot turn a valid response into an opaque provider failure.
    stream_content: str | None = None
    stream_usage: dict[str, int] = {}
    saw_stream_event = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or "type" not in event:
            continue
        saw_stream_event = True
        if event.get("type") == "message" and event.get("role") == "assistant":
            value = event.get("content")
            if isinstance(value, str) and value.strip():
                stream_content = value
        if event.get("type") == "result":
            stream_usage = _usage_from_payload(event)
    if saw_stream_event:
        return stream_content, stream_usage

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip() or None, {}
    if isinstance(payload, dict):
        for key in ("response", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value, _usage_from_payload(payload)
    return None, _usage_from_payload(payload) if isinstance(payload, dict) else {}


def _usage_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") or payload.get("usageMetadata") or payload.get("stats") or {}
    mapping = {
        "promptTokenCount": "input_tokens",
        "candidatesTokenCount": "output_tokens",
        "totalTokenCount": "total_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    result: dict[str, int] = {}
    for source, target in mapping.items():
        if isinstance(raw.get(source), (int, float)):
            result[target] = int(raw[source])
    return result
