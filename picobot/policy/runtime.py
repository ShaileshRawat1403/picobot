"""Durable runtime and session policy.

The global policy is a non-secret record persisted in the active profile's
``config.json``. Each browser session can carry an explicit override in
``Session.metadata``. Before every new run the effective policy is resolved
server-side: global first, session override second, then the process defaults
for anything left unset.

Selection is validated against the real provider setup on write, so an
unknown or unready provider/model pair can never be selected. Resolution
re-validates defensively and falls back to the process defaults if the stored
policy has become unservable (for example a key removed after the policy was
saved).

Values are deliberately metadata-only. No provider credential is ever read,
returned, or written by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from picobot.config.loader import get_config_path, load_config, save_config
from picobot.config.schema import ProvidersConfig, RuntimePolicyConfig

SESSION_POLICY_KEY = "pico_runtime_policy"

_REASONING_EFFORT_VALUES = ("low", "medium", "high")
_RESPONSE_MODES = ("default", "concise", "detailed")
_MAX_MODEL_LENGTH = 240

# Subscription CLI connections do not expose provider-specific reasoning
# controls to Pico. API transports may forward reasoning_effort normally;
# subscription requests preserve the requested value but report it as
# unsupported rather than pretending the official CLI accepted it.
_REASONING_EFFORT_UNSUPPORTED = frozenset({"openai_codex", "gemini_oauth"})

_DEFAULTS: dict[str, Any] = {
    "provider": None,
    "model": None,
    "reasoning_effort": None,
    "response_mode": "default",
}


def _known_providers() -> frozenset[str]:
    return frozenset(ProvidersConfig.model_fields)


def default_policy() -> RuntimePolicyConfig:
    """An empty policy; resolution then uses the process defaults entirely."""
    return RuntimePolicyConfig()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_policy(policy: RuntimePolicyConfig) -> dict[str, Any]:
    return {
        "provider": policy.provider,
        "model": policy.model,
        "reasoningEffort": policy.reasoning_effort,
        "responseMode": policy.response_mode,
        "version": policy.version,
        "updatedAt": policy.updated_at,
    }


def _public_override(override: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(override, dict):
        return {key: _DEFAULTS[key] for key in ("provider", "model", "reasoning_effort", "response_mode")}
    return {
        "provider": override.get("provider"),
        "model": override.get("model"),
        "reasoningEffort": override.get("reasoning_effort"),
        "responseMode": override.get("response_mode", "default"),
        "version": override.get("version", 0),
        "updatedAt": override.get("updated_at"),
    }


def _normalize_fields(
    fields: dict[str, Any],
    *,
    provider: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Validate one policy field set and return it normalized.

    ``provider`` is an optional readiness callback (registry name -> None,
    raising ValueError) applied when a provider is named.
    """
    known = _known_providers()
    for key, value in fields.items():
        if key == "provider":
            if value is None:
                continue
            if not isinstance(value, str) or value not in known:
                raise ValueError(f"Unknown Pico provider: {value!r}")
            if provider is not None:
                provider(value)
        elif key == "model":
            if value is None:
                continue
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > _MAX_MODEL_LENGTH
            ):
                raise ValueError(
                    f"Model must contain between 1 and {_MAX_MODEL_LENGTH} characters"
                )
            fields[key] = value.strip()
        elif key == "reasoning_effort":
            if value is None:
                continue
            if value not in _REASONING_EFFORT_VALUES:
                raise ValueError(
                    f"Reasoning effort must be one of: {', '.join(_REASONING_EFFORT_VALUES)}"
                )
        elif key == "response_mode":
            if value not in _RESPONSE_MODES:
                raise ValueError(
                    f"Response mode must be one of: {', '.join(_RESPONSE_MODES)}"
                )
    return fields


def _apply_payload(current: dict[str, Any], payload: dict[str, Any], *, clear: bool) -> tuple[dict[str, Any], bool]:
    """Merge one PUT body into an existing field set.

    Keys present in the payload are applied; an explicit ``null`` resets that
    field to its default. ``clear: true`` resets every field. Returns the new
    field set and whether anything changed.
    """
    fields = dict(_DEFAULTS) if clear else dict(current)
    changed = bool(clear)
    for key in ("provider", "model", "reasoning_effort", "response_mode"):
        if key not in payload:
            continue
        value = payload[key]
        fields[key] = _DEFAULTS[key] if value is None else value
        changed = changed or fields[key] != current.get(key)
    return fields, changed


