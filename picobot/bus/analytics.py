"""Analytics and usage tracking for Picobot."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


class Analytics:
    """Track usage statistics for Picobot."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.analytics_file = workspace / ".picobot" / "analytics.json"
        self.analytics_file.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if self.analytics_file.exists():
            try:
                self.data = json.loads(self.analytics_file.read_text("utf-8"))
            except Exception:
                self.data = {"events": [], "daily": {}}
        else:
            self.data = {"events": [], "daily": {}}

    def _save(self) -> None:
        self.analytics_file.write_text(json.dumps(self.data, indent=2), "utf-8")

    def track(
        self,
        event_type: str,
        channel: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track an event."""
        now = datetime.now()
        date_key = now.strftime("%Y-%m-%d")

        event = {
            "type": event_type,
            "timestamp": now.isoformat(),
            "channel": channel,
            "model": model,
            "metadata": metadata or {},
        }
        self.data["events"].append(event)

        if date_key not in self.data["daily"]:
            self.data["daily"][date_key] = {
                "messages": 0,
                "tool_calls": 0,
                "errors": 0,
                "channels": set(),
            }

        daily = self.data["daily"][date_key]
        daily["messages"] += 1
        if event_type == "tool_call":
            daily["tool_calls"] += 1
        if event_type == "error":
            daily["errors"] += 1
        if channel:
            daily["channels"].add(channel)

        self._save()

    def get_stats(self, days: int = 7) -> dict[str, Any]:
        """Get statistics for the last N days."""
        stats = {
            "total_messages": 0,
            "total_tool_calls": 0,
            "total_errors": 0,
            "channels": set(),
            "days": {},
        }

        cutoff = datetime.now() - timedelta(days=days)
        for date_key, daily in self.data.get("daily", {}).items():
            stats["total_messages"] += daily.get("messages", 0)
            stats["total_tool_calls"] += daily.get("tool_calls", 0)
            stats["total_errors"] += daily.get("errors", 0)
            stats["channels"].update(daily.get("channels", set()))
            stats["days"][date_key] = daily

        stats["channels"] = list(stats["channels"])
        return stats

    def get_summary(self) -> str:
        """Get a text summary."""
        stats = self.get_stats()
        lines = [
            "## Usage Statistics (Last 7 days)",
            f"Messages: {stats['total_messages']}",
            f"Tool Calls: {stats['total_tool_calls']}",
            f"Errors: {stats['total_errors']}",
            f"Channels: {', '.join(stats['channels']) or 'none'}",
        ]
        return "\n".join(lines)


def get_analytics(workspace: Path) -> Analytics:
    """Get analytics instance for workspace."""
    return Analytics(workspace)
