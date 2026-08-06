"""Analytics and usage tracking for Picobot."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any



class Analytics:
    """Track usage statistics for Picobot."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.analytics_file = workspace / ".picobot" / "analytics.json"
        self.analytics_file.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {"events": [], "daily": {}}

    @staticmethod
    def _count(value: object) -> int:
        """Keep persisted counters safe when older files contain bad values."""
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @classmethod
    def _daily_record(cls, value: object) -> dict[str, Any]:
        """Restore JSON channel arrays to the in-memory set used by ``track``."""
        raw = value if isinstance(value, dict) else {}
        raw_channels = raw.get("channels", [])
        if isinstance(raw_channels, str):
            raw_channels = [raw_channels]
        channels = {
            channel.strip()
            for channel in raw_channels
            if isinstance(channel, str) and channel.strip()
        } if isinstance(raw_channels, (list, tuple, set)) else set()
        return {
            "messages": cls._count(raw.get("messages")),
            "tool_calls": cls._count(raw.get("tool_calls")),
            "errors": cls._count(raw.get("errors")),
            "channels": channels,
        }

    @classmethod
    def _normalize_data(cls, value: object) -> dict[str, Any]:
        """Normalize the JSON-on-disk format into Pico's runtime data shape."""
        raw = value if isinstance(value, dict) else {}
        events = raw.get("events")
        daily = raw.get("daily")
        return {
            "events": events if isinstance(events, list) else [],
            "daily": {
                str(date_key): cls._daily_record(record)
                for date_key, record in daily.items()
            } if isinstance(daily, dict) else {},
        }

    def _load(self) -> None:
        if self.analytics_file.exists():
            try:
                self.data = self._normalize_data(json.loads(self.analytics_file.read_text("utf-8")))
            except Exception:
                self.data = self._empty_data()
        else:
            self.data = self._empty_data()

    def _save(self) -> None:
        data_to_save = {
            "events": self.data["events"],
            "daily": {},
        }
        for date_key, daily in self.data.get("daily", {}).items():
            data_to_save["daily"][date_key] = {
                "messages": daily.get("messages", 0),
                "tool_calls": daily.get("tool_calls", 0),
                "errors": daily.get("errors", 0),
                "channels": sorted(daily.get("channels", set())),
            }
        self.analytics_file.write_text(json.dumps(data_to_save, indent=2), "utf-8")

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
            try:
                in_window = datetime.strptime(date_key, "%Y-%m-%d") >= cutoff
            except ValueError:
                # Malformed/legacy key: exclude it from a bounded window
                # rather than guessing its date.
                in_window = False
            if not in_window:
                continue
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