def reasoning_effort_supported(provider_name: str | None) -> bool:
    """True when the effective provider forwards reasoning effort upstream."""
    if not provider_name:
        return False
    return provider_name not in _REASONING_EFFORT_UNSUPPORTED


@dataclass(frozen=True)
class EffectivePolicy:
    """The fully resolved policy for one upcoming turn.

    ``provider``/``model`` are never None after resolution — they fall back to
    the process defaults. ``source`` says whether anything beyond the process
    default contributed a value: "default" (nothing set), "global", or
    "session". ``policy_valid`` is False when a stored selection could no
    longer be served; the fallback values are then used.
    """

    provider: str
    model: str
    reasoning_effort: str | None
    response_mode: str
    version: int
    source: str = "default"
    reasoning_effort_requested: str | None = None
    reasoning_effort_supported: bool = True
    reasoning_effort_applied: bool = True
    policy_valid: bool = True
    reason: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "reasoningEffortRequested": self.reasoning_effort_requested,
            "reasoningEffortSupported": self.reasoning_effort_supported,
            "reasoningEffortApplied": self.reasoning_effort_applied,
            "responseMode": self.response_mode,
            "version": self.version,
            "source": self.source,
            "valid": self.policy_valid,
            "reason": self.reason,
        }


def resolve_effective_policy(
    global_policy: RuntimePolicyConfig,
    session_metadata: dict[str, Any],
    *,
    fallback_model: str,
    fallback_provider: str,
    validate: Callable[[str | None, str], None] | None = None,
) -> EffectivePolicy:
    """Merge global policy, session override, and process defaults.

    ``validate`` is an optional callback ``(provider, model) -> None`` used to
    re-check availability against the live profile. When it raises, the
    resolved selection falls back to the process defaults and ``policy_valid``
    is set False so the caller can report the capability truthfully.
    """
    override = session_metadata.get(SESSION_POLICY_KEY)
    override = override if isinstance(override, dict) else {}

    merged = dict(_DEFAULTS)
    source = "default"
    version = global_policy.version

    for key in ("provider", "model", "reasoning_effort", "response_mode"):
        value = override.get(key)
        default = _DEFAULTS[key]
        if value is not None and value != default:
            merged[key] = value
            source = "session"
        elif value is not None:
            merged[key] = value
        if merged[key] == default:
            global_value = getattr(global_policy, key)
            if global_value is not None and global_value != default:
                merged[key] = global_value
                if source == "default":
                    source = "global"

    override_version = override.get("version")
    if isinstance(override_version, int):
        version = override_version

    provider = merged["provider"] or fallback_provider
    model = merged["model"] or fallback_model
    effort_requested = merged["reasoning_effort"]
    mode = merged["response_mode"] or "default"

    reason: str | None = None
    valid = True
    if validate is not None:
        try:
            validate(merged["provider"], merged["model"] or fallback_model)
        except ValueError as exc:
            valid = False
            reason = str(exc)
            provider = fallback_provider
            model = fallback_model

    supported = reasoning_effort_supported(provider)
    if effort_requested is not None and not supported:
        applied_effort: str | None = None
    else:
        applied_effort = effort_requested

    return EffectivePolicy(
        provider=provider,
        model=model,
        reasoning_effort=applied_effort,
        response_mode=mode,
        version=version,
        source=source,
        reasoning_effort_requested=effort_requested,
        reasoning_effort_supported=supported,
        reasoning_effort_applied=applied_effort == effort_requested and effort_requested is not None,
        policy_valid=valid,
        reason=reason,
    )


