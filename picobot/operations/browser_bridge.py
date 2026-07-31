"""Owner-paired, read-only records for Pico's local Chrome bridge.

The extension never grants Pico a browser profile. It submits a snapshot only
from the tab an owner explicitly shares, using a short-lived pairing code and a
per-share bearer token. Tokens are stored hashed and never returned to Pico's
web workbench.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class SharedBrowserTab:
    id: str
    owner_id: str
    session_key: str
    extension_tab_id: int
    url: str
    title: str
    domain: str
    snapshot_text: str
    snapshot_truncated: bool
    status: str
    shared_at: str
    updated_at: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("snapshot_text")
        return data


class BrowserBridgeStore:
    """Persist one explicitly shared tab per owner session."""

    _PAIRING_LIFETIME = timedelta(minutes=5)
    _MAX_TITLE = 240
    _MAX_SNAPSHOT = 12_000
    _SENSITIVE_PATH_RE = re.compile(
        r"(?:login|sign[ -]?in|auth|password|recovery|payment|checkout|billing)", re.IGNORECASE
    )
    _SECRET_RE = re.compile(
        r"(?i)\b(?:password|passcode|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|secret)\b"
        r"\s*[:=]\s*[^\s,;]+|\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b|\bAIza[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9_]{20,}\b"
    )

    def __init__(self, workspace: Path):
        root = workspace / "operations"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-browser-bridge.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS browser_pairing_tickets (
                    code_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS shared_browser_tabs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    extension_tab_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    snapshot_text TEXT NOT NULL,
                    snapshot_truncated INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    shared_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS shared_browser_tabs_active_owner_session_idx
                    ON shared_browser_tabs(owner_id, session_key) WHERE status = 'active';
                """
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _required(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Browser bridge {label} is required")
        return value.strip()

    @classmethod
    def _page(cls, url: object, title: object, text: object) -> tuple[str, str, str, str, bool]:
        clean_url = cls._required(url, "URL")
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only http/https browser tabs can be shared")
        if cls._SENSITIVE_PATH_RE.search(parsed.path):
            raise ValueError("Sensitive authentication or payment pages cannot be shared with Pico")
        clean_title = " ".join(cls._required(title, "title").split())[: cls._MAX_TITLE]
        if not isinstance(text, str):
            raise ValueError("Browser bridge visible text is required")
        clean_text = cls._SECRET_RE.sub("[redacted]", text)
        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
        truncated = len(clean_text) > cls._MAX_SNAPSHOT
        return clean_url, clean_title, parsed.hostname.lower(), clean_text[: cls._MAX_SNAPSHOT], truncated

    @staticmethod
    def _tab(row: sqlite3.Row) -> SharedBrowserTab:
        data = dict(row)
        data["snapshot_truncated"] = bool(data["snapshot_truncated"])
        data.pop("token_hash")
        return SharedBrowserTab(**data)

    def create_ticket(self, owner_id: str, session_key: str) -> str:
        self._required(owner_id, "owner")
        self._required(session_key, "session")
        code = secrets.token_urlsafe(18)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO browser_pairing_tickets(code_hash, owner_id, session_key, expires_at, used_at) VALUES (?, ?, ?, ?, NULL)",
                (self._hash(code), owner_id, session_key, self._iso(self._now() + self._PAIRING_LIFETIME)),
            )
        return code

    def share(self, code: object, extension_tab_id: object, url: object, title: object, text: object) -> tuple[SharedBrowserTab, str]:
        clean_code = self._required(code, "pairing code")
        if not isinstance(extension_tab_id, int) or isinstance(extension_tab_id, bool) or extension_tab_id < 0:
            raise ValueError("Browser bridge tab id is invalid")
        clean_url, clean_title, domain, clean_text, truncated = self._page(url, title, text)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM browser_pairing_tickets WHERE code_hash = ?", (self._hash(clean_code),)
            ).fetchone()
            if row is None or row["used_at"] is not None or datetime.fromisoformat(row["expires_at"]) <= self._now():
                raise ValueError("Browser pairing code is invalid or expired")
            now = self._iso(self._now())
            ticket_cursor = connection.execute(
                "UPDATE browser_pairing_tickets SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                (now, self._hash(clean_code)),
            )
            if ticket_cursor.rowcount != 1:
                raise ValueError("Browser pairing code is invalid or expired")
            connection.execute(
                "UPDATE shared_browser_tabs SET status = 'revoked', updated_at = ? WHERE owner_id = ? AND session_key = ? AND status = 'active'",
                (now, row["owner_id"], row["session_key"]),
            )
            bridge_id, bridge_token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO shared_browser_tabs(
                    id, owner_id, session_key, extension_tab_id, token_hash, url, title, domain,
                    snapshot_text, snapshot_truncated, status, shared_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (bridge_id, row["owner_id"], row["session_key"], extension_tab_id, self._hash(bridge_token), clean_url, clean_title, domain, clean_text, int(truncated), now, now),
            )
        return self.get(row["owner_id"], row["session_key"]), bridge_token

    def get(self, owner_id: str, session_key: str) -> SharedBrowserTab:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM shared_browser_tabs WHERE owner_id = ? AND session_key = ? AND status = 'active'",
                (owner_id, session_key),
            ).fetchone()
        if row is None:
            raise KeyError("No browser tab is shared with this Pico session")
        return self._tab(row)

    def refresh(self, bridge_id: object, bridge_token: object, extension_tab_id: object, url: object, title: object, text: object) -> SharedBrowserTab:
        clean_id = self._required(bridge_id, "share id")
        clean_token = self._required(bridge_token, "share token")
        if not isinstance(extension_tab_id, int) or isinstance(extension_tab_id, bool) or extension_tab_id < 0:
            raise ValueError("Browser bridge tab id is invalid")
        clean_url, clean_title, domain, clean_text, truncated = self._page(url, title, text)
        now = self._iso(self._now())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE shared_browser_tabs
                SET url = ?, title = ?, domain = ?, snapshot_text = ?, snapshot_truncated = ?, updated_at = ?
                WHERE id = ? AND token_hash = ? AND extension_tab_id = ? AND status = 'active'
                """,
                (clean_url, clean_title, domain, clean_text, int(truncated), now, clean_id, self._hash(clean_token), extension_tab_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("This browser tab is no longer shared with Pico")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shared_browser_tabs WHERE id = ?", (clean_id,)).fetchone()
        return self._tab(row)

    def revoke(self, owner_id: str, session_key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE shared_browser_tabs SET status = 'revoked', updated_at = ? WHERE owner_id = ? AND session_key = ? AND status = 'active'",
                (self._iso(self._now()), owner_id, session_key),
            )
