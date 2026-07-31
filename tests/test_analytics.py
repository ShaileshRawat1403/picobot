"""Regression coverage for persisted Pico usage analytics."""

from __future__ import annotations

import json
from pathlib import Path

from picobot.bus.analytics import Analytics


def test_analytics_restores_json_channels_as_a_runtime_set(tmp_path: Path):
    analytics_file = tmp_path / ".picobot" / "analytics.json"
    analytics_file.parent.mkdir(parents=True)
    analytics_file.write_text(
        json.dumps(
            {
                "events": [],
                "daily": {
                    "2000-01-01": {
                        "messages": 2,
                        "tool_calls": 0,
                        "errors": 1,
                        "channels": ["web"],
                    }
                },
            }
        ),
        "utf-8",
    )

    analytics = Analytics(tmp_path)
    analytics.track("message", channel="web", model="gpt-4.1-mini")

    today = next(key for key in analytics.data["daily"] if key != "2000-01-01")
    assert analytics.data["daily"]["2000-01-01"]["channels"] == {"web"}
    assert analytics.data["daily"][today]["channels"] == {"web"}
    saved = json.loads(analytics_file.read_text("utf-8"))
    assert saved["daily"]["2000-01-01"]["channels"] == ["web"]


def test_analytics_ignores_malformed_persisted_daily_values(tmp_path: Path):
    analytics_file = tmp_path / ".picobot" / "analytics.json"
    analytics_file.parent.mkdir(parents=True)
    analytics_file.write_text(
        json.dumps({"events": "not-a-list", "daily": {"bad": {"messages": "many", "channels": 3}}}),
        "utf-8",
    )

    analytics = Analytics(tmp_path)

    assert analytics.data == {
        "events": [],
        "daily": {"bad": {"messages": 0, "tool_calls": 0, "errors": 0, "channels": set()}},
    }