class RuntimePolicyService:
    """Read/write the durable global policy and per-session overrides.

    Public return values are metadata-only. ``config_path`` defaults to the
    active profile's config file (``get_config_path``), matching the existing
    provider setup service.
    """

    def __init__(self, config_path=None):
        self.config_path = config_path or get_config_path()

    def _load(self):
        return load_config(self.config_path)

    def _ready_provider(self, provider: str) -> None:
        from picobot.providers.setup import ProviderSetupService

        entry = ProviderSetupService(self.config_path)._entry(provider, self._load())
        if entry["status"] not in {"configured", "ready"}:
            raise ValueError(
                f"Provider {provider!r} is not ready to serve turns ({entry['status']}). "
                "Configure it before selecting it in the runtime policy."
            )

    def _validate_selection(self, provider: str | None, model: str, config) -> None:
        """Reject unknown, unready, or incoherent provider/model selections."""
        if provider is not None:
            self._ready_provider(provider)
        routed = config.get_provider_name(model)
        if routed is None:
            raise ValueError(f"No configured provider can serve model {model!r}.")
        if provider is not None and routed != provider:
            raise ValueError(
                f"Model {model!r} routes to provider {routed!r}, not {provider!r}."
            )

    def _selection_validator(self, config):
        def _validate(provider: str | None, model: str) -> None:
            self._validate_selection(provider, model, config)

        return _validate

    def get_global(self) -> RuntimePolicyConfig:
        return self._load().policy

    def resolve_effective(
        self,
        session_metadata: dict[str, Any],
        *,
        fallback_model: str | None = None,
        fallback_provider: str | None = None,
    ) -> EffectivePolicy:
        config = self._load()
        if fallback_model is None:
            fallback_model = config.agents.defaults.model
        if fallback_provider is None:
            fallback_provider = (
                config.agents.defaults.provider
                or config.get_provider_name(fallback_model)
                or fallback_model
            )
        return resolve_effective_policy(
            config.policy,
            session_metadata,
            fallback_model=fallback_model,
            fallback_provider=fallback_provider,
            validate=self._selection_validator(config),
        )

    def _set_global_fields(self, fields: dict[str, Any]) -> dict[str, Any]:
        config = self._load()
        normalized = _normalize_fields(dict(fields))
        if normalized["provider"] is not None or normalized["model"] is not None:
            model = normalized["model"] or config.agents.defaults.model
            self._validate_selection(normalized["provider"], model, config)
        version = config.policy.version + 1
        config.policy = RuntimePolicyConfig(
            provider=normalized["provider"],
            model=normalized["model"],
            reasoning_effort=normalized["reasoning_effort"],
            response_mode=normalized["response_mode"],
            version=version,
            updated_at=_now(),
        )
        save_config(config, self.config_path)
        return _public_policy(config.policy)

    def set_global(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_global()
        current_fields = {
            "provider": current.provider,
            "model": current.model,
            "reasoning_effort": current.reasoning_effort,
            "response_mode": current.response_mode,
        }
        fields, changed = _apply_payload(current_fields, payload, clear=payload.get("clear") is True)
        if changed:
            self._set_global_fields(fields)
        return self.summary()

    def set_session_override(self, session, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("clear") is True:
            session.metadata.pop(SESSION_POLICY_KEY, None)
            return self.session_summary(session)
        raw = session.metadata.get(SESSION_POLICY_KEY)
        current = {**_DEFAULTS, **(raw if isinstance(raw, dict) else {})}
        fields, changed = _apply_payload(dict(current), payload, clear=False)
        normalized = _normalize_fields(dict(fields))
        if changed:
            if normalized["provider"] is not None or normalized["model"] is not None:
                config = self._load()
                model = normalized["model"] or config.agents.defaults.model
                self._validate_selection(normalized["provider"], model, config)
            version = int(current.get("version") or 0) + 1
            session.metadata[SESSION_POLICY_KEY] = {
                **normalized,
                "version": version,
                "updated_at": _now(),
            }
        return self.session_summary(session)

    def summary(self) -> dict[str, Any]:
        config = self._load()
        effective = self.resolve_effective({})
        from picobot.providers.setup import ProviderSetupService

        providers = ProviderSetupService(self.config_path).inventory()["providers"]
        return {
            "policy": _public_policy(config.policy),
            "effective": effective.to_public(),
            "providers": providers,
        }

    def session_summary(self, session) -> dict[str, Any]:
        config = self._load()
        effective = self.resolve_effective(session.metadata)
        return {
            "policy": _public_policy(config.policy),
            "override": _public_override(session.metadata.get(SESSION_POLICY_KEY)),
            "effective": effective.to_public(),
        }
