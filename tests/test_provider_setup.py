"""Unit tests for CH7 — Safe Provider Setup and Diagnostics."""

from pathlib import Path
from unittest.mock import patch

import pytest

from picobot.config.loader import load_config, save_config, set_profile_provider_secret
from picobot.policy.runtime import RuntimePolicyService
from picobot.providers.setup import ProviderSetupService


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    config_dir = tmp_path / ".picobot"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config = load_config(config_file)
    save_config(config, config_file)
    return config_file


def test_provider_status_projection_contains_no_secrets(tmp_workspace: Path):
    """Test 1: Provider status projection contains safe metadata and no secrets."""
    set_profile_provider_secret("openai", "sk-proj-secret12345", tmp_workspace)
    service = ProviderSetupService(tmp_workspace)
    inventory = service.inventory()

    assert "providers" in inventory
    assert "default" in inventory

    for provider in inventory["providers"]:
        # Verify safe metadata fields exist
        assert "id" in provider
        assert "label" in provider
        assert "status" in provider
        assert "setup_hint" in provider
        assert "has_api_key" in provider
        assert "has_endpoint" in provider

        # Verify no secret values are leaked
        provider_str = str(provider).lower()
        assert "sk-proj-secret12345" not in provider_str
        assert "secret" not in provider
        assert "api_key" not in provider
        assert "token" not in provider
        assert "password" not in provider


def test_unavailable_provider_cannot_be_selected_by_policy(tmp_workspace: Path):
    """Test 2: Unavailable/unconfigured provider cannot be selected by policy."""
    policy_service = RuntimePolicyService(tmp_workspace)
    setup_service = ProviderSetupService(tmp_workspace)

    # 1. Unconfigured provider cannot be selected as default in setup_service
    with pytest.raises(ValueError, match="outside Pico's supported provider boundary"):
        setup_service.choose_default("deepseek", "deepseek-chat")

    # 2. Unconfigured provider cannot be set in global runtime policy
    with pytest.raises(ValueError, match="is not ready to serve turns"):
        policy_service.set_global({"provider": "deepseek", "model": "deepseek-chat"})

    # 3. Configure key for openai -> selection succeeds
    set_profile_provider_secret("openai", "sk-testkey", tmp_workspace)
    result = policy_service.set_global({"provider": "openai", "model": "gpt-4o"})
    assert result["policy"]["provider"] == "openai"
    assert result["policy"]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_successful_and_failed_diagnostics_are_bounded_and_redacted(tmp_workspace: Path):
    """Test 3: Connection test returns bounded, redacted failure categories."""
    service = ProviderSetupService(tmp_workspace)

    # 1. Test unconfigured provider -> key_missing
    res1 = await service.test_connection("openai")
    assert res1["status"] == "not_configured"
    assert res1["failure_category"] == "key_missing"
    assert res1["detail"] == "Add an API key before testing this provider."
    assert "sk-" not in str(res1)

    # 2. Subscription readiness is delegated to the official provider CLI.
    with patch(
        "picobot.providers.setup.subscription_status",
        return_value={
            "status": "setup_required",
            "failure_category": "login_required",
            "detail": "Run `codex login` to connect your ChatGPT/Codex subscription.",
            "cli_available": True,
        },
    ):
        res2 = await service.test_connection("openai_codex")
    assert res2["status"] == "setup_required"
    assert res2["failure_category"] == "login_required"

    # 3. Test with configured key and mocked successful probe -> connection_verified
    set_profile_provider_secret("openai", "sk-validkey", tmp_workspace)
    with patch.object(ProviderSetupService, "_probe_models", return_value=200):
        res3 = await service.test_connection("openai")
        assert res3["status"] == "ready"
        assert res3["failure_category"] == "connection_verified"
        assert res3["detail"] == "Connection verified."

    # 4. Test with mocked permission error -> auth_failed
    with patch.object(ProviderSetupService, "_probe_models", side_effect=PermissionError("HTTP 401 Unauthorized")):
        res4 = await service.test_connection("openai")
        assert res4["status"] == "error"
        assert res4["failure_category"] == "auth_failed"
        assert res4["detail"] == "The provider rejected Pico's credentials."
        assert "401" not in res4["detail"]  # redacted

    # 5. Test with mocked network failure -> unreachable
    with patch.object(ProviderSetupService, "_probe_models", side_effect=OSError("Connection refused")):
        res5 = await service.test_connection("openai")
        assert res5["status"] == "error"
        assert res5["failure_category"] == "unreachable"
        assert res5["detail"] == "Pico could not reach that provider endpoint."
        assert "refused" not in res5["detail"]  # redacted


@pytest.mark.asyncio
async def test_action_does_not_persist_provider_selection_as_side_effect(tmp_workspace: Path):
    """Test 4: Testing connection does NOT alter stored default provider or runtime policy."""
    service = ProviderSetupService(tmp_workspace)
    set_profile_provider_secret("openai", "sk-validkey", tmp_workspace)
    set_profile_provider_secret("deepseek", "sk-deepseekkey", tmp_workspace)

    initial_config = load_config(tmp_workspace)
    initial_default_provider = initial_config.agents.defaults.provider
    initial_default_model = initial_config.agents.defaults.model
    initial_policy = initial_config.policy.model_dump()

    # Deferred providers are not probed, even if an old key exists.
    with patch.object(ProviderSetupService, "_probe_models", return_value=200):
        res = await service.test_connection("deepseek")
        assert res["status"] == "not_available"
        assert res["failure_category"] == "unsupported"

    # Verify config defaults and policy were NOT modified as a side effect
    after_config = load_config(tmp_workspace)
    assert after_config.agents.defaults.provider == initial_default_provider
    assert after_config.agents.defaults.model == initial_default_model
    assert after_config.policy.model_dump() == initial_policy


@pytest.mark.asyncio
async def test_reconfiguring_a_provider_clears_its_stale_diagnostic(tmp_workspace: Path):
    """A previous failed probe cannot lock out newly supplied credentials."""
    service = ProviderSetupService(tmp_workspace)
    set_profile_provider_secret("openai", "sk-old-key", tmp_workspace)
    with patch.object(ProviderSetupService, "_probe_models", side_effect=PermissionError):
        result = await service.test_connection("openai")
    assert result["failure_category"] == "auth_failed"
    assert next(item for item in service.inventory()["providers"] if item["id"] == "openai")["status"] == "unavailable"

    service.configure_api_key("openai", "sk-replacement-key")
    refreshed = next(item for item in service.inventory()["providers"] if item["id"] == "openai")
    assert refreshed["status"] == "configured"
    assert refreshed["last_diagnostic"] is None
