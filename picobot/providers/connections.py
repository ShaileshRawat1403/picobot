"""Provider connection boundaries and provider-owned subscription checks.

This module deliberately does not read token contents. Subscription providers
are authenticated by their official local CLIs, and Pico only observes a
bounded readiness result from those CLIs (or the presence of the CLI-managed
credential file for Gemini, whose CLI has no non-interactive status command).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

SUPPORTED_API_PROVIDERS: tuple[str, ...] = (
    "openai",
    "gemini",
    "anthropic",
    "ollama",
    "custom",
)
SUPPORTED_SUBSCRIPTION_PROVIDERS: tuple[str, ...] = (
    "openai_codex",
    "gemini_oauth",
)
SUPPORTED_PROVIDER_NAMES: tuple[str, ...] = (
    *SUPPORTED_API_PROVIDERS,
    *SUPPORTED_SUBSCRIPTION_PROVIDERS,
)

_GEMINI_CREDENTIAL_PATHS = (
    Path.home() / ".gemini" / "oauth_creds.json",
    Path.home() / ".gemini" / "google_accounts.json",
)


def is_supported_provider(provider_name: str) -> bool:
    return provider_name in SUPPORTED_PROVIDER_NAMES


def subscription_login_hint(provider_name: str) -> str:
    if provider_name == "openai_codex":
        return "Run `codex login` to connect your ChatGPT/Codex subscription."
    if provider_name == "gemini_oauth":
        return "Run `gemini` and choose Sign in with Google in the official CLI."
    return "Use the provider's official sign-in flow."


def _codex_status() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {
            "status": "setup_required",
            "failure_category": "cli_missing",
            "detail": "Install the official Codex CLI, then run `codex login`.",
            "cli_available": False,
        }

    try:
        result = subprocess.run(
            [executable, "login", "status"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "setup_required",
            "failure_category": "cli_unavailable",
            "detail": "Pico could not verify Codex CLI login status.",
            "cli_available": True,
        }

    if result.returncode == 0:
        return {
            "status": "ready",
            "failure_category": "subscription_verified",
            "detail": "Codex CLI is logged in with the provider-owned subscription flow.",
            "cli_available": True,
        }
    return {
        "status": "setup_required",
        "failure_category": "login_required",
        "detail": "Run `codex login` to connect your ChatGPT/Codex subscription.",
        "cli_available": True,
    }


def _gemini_status() -> dict[str, Any]:
    executable = shutil.which("gemini")
    if not executable:
        return {
            "status": "setup_required",
            "failure_category": "cli_missing",
            "detail": "Install the official Gemini CLI, then sign in with Google.",
            "cli_available": False,
        }

    # Do not open or parse these files: they are owned by Gemini CLI and may
    # contain refresh/access credentials. Presence is enough for a safe local
    # setup hint; the CLI remains the only component that uses their contents.
    if any(path.is_file() for path in _GEMINI_CREDENTIAL_PATHS):
        return {
            "status": "configured",
            "failure_category": "cli_credentials_detected",
            "detail": "Gemini CLI credentials detected; Pico will use the official CLI transport.",
            "cli_available": True,
        }
    return {
        "status": "setup_required",
        "failure_category": "login_required",
        "detail": "Run `gemini` and choose Sign in with Google in the official CLI.",
        "cli_available": True,
    }


def subscription_status(provider_name: str) -> dict[str, Any]:
    """Return redacted readiness metadata for a supported subscription."""
    if provider_name == "openai_codex":
        return _codex_status()
    if provider_name == "gemini_oauth":
        return _gemini_status()
    raise ValueError(f"Unsupported subscription provider: {provider_name}")

