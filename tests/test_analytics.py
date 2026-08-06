"""Regression coverage for persisted Pico usage analytics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
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


def test_get_stats_only_counts_days_within_the_window(tmp_path: Path):
    """get_stats(days=N) must exclude records older than N days.

    Regression test: `cutoff` was computed but never applied, so stats
    silently included every day ever recorded regardless of `days`.
    """
    analytics_file = tmp_path / ".picobot" / "analytics.json"
    analytics_file.parent.mkdir(parents=True)
    old_key = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    analytics_file.write_text(
        json.dumps(
            {
                "events": [],
                "daily": {
                    old_key: {
                        "messages": 100,
                        "tool_calls": 0,
                        "errors": 0,
                        "channels": ["web"],
                    },
                    recent_key: {
                        "messages": 5,
                        "tool_calls": 1,
                        "errors": 0,
                        "channels": ["cli"],
                    },
                    "not-a-date": {
                        "messages": 9,
                        "tool_calls": 0,
                        "errors": 0,
                        "channels": [],
                    },
                },
            }
        ),
        "utf-8",
    )

    analytics = Analytics(tmp_path)
    stats = analytics.get_stats(days=7)

    assert stats["total_messages"] == 5
    assert stats["total_tool_calls"] == 1
    assert recent_key in stats["days"]
    assert old_key not in stats["days"]
    assert "not-a-date" not in stats["days"]
