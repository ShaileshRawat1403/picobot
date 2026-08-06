"""launchd LaunchAgent management for the web interface (macOS).

The LaunchAgent starts Pico at login and keeps it running: ``KeepAlive``
restarts the server whenever it exits, so a crash self-heals. Output is
captured to a log under the workspace so background death is never silent.
"""

from __future__ import annotations

import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

AGENT_LABEL = "com.picobot.web"

LOG_RELATIVE = Path("logs") / "pico-web.log"


def agent_plist_path() -> Path:
    """Return the per-user LaunchAgent plist location."""
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def agent_log_path(workspace: Path) -> Path:
    """Return the web server log path under the workspace."""
    return workspace / LOG_RELATIVE


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _web_python() -> str:
    """Prefer this repository's venv interpreter for the agent process."""
    venv_python = _repo_root() / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def build_agent_plist(
    config_path: Path, workspace: Path, host: str = "127.0.0.1", port: int = 18791
) -> dict:
    """Build the LaunchAgent plist content for the web interface."""
    log_path = agent_log_path(workspace)
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": [
            _web_python(),
            "-m",
            "picobot",
            "web",
            "--host",
            host,
            "--port",
            str(port),
            "--config",
            str(config_path.resolve()),
            "--wait-for-free-port",
        ],
        "WorkingDirectory": str(_repo_root()),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def agent_is_installed() -> bool:
    """Whether the LaunchAgent plist exists on disk."""
    return agent_plist_path().exists()


def agent_is_loaded() -> bool:
    """Whether launchd has the agent bootstrapped and (re)starting the server."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["launchctl", "list", AGENT_LABEL],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _user_domain() -> str:
    return f"gui/{os.getuid()}"


def install_agent(
    config_path: Path, workspace: Path, host: str = "127.0.0.1", port: int = 18791
) -> tuple[Path, Path]:
    """Write and bootstrap the LaunchAgent. Returns (plist_path, log_path)."""
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent support is macOS-only.")
    plist_path = agent_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(build_agent_plist(config_path, workspace, host, port), f)
    log_path = agent_log_path(workspace)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["launchctl", "bootout", f"{_user_domain()}/{AGENT_LABEL}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    subprocess.run(
        ["launchctl", "bootstrap", _user_domain(), str(plist_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return plist_path, agent_log_path(workspace)

def uninstall_agent() -> Path:
    """Stop and remove the LaunchAgent. Returns the removed plist path."""
    plist_path = agent_plist_path()
    try:
        subprocess.run(
            ["launchctl", "bootout", f"{_user_domain()}/{AGENT_LABEL}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if plist_path.exists():
        plist_path.unlink()
    return plist_path


def web_port_listening(host: str, port: int) -> bool:
    """Whether the web UI HTTP port (port + 1) accepts connections."""
    try:
        with socket.create_connection((host, port + 1), timeout=0.5):
            return True
    except OSError:
        return False
