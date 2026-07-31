"""Durable governed registry for skills and configured MCP servers."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterable, Literal


@dataclass(frozen=True)
class GovernedEntry:
    """Safe metadata for a governed skill or MCP server."""

    id: str
    display_name: str
    kind: Literal["skill", "mcp_server"]
    enabled: bool
    risk: Literal["read", "mutating"]
    readiness: Literal["untested", "ready", "failed", "disabled"]
    safe_diagnostic: str
    last_checked_at: str | None
    discovered_tools: tuple[str, ...]
    allowed_profiles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return safe public representation with no credentials or arguments."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "enabled": self.enabled,
            "risk": self.risk,
            "readiness": self.readiness,
            "safe_diagnostic": self.safe_diagnostic,
            "last_checked_at": self.last_checked_at,
            "discovered_tools": list(self.discovered_tools),
            "allowed_profiles": list(self.allowed_profiles),
        }


def sanitize_diagnostic(text: str) -> str:
    """Scrub diagnostic text so secrets, API keys, and paths are never exposed."""
    if not text:
        return "No diagnostic info"
    # Redact secret patterns
    text = re.sub(
        r"(?:api_key|apikey|token|secret|authorization|bearer|sk-)[=:\s]*[^\s,;&]+",
        "[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Diagnostics are visible in the local web UI.  A connection error may
    # legitimately contain its local path, but that detail is not useful for
    # capability decisions and should not become evidence or UI output.
    text = re.sub(
        r"(?:file:)?/(?:Users|home|private|tmp|var|opt)(?:/[^\s,;&]*)*",
        "[path redacted]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"~/(?:[^\s,;&]*)", "[path redacted]", text)
    cleaned = " ".join(text.split())
    if not cleaned:
        return "No diagnostic info"
    return cleaned[:300]


class GovernedRegistryStore:
    """Owner/workspace-scoped store for governed skills and MCP entries."""

    DEFAULT_PROFILES = ("personal-work", "research", "browser-review")
    MCP_PROFILES = ("research",)

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = workspace / "operations"
        self.root.mkdir(parents=True, exist_ok=True)
        self.file_path = self.root / "governed-registry.json"
        self._entries: dict[str, GovernedEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            self._entries = {}
            return
        try:
            data = json.loads(self.file_path.read_text("utf-8"))
            entries = {}
            for raw in data.get("entries", []):
                entry = GovernedEntry(
                    id=raw["id"],
                    display_name=raw["display_name"],
                    kind=raw["kind"],
                    enabled=bool(raw["enabled"]),
                    risk=raw.get("risk", "read"),
                    readiness=raw.get("readiness", "untested"),
                    safe_diagnostic=sanitize_diagnostic(raw.get("safe_diagnostic", "")),
                    last_checked_at=raw.get("last_checked_at"),
                    discovered_tools=tuple(raw.get("discovered_tools", [])),
                    allowed_profiles=tuple(raw.get("allowed_profiles", self.DEFAULT_PROFILES)),
                )
                entries[entry.id] = entry
            self._entries = entries
        except Exception:
            self._entries = {}

    def _save(self) -> None:
        payload = {
            "entries": [entry.to_dict() for entry in self._entries.values()]
        }
        self.file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def register_builtin_skills(self) -> None:
        """Register Pico's default skill tools as governed entries."""
        skills = [
            GovernedEntry(
                id="skill:list_skills",
                display_name="Inspect installed skills",
                kind="skill",
                enabled=True,
                risk="read",
                readiness="ready",
                safe_diagnostic="Built-in skill loader ready",
                last_checked_at=self._now(),
                discovered_tools=("list_skills",),
                allowed_profiles=self.DEFAULT_PROFILES,
            ),
            GovernedEntry(
                id="skill:get_skill",
                display_name="Read skill guide",
                kind="skill",
                enabled=True,
                risk="read",
                readiness="ready",
                safe_diagnostic="Built-in skill loader ready",
                last_checked_at=self._now(),
                discovered_tools=("get_skill",),
                allowed_profiles=self.DEFAULT_PROFILES,
            ),
        ]
        for skill in skills:
            if skill.id not in self._entries:
                self._entries[skill.id] = skill
        self._save()

    def sync_inventory(self, skills_loader=None, mcp_servers_config: dict | None = None) -> None:
        """Synchronize skills and MCP server definitions with the registry."""
        self.register_builtin_skills()

        # Sync skills fromloader if provided
        if skills_loader is not None:
            try:
                for skill_info in skills_loader.list_skills(filter_unavailable=False):
                    skill_name = skill_info["name"]
                    entry_id = f"skill:{skill_name}"
                    meta = skills_loader.get_skill_metadata(skill_name) or {}
                    skill_meta = skills_loader._get_skill_meta(skill_name)
                    available = skills_loader._check_requirements(skill_meta)
                    enabled = skills_loader.is_skill_enabled(skill_name)
                    risk = "mutating" if skill_meta.get("risk") == "mutating" else "read"
                    if not enabled:
                        readiness = "disabled"
                        diagnostic = "Disabled in skill configuration"
                    elif available:
                        readiness = "ready"
                        diagnostic = "Skill requirements met"
                    else:
                        readiness = "failed"
                        missing = skills_loader._get_missing_requirements(skill_meta)
                        diagnostic = f"Unmet requirements: {missing}" if missing else "Requirements not met"

                    existing = self._entries.get(entry_id)
                    allowed_profiles = existing.allowed_profiles if existing else self.DEFAULT_PROFILES
                    new_entry = GovernedEntry(
                        id=entry_id,
                        display_name=meta.get("description", skill_name),
                        kind="skill",
                        enabled=enabled,
                        risk=risk,
                        readiness=readiness,
                        safe_diagnostic=sanitize_diagnostic(diagnostic),
                        last_checked_at=self._now(),
                        discovered_tools=(),
                        allowed_profiles=allowed_profiles,
                    )
                    self._entries[entry_id] = new_entry
            except Exception:
                pass

        # Sync configured MCP servers
        if mcp_servers_config:
            for server_name, server_cfg in mcp_servers_config.items():
                entry_id = f"mcp:{server_name}"
                enabled = getattr(server_cfg, "enabled", True)
                risk = getattr(server_cfg, "risk", "read")
                existing = self._entries.get(entry_id)
                if existing:
                    readiness = existing.readiness
                    diagnostic = existing.safe_diagnostic
                    discovered = existing.discovered_tools
                    profiles = existing.allowed_profiles
                    last_checked = existing.last_checked_at
                    if not enabled:
                        readiness = "disabled"
                        diagnostic = "MCP server is disabled"
                else:
                    readiness = "disabled" if not enabled else "untested"
                    diagnostic = "MCP server disabled" if not enabled else "Not probed yet"
                    discovered = ()
                    profiles = self.MCP_PROFILES
                    last_checked = None

                self._entries[entry_id] = GovernedEntry(
                    id=entry_id,
                    display_name=f"MCP Server ({server_name})",
                    kind="mcp_server",
                    enabled=enabled,
                    risk=risk,
                    readiness=readiness,
                    safe_diagnostic=sanitize_diagnostic(diagnostic),
                    last_checked_at=last_checked,
                    discovered_tools=discovered,
                    allowed_profiles=profiles,
                )
        self._save()

    def get(self, entry_id: str) -> GovernedEntry | None:
        return self._entries.get(entry_id)

    def list_entries(self) -> list[GovernedEntry]:
        return list(self._entries.values())

    def update_entry(
        self,
        entry_id: str,
        *,
        enabled: bool | None = None,
        readiness: Literal["untested", "ready", "failed", "disabled"] | None = None,
        safe_diagnostic: str | None = None,
        discovered_tools: Iterable[str] | None = None,
        allowed_profiles: Iterable[str] | None = None,
        risk: Literal["read", "mutating"] | None = None,
    ) -> GovernedEntry:
        existing = self.get(entry_id)
        if existing is None:
            raise KeyError(f"Governed entry '{entry_id}' not found")

        updated = GovernedEntry(
            id=existing.id,
            display_name=existing.display_name,
            kind=existing.kind,
            enabled=existing.enabled if enabled is None else enabled,
            risk=existing.risk if risk is None else risk,
            readiness=existing.readiness if readiness is None else readiness,
            safe_diagnostic=sanitize_diagnostic(existing.safe_diagnostic if safe_diagnostic is None else safe_diagnostic),
            last_checked_at=self._now(),
            discovered_tools=existing.discovered_tools if discovered_tools is None else tuple(discovered_tools),
            allowed_profiles=existing.allowed_profiles if allowed_profiles is None else tuple(allowed_profiles),
        )
        self._entries[entry_id] = updated
        self._save()
        return updated

    async def probe(
        self,
        entry_id: str,
        probe_func: Callable[[str], Awaitable[tuple[bool, str, list[str]]]] | None = None,
        timeout: float = 5.0,
    ) -> GovernedEntry:
        """Safely probe an entry with bounded timeout."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(f"Governed entry '{entry_id}' not found")

        if not entry.enabled:
            return self.update_entry(
                entry_id,
                readiness="disabled",
                safe_diagnostic="Entry is disabled",
                discovered_tools=(),
            )

        if probe_func is None:
            # Default probe stub if no explicit probe handler is supplied
            return self.update_entry(
                entry_id,
                readiness="failed",
                safe_diagnostic="No probe implementation available for this entry",
                discovered_tools=(),
            )

        try:
            success, diagnostic, tools = await asyncio.wait_for(probe_func(entry_id), timeout=timeout)
            if success:
                return self.update_entry(
                    entry_id,
                    readiness="ready",
                    safe_diagnostic=diagnostic or f"Connected ({len(tools)} tools discovered)",
                    discovered_tools=tools,
                )
            else:
                return self.update_entry(
                    entry_id,
                    readiness="failed",
                    safe_diagnostic=diagnostic or "Probe failed",
                    discovered_tools=(),
                )
        except asyncio.TimeoutError:
            return self.update_entry(
                entry_id,
                readiness="failed",
                safe_diagnostic=f"Probe timed out after {timeout}s",
                discovered_tools=(),
            )
        except Exception as exc:
            return self.update_entry(
                entry_id,
                readiness="failed",
                safe_diagnostic=f"Probe error: {sanitize_diagnostic(str(exc))}",
                discovered_tools=(),
            )

    def get_entry_for_tool(self, tool_name: str) -> GovernedEntry | None:
        """Find the governed entry responsible for a tool name."""
        for entry in self._entries.values():
            if tool_name in entry.discovered_tools:
                return entry
            if entry.id.startswith("skill:") and entry.id.removeprefix("skill:") == tool_name:
                return entry
        if tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)
            if len(parts) >= 2:
                server_name = parts[1]
                if f"mcp:{server_name}" in self._entries:
                    return self._entries[f"mcp:{server_name}"]
        return None

    def is_tool_allowed(
        self,
        tool_name: str,
        session_profile_id: str,
        session_profile_tool_names: Iterable[str],
    ) -> bool:
        """Check all 4 governance gates:
        1. Entry enabled state
        2. Entry readiness state == 'ready'
        3. session_profile_id in entry.allowed_profiles
        4. Tool allowed by session profile tool list
        """
        entry = self.get_entry_for_tool(tool_name)
        if entry is None:
            # Not a governed entry; enforce session profile tool names directly
            return tool_name in session_profile_tool_names

        # Gate 1: Enabled state
        if not entry.enabled:
            return False

        # Gate 2: Readiness state
        if entry.readiness != "ready":
            return False

        # Gate 3: Registry capability-profile allow-list
        if session_profile_id not in entry.allowed_profiles:
            return False

        # Gate 4: Existing session capability profile.  MCP servers do not
        # gain a blanket bypass just because the registry allowed a profile:
        # the profile must carry Pico's explicit, read-only MCP entitlement.
        if entry.kind == "skill" and tool_name in ("list_skills", "get_skill"):
            return tool_name in session_profile_tool_names
        if entry.kind == "mcp_server":
            return entry.risk == "read" and "governed_mcp_read" in session_profile_tool_names

        return tool_name in session_profile_tool_names

    def filter_allowed_tools(
        self,
        session_profile_id: str,
        candidate_tools: Iterable[str],
        session_profile_tool_names: Iterable[str],
    ) -> set[str]:
        """Return subset of candidate_tools that pass all 4 governance gates."""
        session_tools = set(session_profile_tool_names)
        allowed = set()
        for tool_name in candidate_tools:
            if self.is_tool_allowed(tool_name, session_profile_id, session_tools):
                allowed.add(tool_name)
        return allowed
