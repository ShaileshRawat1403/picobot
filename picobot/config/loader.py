"""Configuration loading utilities."""

import json
import os
from pathlib import Path

from picobot.config.schema import Config, ProvidersConfig

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".picobot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    data: dict = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config.model_validate(_apply_profile_env(data, path.parent / ".env"))


def provider_secret_env_name(provider_name: str) -> str:
    """Return the profile-local environment variable for one provider key."""
    normalized = provider_name.strip().upper().replace("-", "_")
    return f"PICOBOT_PROVIDER_{normalized}_API_KEY"


def _provider_alias(provider_name: str) -> str:
    """Return the serialized camelCase key for a ProvidersConfig field."""
    field = ProvidersConfig.model_fields.get(provider_name)
    if field is None:
        raise ValueError(f"Unknown Pico provider: {provider_name}")
    return field.alias or provider_name


def _apply_profile_env(data: dict, env_path: Path) -> dict:
    """Overlay profile-local provider secrets without serializing them to JSON.

    ``PICOBOT_PROVIDER_<NAME>_API_KEY`` is Pico's write-only storage convention.
    The historical ``OPENAI_API_KEY`` remains a supported read-only fallback for
    existing local profiles. Environment values always stay in process memory.
    """
    if not env_path.exists():
        return data
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path)
    except Exception:
        return data

    result = dict(data)
    providers = dict(result.get("providers") or {})

    for provider_name in ProvidersConfig.model_fields:
        api_key = values.get(provider_secret_env_name(provider_name))
        if not isinstance(api_key, str) or not api_key.strip():
            continue
        alias = _provider_alias(provider_name)
        provider = dict(providers.get(alias) or providers.get(provider_name) or {})
        provider["apiKey"] = api_key.strip()
        providers[alias] = provider

    # Maintain compatibility with existing OPENAI_API_KEY profile files. A
    # Pico-specific value, if present, deliberately takes precedence.
    openai_key = values.get("OPENAI_API_KEY")
    pico_openai_key = values.get(provider_secret_env_name("openai"))
    if (
        isinstance(openai_key, str)
        and openai_key.strip()
        and not (isinstance(pico_openai_key, str) and pico_openai_key.strip())
    ):
        openai = dict(providers.get("openai") or {})
        openai["apiKey"] = openai_key.strip()
        providers["openai"] = openai

    result["providers"] = providers
    return result


def set_profile_provider_secret(
    provider_name: str, api_key: str, config_path: Path | None = None
) -> None:
    """Persist one provider key in the active profile's local ``.env`` file.

    This is intentionally write-only: callers receive no key and config.json
    never becomes a secret store.
    """
    _provider_alias(provider_name)
    secret = api_key.strip()
    if not secret or len(secret) > 4096:
        raise ValueError("Provider API key must contain between 1 and 4096 characters")

    path = config_path or get_config_path()
    env_path = path.parent / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from dotenv import set_key

        set_key(env_path, provider_secret_env_name(provider_name), secret)
        os.chmod(env_path, 0o600)
    except Exception as exc:
        raise ValueError("Pico could not securely save the provider key") from exc


def clear_profile_provider_secret(provider_name: str, config_path: Path | None = None) -> None:
    """Remove one provider key without reading or returning its value."""
    _provider_alias(provider_name)
    path = config_path or get_config_path()
    env_path = path.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import unset_key

        unset_key(env_path, provider_secret_env_name(provider_name))
        os.chmod(env_path, 0o600)
    except Exception as exc:
        raise ValueError("Pico could not securely disconnect the provider") from exc


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)
    providers = data.get("providers") or {}

    # Move any legacy in-memory/config JSON credentials into the profile-local
    # .env file before writing. The serialized configuration always has blank
    # provider keys, even when the runtime configuration was loaded with one.
    for provider_name in ProvidersConfig.model_fields:
        alias = _provider_alias(provider_name)
        provider = providers.get(alias)
        if not isinstance(provider, dict):
            continue
        api_key = provider.get("apiKey")
        if isinstance(api_key, str) and api_key.strip():
            set_profile_provider_secret(provider_name, api_key, path)
            provider["apiKey"] = ""

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
