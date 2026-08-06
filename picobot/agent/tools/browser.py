"""Read the one browser tab explicitly shared with the current Pico session."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from picobot.agent.tools.base import Tool
from picobot.config.identity import LOCAL_OWNER_ID, web_session_key
from picobot.operations.browser_bridge import BrowserBridgeStore


class BrowserReadSharedTabTool(Tool):
    name = "browser_read_shared_tab"
    description = "Read the latest redacted visible-text snapshot from the one tab the owner explicitly shared with this session."
    parameters = {
        "type": "object",
        "properties": {"max_characters": {"type": "integer", "minimum": 500, "maximum": 12000}},
    }

    def __init__(self, workspace: Path):
        self.store = BrowserBridgeStore(workspace)
        self._owner_id: str | None = None
        self._session_key: str | None = None

    def set_context(self, channel: str, chat_id: str, *_: Any) -> None:
        if channel != "web":
            self._owner_id = self._session_key = None
            return
        parts = chat_id.split(":", 2)
        if len(parts) != 3 or parts[0] != "web":
            self._owner_id = self._session_key = None
            return
        self._owner_id = LOCAL_OWNER_ID
        self._session_key = web_session_key(parts[2])

    async def execute(self, max_characters: int | None = None, **_: Any) -> str:
        if not self._owner_id or not self._session_key:
            return "Error: Browser reading is available only from Pico's local web workbench."
        try:
            tab = self.store.get(self._owner_id, self._session_key)
        except KeyError as exc:
            return f"Error: {exc}"
        limit = max_characters or 6000
        text = tab.snapshot_text[:limit]
        suffix = "\n\n[Snapshot was truncated.]" if tab.snapshot_truncated or len(tab.snapshot_text) > limit else ""
        return f"Shared tab: {tab.title}\nURL: {tab.url}\n\nVisible text:\n{text}{suffix}"
