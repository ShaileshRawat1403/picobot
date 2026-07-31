from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from picobot.providers.setup import ProviderSetupService


def _service(tmp_path: Path) -> ProviderSetupService:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    return ProviderSetupService(config_path)


def test_provider_inventory_is_metadata_only_and_marks_local_key_configuration(tmp_path: Path):
    service = _service(tmp_path)

    configured = service.configure_api_key("openai", "test-openai-key")
    inventory = service.inventory()
    serialized = json.dumps(inventory)

    assert configured["status"] == "configured"
    assert next(item for item in inventory["providers"] if item["id"] == "openai")["has_api_key"]
    assert "test-openai-key" not in serialized
    assert "test-openai-key" not in (tmp_path / "config.json").read_text(encoding="utf-8")


def test_custom_endpoint_requires_https_or_loopback_http(tmp_path: Path):
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="Use HTTPS"):
        service.configure_custom_endpoint("http://remote.example.test/v1", "test-model")

    result = service.configure_custom_endpoint("http://127.0.0.1:11434/v1", "local-model")

    assert result["default"] == {
        "provider": "custom",
        "model": "local-model",
        "applies_after_restart": True,
    }
    custom = next(item for item in result["providers"] if item["id"] == "custom")
    assert custom["status"] == "configured"
    assert custom["has_endpoint"] is True


def test_default_provider_requires_real_configuration(tmp_path: Path):
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="Configure this provider"):
        service.choose_default("openai", "gpt-4.1-mini")

    service.configure_api_key("openai", "test-openai-key")
    result = service.choose_default("openai", "gpt-4.1-mini")

    assert result["default"]["provider"] == "openai"
    assert result["default"]["model"] == "gpt-4.1-mini"


def test_openai_connection_probe_is_truthful_without_a_key(tmp_path: Path):
    service = _service(tmp_path)

    result = asyncio.run(service.test_connection("openai"))

    assert result == {
        "provider": "openai",
        "status": "not_configured",
        "detail": "Add an API key before testing this provider.",
    }
