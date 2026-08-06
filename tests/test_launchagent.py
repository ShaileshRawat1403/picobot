"""Tests for launchd LaunchAgent management (plist generation only; no install)."""

from pathlib import Path

from picobot.launchagent import AGENT_LABEL, build_agent_plist


def test_build_agent_plist_runs_web_with_absolute_config():
    plist = build_agent_plist(Path("/repo/.picobot/config.json"), Path("/ws"), "127.0.0.1", 18791)

    assert plist["Label"] == AGENT_LABEL
    args = plist["ProgramArguments"]
    assert "--wait-for-free-port" in args
    assert args[-7:-1] == ["--host", "127.0.0.1", "--port", "18791", "--config", "/repo/.picobot/config.json"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == str(Path("/ws/logs/pico-web.log"))
    assert plist["StandardErrorPath"] == str(Path("/ws/logs/pico-web.log"))
    assert plist["WorkingDirectory"]


def test_build_agent_plist_prefers_repo_venv_python():
    plist = build_agent_plist(Path("/repo/.picobot/config.json"), Path("/ws"))
    python = plist["ProgramArguments"][0]
    assert python.endswith(".venv/bin/python") or "python" in python
