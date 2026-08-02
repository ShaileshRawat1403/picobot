"""Tests for Pico's deliberately small provider connection boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest

from picobot.config.loader import load_config, save_config
from picobot.providers.connections import (
    SUPPORTED_PROVIDER_NAMES,
    subscription_status,
)
from picobot.providers.setup import ProviderSetupService
from picobot.providers.subscription_cli import (
    SubscriptionCLIProvider,
    _parse_codex_jsonl,
    _parse_gemini_json,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / ".picobot" / "config.json"
    path.parent.mkdir()
    save_config(load_config(path), path)
    return path


def test_supported_provider_surface_is_small_and_explicit():
    assert SUPPORTED_PROVIDER_NAMES == (
        "openai",
        "gemini",
        "anthropic",
        "ollama",
        "custom",
        "openai_codex",
        "gemini_oauth",
    )


def test_inventory_hides_deferred_provider_catalogue(config_path: Path):
    service = ProviderSetupService(config_path)
    with patch(
        "picobot.providers.setup.subscription_status",
        return_value={
            "status": "setup_required",
            "failure_category": "login_required",
            "detail": "Login required.",
            "cli_available": True,
        },
    ):
        inventory = service.inventory()
    ids = [entry["id"] for entry in inventory["providers"]]
    assert ids == list(SUPPORTED_PROVIDER_NAMES)
    assert "deepseek" not in ids
    assert "openrouter" not in ids


@pytest.mark.asyncio
async def test_deferred_provider_diagnostic_is_fail_closed(config_path: Path):
    service = ProviderSetupService(config_path)
    result = await service.test_connection("deepseek")
    assert result["status"] == "not_available"
    assert result["failure_category"] == "unsupported"


def test_subscription_status_never_returns_cli_output_or_tokens():
    fake_result = type("Result", (), {"returncode": 0})()
    with patch("picobot.providers.connections.shutil.which", return_value="/usr/bin/codex"), patch(
        "picobot.providers.connections.subprocess.run", return_value=fake_result
    ) as run:
        result = subscription_status("openai_codex")
    run.assert_called_once()
    assert result["status"] == "ready"
    assert "stdout" not in result
    assert "token" not in str(result).lower()


@pytest.mark.asyncio
async def test_subscription_cli_command_never_receives_tools():
    provider = SubscriptionCLIProvider("openai_codex", "openai-codex/gpt-5.3-codex")
    response = await provider.chat([{"role": "user", "content": "hello"}], tools=[{"name": "exec"}])
    assert response.finish_reason == "error"
    assert "do not expose Pico tools" in (response.content or "")


def test_subscription_output_parsers_are_bounded():
    codex_output = '\n'.join(
        [
            '{"type":"item.completed","item":{"type":"agent_message","text":"intermediate"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
            '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}',
        ]
    )
    assert _parse_codex_jsonl(codex_output) == ("final", {"input_tokens": 3, "output_tokens": 2})
    assert _parse_gemini_json('{"response":"hello","usageMetadata":{"totalTokenCount":4}}') == (
        "hello",
        {"total_tokens": 4},
    )
