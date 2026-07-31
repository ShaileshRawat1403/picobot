"""Configuration loading utilities."""

import json
from pathlib import Path

from picobot.config.schema import Config

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


def _apply_profile_env(data: dict, env_path: Path) -> dict:
    """Overlay profile-local secrets without storing them in config.json.

    The profile uses the conventional ``OPENAI_API_KEY`` name. It is read only
    in process and takes precedence over an empty JSON provider field.
    """
    if not env_path.exists():
        return data
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path)
    except Exception:
        return data

    openai_key = values.get("OPENAI_API_KEY")
    if not isinstance(openai_key, str) or not openai_key.strip():
        return data

    result = dict(data)
    providers = dict(result.get("providers") or {})
    openai = dict(providers.get("openai") or {})
    openai["apiKey"] = openai_key.strip()
    providers["openai"] = openai
    result["providers"] = providers
    return result


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
