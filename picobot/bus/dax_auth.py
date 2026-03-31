"""Shared DAX authorization utilities."""

import os
from typing import Sequence


ADMIN_NUMBERS_ENV = "DAX_ADMIN_NUMBERS"


def get_admin_numbers(config_numbers: Sequence[str] | None = None) -> list[str]:
    """Get list of authorized phone numbers.
    
    Priority:
    1. Config file (config_numbers)
    2. Environment variable (DAX_ADMIN_NUMBERS)
    """
    if config_numbers:
        return [n.strip() for n in config_numbers if n.strip()]
    
    env_numbers = os.environ.get(ADMIN_NUMBERS_ENV, "")
    if env_numbers:
        return [n.strip() for n in env_numbers.split(",") if n.strip()]
    
    return []


def is_authorized(chat_id: str, config_numbers: Sequence[str] | None = None) -> bool:
    """Check if sender/chat is authorized to use DAX workflows.
    
    Only admin numbers from config or environment variable can trigger DAX workflows.
    This prevents accidental message leakage.
    """
    if not chat_id:
        return False
    
    authorized = get_admin_numbers(config_numbers)
    return chat_id in authorized


def get_default_url(config_url: str | None = None) -> str:
    """Get DAX URL with fallback to environment variable or default."""
    if config_url:
        return config_url.rstrip("/")
    
    env_url = os.environ.get("DAX_URL", "")
    if env_url:
        return env_url.rstrip("/")
    
    return "http://localhost:4096"
