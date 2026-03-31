"""
Gemini OAuth Provider for Picobot
Uses DAX OAuth tokens with Code Assist API (Pro/Plus serverless).
"""

import json
import os
import time
import uuid
from typing import Any

import httpx
from loguru import logger

from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class GeminiOAuthProvider(LLMProvider):
    """Gemini provider using DAX OAuth with Code Assist API (serverless)."""

    # Code Assist API (Pro/Plus serverless endpoint)
    CODE_ASSIST_URL = "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
    CODE_ASSIST_LOAD_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
    CODE_ASSIST_ONBOARD_URL = "https://cloudcode-pa.googleapis.com/v1internal:onboardUser"

    DAX_AUTH_PATH = os.path.expanduser("~/.local/share/dax/auth.json")

    def __init__(self):
        super().__init__(api_key=None, api_base=None)
        self._token = ""
        self._token_expires_ms = 0
        self._reload_auth(force=True)
        self._quota_project = None

    def _load_auth(self) -> tuple[str, int]:
        """Load OAuth token state from DAX auth.json."""
        if not os.path.exists(self.DAX_AUTH_PATH):
            raise ValueError(f"DAX auth not found at {self.DAX_AUTH_PATH}")

        with open(self.DAX_AUTH_PATH, encoding="utf-8") as f:
            auth = json.load(f)

        google = auth.get("google", {})
        token = google.get("access")
        expires = int(google.get("expires") or 0)

        if not token:
            raise ValueError("No access token in DAX auth.json")

        return token, expires

    def _reload_auth(self, force: bool = False) -> None:
        """Reload auth from disk when missing, rotated, or near expiry."""
        now_ms = int(time.time() * 1000)
        if not force and self._token and self._token_expires_ms and now_ms < (self._token_expires_ms - 60_000):
            return

        previous_token = self._token
        token, expires = self._load_auth()
        self._token = token
        self._token_expires_ms = expires

        # Quota-project lookup is tied to auth context; refresh it on token change.
        if force or token != previous_token:
            self._quota_project = None

    def _auth_headers(self) -> dict[str, str]:
        """Build request headers with a fresh OAuth token."""
        self._reload_auth()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "GoogleCloud/1.0.0 GeminiCLI/0.34.0",
        }

    async def _resolve_quota_project(self) -> str:
        """Resolve quota project for Code Assist API."""
        if self._quota_project:
            return self._quota_project

        headers = self._auth_headers()

        metadata = {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }

        for url in [self.CODE_ASSIST_LOAD_URL, self.CODE_ASSIST_ONBOARD_URL]:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(url, headers=headers, json={"metadata": metadata})
                    if response.status_code == 200:
                        data = response.json()
                        project = data.get("cloudaicompanionProject", {})
                        if isinstance(project, dict):
                            self._quota_project = project.get("id", "free-tier")
                        elif isinstance(project, str) and project:
                            self._quota_project = project
                        else:
                            self._quota_project = "free-tier"
                        return self._quota_project
            except Exception:
                pass

        self._quota_project = "free-tier"
        return self._quota_project

    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        return "gemini-2.5-pro"

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[dict, dict | None]:
        """Build payload for Generative Language API."""
        gemini_contents = []
        system_instruction = None

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                if isinstance(content, str):
                    system_instruction = {"parts": [{"text": content}]}
                continue

            if role == "tool":
                name = msg.get("name", "unknown")
                part = {
                    "functionResponse": {
                        "name": name,
                        "response": {"content": content}
                    }
                }

                if gemini_contents and gemini_contents[-1]["role"] == "user":
                    gemini_contents[-1]["parts"].append(part)
                else:
                    gemini_contents.append({
                        "role": "user",
                        "parts": [part]
                    })
                continue

            gemini_role = "model" if role == "assistant" else "user"

            parts = []
            if isinstance(content, str) and content:
                parts.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append({"text": part.get("text", "")})
                    elif isinstance(part, str):
                        parts.append({"text": part})

            if role == "assistant" and "tool_calls" in msg and msg["tool_calls"]:
                for tool_call in msg["tool_calls"]:
                    func = tool_call.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError:
                        args = {}
                    parts.append({
                        "functionCall": {
                            "name": func.get("name", ""),
                            "args": args
                        }
                    })

            if parts:
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": parts
                })

        generation_config = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }

        payload = {
            "contents": gemini_contents,
            "generationConfig": generation_config
        }

        if system_instruction:
            payload["systemInstruction"] = system_instruction

        if tools:
            function_declarations = self._build_function_declarations(tools)
            if function_declarations:
                payload["tools"] = [{"functionDeclarations": function_declarations}]
                payload["toolConfig"] = {
                    "functionCallingConfig": {"mode": "AUTO"}
                }

        return payload, system_instruction

    def _build_function_declarations(self, tools: list[dict[str, Any]]) -> list:
        """Build function declarations from tools."""
        function_declarations = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                parameters = func.get("parameters", {}).copy()

                def fix_param_types(param_schema):
                    if isinstance(param_schema, dict):
                        if param_schema.get("type") == "object":
                            param_schema["type"] = "object"
                        if "properties" in param_schema:
                            for prop in param_schema["properties"].values():
                                fix_param_types(prop)
                        if "items" in param_schema:
                            fix_param_types(param_schema["items"])
                    return param_schema
                fix_param_types(parameters)

                function_declarations.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": parameters
                })

        return function_declarations

    def _parse_response(self, result: dict) -> LLMResponse:
        """Parse API response into LLMResponse."""
        if "candidates" not in result or not result["candidates"]:
            raise ValueError(f"No candidates in response: {json.dumps(result)}")

        candidate = result["candidates"][0]
        if "content" not in candidate or "parts" not in candidate["content"]:
            raise ValueError(f"No content parts in response: {json.dumps(candidate)}")

        text_parts = []
        tool_calls = []

        for part in candidate["content"]["parts"]:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fn_call = part["functionCall"]
                name = fn_call.get("name", "")
                args = fn_call.get("args", {})
                tool_calls.append(ToolCallRequest(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=name,
                    arguments=args
                ))

        content = "".join(text_parts)

        finish_reason = "stop"
        if candidate.get("finishReason") == "MAX_TOKENS":
            finish_reason = "length"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            provider_name="gemini_oauth",
        )

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
        """Send a chat completion request using Code Assist API (Pro/Plus serverless)."""
        model_name = model or self.get_default_model()

        # Get quota project
        project = await self._resolve_quota_project()

        # Build standard payload
        payload, _ = self._build_payload(
            messages, tools, model_name, temperature, max_tokens
        )

        # Wrap for Code Assist API
        wrapped_payload = {
            "project": project,
            "model": model_name,
            "user_prompt_id": uuid.uuid4().hex,
            "request": {
                **payload,
                "session_id": uuid.uuid4().hex,
            },
        }

        async def _send_request() -> httpx.Response:
            headers = {
                **self._auth_headers(),
                "x-activity-request-id": uuid.uuid4().hex[:16],
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                return await client.post(self.CODE_ASSIST_URL, headers=headers, json=wrapped_payload)

        response = await _send_request()

        if response.status_code == 401:
            logger.warning("Gemini OAuth returned 401; reloading DAX auth token and retrying once")
            self._reload_auth(force=True)
            self._quota_project = None
            wrapped_payload["project"] = await self._resolve_quota_project()
            response = await _send_request()

        if response.status_code == 429:
            # Parse quota exhaustion message
            try:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", "")

                # Extract retry time if available
                import re
                retry_match = re.search(r'(\d+)s', error_msg)
                if retry_match:
                    retry_seconds = retry_match.group(1)
                    raise ValueError(f"Gemini quota exhausted. Retry in {retry_seconds}s.")
                else:
                    raise ValueError("Gemini quota exhausted. Please wait and try again.")
            except ValueError:
                raise
            except Exception:
                raise ValueError("Gemini quota exhausted. Please wait and try again.")

        if response.status_code != 200:
            raise ValueError(f"Gemini API error: {response.text[:200]}")

        result = response.json()

        # Extract response from Code Assist wrapper
        if "response" in result:
            result = result["response"]

        parsed = self._parse_response(result)
        parsed.model_name = model_name
        return parsed


def create_provider(**kwargs):
    """Factory function for compatibility with Picobot's provider interface."""
    return GeminiOAuthProvider()
