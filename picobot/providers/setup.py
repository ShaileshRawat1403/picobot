"""Local-first provider setup without exposing credentials to the web client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from picobot.config.loader import get_config_path, load_config, set_profile_provider_secret, save_config
from picobot.config.schema import ProvidersConfig
from picobot.providers.registry import PROVIDERS, find_by_name


class ProviderSetupService:
    """Manage the active local Pico profile's provider configuration.

    Public return values are deliberately metadata-only. API keys are accepted
    to write local profile storage and are never returned, logged, or rendered.
    """

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or get_config_path()

    @staticmethod
    def _provider_names() -> tuple[str, ...]:
        return tuple(ProvidersConfig.model_fields)

    @classmethod
    def _validate_provider_name(cls, provider_name: str) -> str:
        if not isinstance(provider_name, str) or provider_name not in cls._provider_names():
            raise ValueError("Unknown Pico provider")
        return provider_name

    @staticmethod
    def _kind(provider_name: str) -> str:
        spec = find_by_name(provider_name)
        if spec is None:
            return "standard"
        if spec.is_oauth:
            return "oauth"
        if spec.is_local:
            return "local"
        if provider_name == "custom":
            return "custom"
        if spec.is_gateway:
            return "gateway"
        return "standard"

    @staticmethod
    def _label(provider_name: str) -> str:
        spec = find_by_name(provider_name)
        return spec.label if spec else provider_name.replace("_", " ").title()

    def _entry(self, provider_name: str, config) -> dict:
        provider = getattr(config.providers, provider_name)
        kind = self._kind(provider_name)
        has_key = bool(provider.api_key)
        has_endpoint = bool(provider.api_base)
        if kind == "oauth":
            status = "setup_required"
            detail = "OAuth setup is not available in Pico's web workbench yet."
        elif kind == "local":
            status = "configured" if has_endpoint else "not_configured"
            detail = "Local endpoint configured." if has_endpoint else "Add a local endpoint to use this provider."
        elif kind == "custom":
            status = "configured" if has_endpoint else "not_configured"
            detail = "Custom endpoint configured." if has_endpoint else "Add an OpenAI-compatible endpoint."
        elif has_key:
            status = "configured"
            detail = "API key stored locally."
        else:
            status = "not_configured"
            detail = "API key required."

        return {
            "id": provider_name,
            "label": self._label(provider_name),
            "kind": kind,
            "status": status,
            "detail": detail,
            "has_api_key": has_key,
            "has_endpoint": has_endpoint,
            "is_active": config.agents.defaults.provider == provider_name,
        }

    def inventory(self) -> dict:
        config = load_config(self.config_path)
        return {
            "providers": [self._entry(name, config) for name in self._provider_names()],
            "default": {
                "provider": config.agents.defaults.provider,
                "model": config.agents.defaults.model,
                "applies_after_restart": True,
            },
        }

    def configure_api_key(self, provider_name: str, api_key: str) -> dict:
        provider_name = self._validate_provider_name(provider_name)
        if self._kind(provider_name) in {"oauth", "local"}:
            raise ValueError("This provider does not accept an API key in Pico's web workbench")
        if not isinstance(api_key, str):
            raise ValueError("Provider API key is required")
        set_profile_provider_secret(provider_name, api_key, self.config_path)
        return self._entry(provider_name, load_config(self.config_path))

    @staticmethod
    def validate_custom_endpoint(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Endpoint URL is required")
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint URL must not include credentials, a query, or a fragment")
        if parsed.scheme == "https" and host:
            return value.strip().rstrip("/")
        if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
            return value.strip().rstrip("/")
        raise ValueError("Use HTTPS, or HTTP only for a loopback endpoint")

    def configure_custom_endpoint(
        self, endpoint: str, model: str, api_key: str | None = None
    ) -> dict:
        safe_endpoint = self.validate_custom_endpoint(endpoint)
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 240:
            raise ValueError("Default model must contain between 1 and 240 characters")
        if api_key is not None:
            if not isinstance(api_key, str):
                raise ValueError("Custom endpoint API key must be text")
            if api_key.strip():
                set_profile_provider_secret("custom", api_key, self.config_path)

        config = load_config(self.config_path)
        config.providers.custom.api_base = safe_endpoint
        config.agents.defaults.provider = "custom"
        config.agents.defaults.model = model.strip()
        save_config(config, self.config_path)
        return self.inventory()

    def choose_default(self, provider_name: str, model: str) -> dict:
        provider_name = self._validate_provider_name(provider_name)
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 240:
            raise ValueError("Model must contain between 1 and 240 characters")
        config = load_config(self.config_path)
        entry = self._entry(provider_name, config)
        if entry["status"] != "configured":
            raise ValueError("Configure this provider before making it the default")
        config.agents.defaults.provider = provider_name
        config.agents.defaults.model = model.strip()
        save_config(config, self.config_path)
        return self.inventory()

    async def test_connection(self, provider_name: str) -> dict:
        """Run a bounded, read-only OpenAI-compatible ``/models`` probe where valid."""
        provider_name = self._validate_provider_name(provider_name)
        config = load_config(self.config_path)
        provider = getattr(config.providers, provider_name)
        spec = find_by_name(provider_name)

        endpoint = provider.api_base
        if provider_name == "openai":
            endpoint = endpoint or "https://api.openai.com/v1"
        elif spec and spec.is_gateway:
            endpoint = endpoint or spec.default_api_base
        elif provider_name not in {"custom", "vllm", "ollama"}:
            return {
                "provider": provider_name,
                "status": "not_available",
                "detail": "Pico does not have a safe connection probe for this provider yet.",
            }

        if not endpoint:
            return {
                "provider": provider_name,
                "status": "not_configured",
                "detail": "Add an endpoint before testing this provider.",
            }
        if provider_name not in {"ollama", "vllm"} and not provider.api_key:
            return {
                "provider": provider_name,
                "status": "not_configured",
                "detail": "Add an API key before testing this provider.",
            }

        try:
            status = await asyncio.to_thread(self._probe_models, endpoint, provider.api_key)
        except PermissionError:
            return {
                "provider": provider_name,
                "status": "error",
                "detail": "The provider rejected Pico's credentials.",
            }
        except (OSError, ValueError):
            return {
                "provider": provider_name,
                "status": "error",
                "detail": "Pico could not reach that provider endpoint.",
            }
        return {"provider": provider_name, "status": "ready", "detail": "Connection verified."}

    @staticmethod
    def _probe_models(endpoint: str, api_key: str) -> int:
        url = urljoin(f"{endpoint.rstrip('/')}/", "models")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=5) as response:  # noqa: S310 - endpoint is explicitly user-configured
                return int(response.status)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError from exc
            raise OSError from exc
        except URLError as exc:
            raise OSError from exc
