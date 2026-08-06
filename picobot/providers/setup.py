"""Local-first provider setup without exposing credentials to the web client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from picobot.config.loader import (
    clear_profile_provider_secret,
    get_config_path,
    load_config,
    save_config,
    set_profile_provider_secret,
)
from picobot.config.schema import ProvidersConfig
from picobot.providers.connections import (
    SUPPORTED_PROVIDER_NAMES,
    SUPPORTED_SUBSCRIPTION_PROVIDERS,
    ProviderConnection,
    is_supported_provider,
    subscription_login_hint,
    subscription_status,
)
from picobot.providers.registry import find_by_name


class ProviderSetupService:
    """Manage the active local Pico profile's provider configuration.

    Public return values are deliberately metadata-only. API keys are accepted
    to write local profile storage and are never returned, logged, or rendered.
    """

    _last_diagnostics: dict[tuple[str, str], dict[str, Any]] = {}

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or get_config_path()

    def _diagnostic_key(self, provider_name: str) -> tuple[str, str]:
        return str(self.config_path), provider_name

    def _clear_diagnostic(self, provider_name: str) -> None:
        """Discard a probe result that belonged to prior provider settings."""
        self._last_diagnostics.pop(self._diagnostic_key(provider_name), None)

    @staticmethod
    def _provider_names() -> tuple[str, ...]:
        return SUPPORTED_PROVIDER_NAMES

    @staticmethod
    def _known_provider_names() -> tuple[str, ...]:
        return tuple(ProvidersConfig.model_fields)

    @classmethod
    def _validate_provider_name(cls, provider_name: str) -> str:
        if not isinstance(provider_name, str) or provider_name not in cls._known_provider_names():
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

    def _default_model(self, provider_name: str, config) -> str | None:
        if config.agents.defaults.provider == provider_name and config.agents.defaults.model:
            return config.agents.defaults.model
        spec = find_by_name(provider_name)
        if spec and spec.keywords:
            return spec.keywords[0]
        return None

    def _entry(self, provider_name: str, config) -> dict:
        provider = getattr(config.providers, provider_name)
        if not is_supported_provider(provider_name):
            lifecycle = ProviderConnection(
                provider_name,
                "deferred",
                "deferred",
                "This provider is outside Pico's supported provider boundary.",
                failure_category="unsupported",
            )
            return {
                "id": provider_name,
                "label": self._label(provider_name),
                "kind": "deferred",
                "status": "deferred",
                "detail": "This provider is outside Pico's supported provider boundary.",
                "setup_hint": "Choose one of Pico's supported API or subscription connections.",
                "default_model": None,
                "has_api_key": bool(provider.api_key),
                "has_endpoint": bool(provider.api_base),
                "is_active": False,
                "last_diagnostic": None,
                "failure_category": "unsupported",
                "connection": lifecycle.to_dict(),
            }
        kind = self._kind(provider_name)
        has_key = bool(provider.api_key)
        has_endpoint = bool(provider.api_base)
        auth_metadata: dict[str, Any] = {}
        if provider_name in SUPPORTED_SUBSCRIPTION_PROVIDERS:
            auth_metadata = subscription_status(provider_name)
            status = auth_metadata["status"]
            detail = auth_metadata["detail"]
        elif kind == "oauth":
            status = "deferred"
            detail = "This subscription connection is outside Pico's supported boundary."
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

        key_pair = self._diagnostic_key(provider_name)
        diag = self._last_diagnostics.get(key_pair)
        failure_category = diag.get("failure_category") if diag else None
        if diag and diag.get("status") == "ready":
            status = "ready"
            detail = diag.get("detail", "Connection verified.")
        elif diag and diag.get("status") == "error":
            status = "unavailable"
            detail = diag.get("detail", "Provider connection failed.")

        lifecycle = ProviderConnection(
            provider_name,
            kind,
            status,
            detail,
            failure_category=failure_category,
            cli_available=auth_metadata.get("cli_available") if auth_metadata else None,
        )

        return {
            "id": provider_name,
            "label": self._label(provider_name),
            "kind": kind,
            "status": status,
            "detail": detail,
            "setup_hint": detail,
            "default_model": self._default_model(provider_name, config),
            "has_api_key": has_key,
            "has_endpoint": has_endpoint,
            "is_active": config.agents.defaults.provider == provider_name,
            "last_diagnostic": diag,
            "failure_category": failure_category,
            "login_hint": subscription_login_hint(provider_name)
            if provider_name in SUPPORTED_SUBSCRIPTION_PROVIDERS
            else None,
            "cli_available": auth_metadata.get("cli_available")
            if auth_metadata
            else None,
            "connection": lifecycle.to_dict(),
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
        if not is_supported_provider(provider_name):
            raise ValueError("This provider is outside Pico's supported provider boundary")
        if self._kind(provider_name) in {"oauth", "local"}:
            raise ValueError("This provider does not accept an API key in Pico's web workbench")
        if not isinstance(api_key, str):
            raise ValueError("Provider API key is required")
        set_profile_provider_secret(provider_name, api_key, self.config_path)
        self._clear_diagnostic(provider_name)
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
        self._clear_diagnostic("custom")
        return self.inventory()

    def disconnect(self, provider_name: str) -> dict:
        """Disconnect a local API connection without exposing secret material."""
        provider_name = self._validate_provider_name(provider_name)
        if not is_supported_provider(provider_name):
            raise ValueError("This provider is outside Pico's supported provider boundary")
        if provider_name in SUPPORTED_SUBSCRIPTION_PROVIDERS:
            # Pico never owns subscription credentials. The provider's official
            # CLI remains the only place where logout/revocation can occur.
            result = self._entry(provider_name, load_config(self.config_path))
            result["detail"] = "Disconnect through the official provider CLI; Pico stores no subscription token."
            result["setup_hint"] = result["detail"]
            return result
        clear_profile_provider_secret(provider_name, self.config_path)
        config = load_config(self.config_path)
        provider = getattr(config.providers, provider_name)
        provider.api_key = ""
        if provider_name in {"custom", "ollama"}:
            provider.api_base = ""
        if config.agents.defaults.provider == provider_name:
            config.agents.defaults.provider = "openai"
            config.agents.defaults.model = "gpt-4o-mini"
        save_config(config, self.config_path)
        self._clear_diagnostic(provider_name)
        return self._entry(provider_name, config)

    def choose_default(self, provider_name: str, model: str) -> dict:
        provider_name = self._validate_provider_name(provider_name)
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 240:
            raise ValueError("Model must contain between 1 and 240 characters")
        config = load_config(self.config_path)
        entry = self._entry(provider_name, config)
        if entry["status"] == "deferred":
            raise ValueError("This provider is outside Pico's supported provider boundary")
        if entry["status"] not in {"configured", "ready"}:
            raise ValueError("Configure this provider before making it the default")
        config.agents.defaults.provider = provider_name
        config.agents.defaults.model = model.strip()
        save_config(config, self.config_path)
        return self.inventory()

    async def list_models(self, provider_name: str) -> dict[str, Any]:
        """Return the account-visible model identifiers for one safe provider.

        Model discovery is intentionally narrow: Pico asks OpenAI's documented
        ``/v1/models`` endpoint only when the owner opens the picker.  The
        credential stays in the local profile and neither request headers nor
        response bodies beyond model ids are retained or logged.
        """
        provider_name = self._validate_provider_name(provider_name)
        if provider_name != "openai":
            raise ValueError("Live model discovery is currently available for OpenAI API only")
        config = load_config(self.config_path)
        entry = self._entry(provider_name, config)
        if entry["status"] not in {"configured", "ready"}:
            raise ValueError("Configure the OpenAI API key before listing available models")
        api_key = config.providers.openai.api_key
        if not api_key:
            raise ValueError("Configure the OpenAI API key before listing available models")

        def fetch() -> list[str]:
            request = Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                method="GET",
            )
            try:
                with urlopen(request, timeout=10) as response:
                    raw = response.read(2_000_000)
            except HTTPError as exc:
                if exc.code in {401, 403}:
                    raise ValueError("OpenAI rejected the model catalog request") from None
                raise ValueError("OpenAI model catalog is unavailable right now") from None
            except (URLError, OSError):
                raise ValueError("Pico could not reach the OpenAI model catalog") from None
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError("OpenAI returned an invalid model catalog") from None
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("OpenAI returned an invalid model catalog")
            ids = {
                item.get("id").strip()
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].strip()
                and len(item["id"].strip()) <= 240
            }
            return sorted(ids, key=str.casefold)

        return {"provider": "openai", "models": await asyncio.to_thread(fetch)}

    async def test_connection(self, provider_name: str) -> dict:
        """Run a bounded, read-only readiness check where valid without modifying configuration."""
        provider_name = self._validate_provider_name(provider_name)
        config = load_config(self.config_path)
        provider = getattr(config.providers, provider_name)
        spec = find_by_name(provider_name)

        key_pair = self._diagnostic_key(provider_name)

        if not is_supported_provider(provider_name):
            res = {
                "provider": provider_name,
                "status": "not_available",
                "failure_category": "unsupported",
                "detail": "This provider is outside Pico's supported provider boundary.",
            }
            self._last_diagnostics[key_pair] = res
            return res

        if provider_name in SUPPORTED_SUBSCRIPTION_PROVIDERS:
            res = {"provider": provider_name, **subscription_status(provider_name)}
            self._last_diagnostics[key_pair] = res
            return res

        if self._kind(provider_name) == "oauth":
            res = {
                "provider": provider_name,
                "status": "not_available",
                "failure_category": "unsupported",
                "detail": "This subscription connection is outside Pico's supported boundary.",
            }
            self._last_diagnostics[key_pair] = res
            return res

        endpoint = provider.api_base
        if provider_name == "openai":
            endpoint = endpoint or "https://api.openai.com/v1"
        elif provider_name == "deepseek":
            endpoint = endpoint or "https://api.deepseek.com/v1"
        elif provider_name == "groq":
            endpoint = endpoint or "https://api.groq.com/openai/v1"
        elif spec and spec.is_gateway:
            endpoint = endpoint or spec.default_api_base
        elif provider_name not in {"custom", "vllm", "ollama"}:
            res = {
                "provider": provider_name,
                "status": "not_available",
                "failure_category": "unsupported",
                "detail": "Pico does not have a safe connection probe for this provider yet.",
            }
            self._last_diagnostics[key_pair] = res
            return res

        if not endpoint:
            res = {
                "provider": provider_name,
                "status": "not_configured",
                "failure_category": "endpoint_missing",
                "detail": "Add an endpoint before testing this provider.",
            }
            self._last_diagnostics[key_pair] = res
            return res
        if provider_name not in {"ollama", "vllm"} and not provider.api_key:
            res = {
                "provider": provider_name,
                "status": "not_configured",
                "failure_category": "key_missing",
                "detail": "Add an API key before testing this provider.",
            }
            self._last_diagnostics[key_pair] = res
            return res

        try:
            status_code = await asyncio.to_thread(self._probe_models, endpoint, provider.api_key)
            if status_code in {200, 201, 202, 204}:
                res = {
                    "provider": provider_name,
                    "status": "ready",
                    "failure_category": "connection_verified",
                    "detail": "Connection verified.",
                }
            else:
                res = {
                    "provider": provider_name,
                    "status": "error",
                    "failure_category": "unreachable",
                    "detail": "Pico could not reach that provider endpoint.",
                }
        except PermissionError:
            res = {
                "provider": provider_name,
                "status": "error",
                "failure_category": "auth_failed",
                "detail": "The provider rejected Pico's credentials.",
            }
        except (OSError, ValueError):
            res = {
                "provider": provider_name,
                "status": "error",
                "failure_category": "unreachable",
                "detail": "Pico could not reach that provider endpoint.",
            }

        self._last_diagnostics[key_pair] = res
        return res

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
