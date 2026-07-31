"""Narrow, server-owned tool capability profiles for Pico sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SessionProfile:
    id: str
    label: str
    description: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    tool_name: str
    toolset: str
    label: str
    risk: str
    approval: str
    profiles: tuple[str, ...]
    requires_setup: bool = False
    requires_browser_share: bool = False


@dataclass(frozen=True)
class CapabilityStatus:
    id: str
    tool_name: str
    toolset: str
    label: str
    risk: str
    approval: str
    configured: bool
    available: bool
    permitted: bool
    state: str
    setup_hint: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class CapabilityRegistry:
    """Resolve Pico's small initial profiles without exposing secrets.

    The registry is deliberately the source of truth for which tool definitions
    the provider receives. Client requests choose a named profile only; they
    never submit a raw tool allow-list.
    """

    DEFAULT_PROFILE = "personal-work"
    PROFILES = {
        "personal-work": SessionProfile(
            id="personal-work",
            label="Personal work",
            description="Chat, explicit memory, artifacts, and approved skills. No external tools.",
            tool_names=("list_skills", "get_skill"),
        ),
        "research": SessionProfile(
            id="research",
            label="Research",
            description="Personal work plus web search and page fetching. No external writes.",
            # This is an authority marker, not a tool definition.  It permits
            # only ready, governed, read-only MCP tools after all other gates
            # pass.  Mutating MCP tools are intentionally not exposed by any
            # current session profile.
            tool_names=("list_skills", "get_skill", "web_search", "web_fetch", "governed_mcp_read"),
        ),
        "browser-review": SessionProfile(
            id="browser-review",
            label="Browser review",
            description="Read one browser tab you explicitly share with this session. No browser writes.",
            tool_names=("list_skills", "get_skill", "browser_read_shared_tab"),
        ),
    }
    CAPABILITIES = (
        CapabilitySpec(
            id="skills.list",
            tool_name="list_skills",
            toolset="skills",
            label="Inspect installed skills",
            risk="read",
            approval="none",
            profiles=("personal-work", "research"),
        ),
        CapabilitySpec(
            id="skills.read",
            tool_name="get_skill",
            toolset="skills",
            label="Read a skill guide",
            risk="read",
            approval="none",
            profiles=("personal-work", "research"),
        ),
        CapabilitySpec(
            id="research.search",
            tool_name="web_search",
            toolset="research",
            label="Search the web",
            risk="read",
            approval="none",
            profiles=("research",),
            requires_setup=True,
        ),
        CapabilitySpec(
            id="browser.read_shared_tab",
            tool_name="browser_read_shared_tab",
            toolset="browser",
            label="Read the shared browser tab",
            risk="read",
            approval="none",
            profiles=("browser-review",),
            requires_browser_share=True,
        ),
        CapabilitySpec(
            id="research.fetch",
            tool_name="web_fetch",
            toolset="research",
            label="Read a web page",
            risk="read",
            approval="none",
            profiles=("research",),
        ),
    )

    def profile(self, profile_id: object) -> SessionProfile:
        if not isinstance(profile_id, str) or profile_id not in self.PROFILES:
            raise ValueError("Unknown Pico capability profile")
        return self.PROFILES[profile_id]

    def resolve(self, stored_profile: object) -> SessionProfile:
        return self.PROFILES.get(stored_profile, self.PROFILES[self.DEFAULT_PROFILE])

    def allowed_tools(
        self,
        profile_id: object,
        registered_tools: Iterable[str],
        governed_registry: Any | None = None,
    ) -> set[str]:
        profile = self.profile(profile_id)
        registered = set(registered_tools)
        if governed_registry is None:
            return {name for name in profile.tool_names if name in registered}
        return governed_registry.filter_allowed_tools(
            session_profile_id=profile.id,
            candidate_tools=registered,
            session_profile_tool_names=profile.tool_names,
        )


    def capability_for_tool(self, tool_name: str) -> CapabilitySpec | None:
        return next((item for item in self.CAPABILITIES if item.tool_name == tool_name), None)

    def statuses(
        self,
        profile_id: object,
        registered_tools: Iterable[str],
        *,
        web_search_configured: bool,
        browser_shared: bool = False,
    ) -> list[CapabilityStatus]:
        profile = self.profile(profile_id)
        registered = set(registered_tools)
        result = []
        for item in self.CAPABILITIES:
            permitted = item.tool_name in profile.tool_names
            configured = not item.requires_setup or web_search_configured
            if item.requires_browser_share:
                configured = browser_shared
            available = item.tool_name in registered and configured
            state = "ready" if permitted and available else "needs_setup" if permitted else "not_in_profile"
            hint = None
            if item.requires_setup and not configured:
                hint = "Configure a supported search provider before using this capability."
            elif item.requires_browser_share and not configured:
                hint = "Share one current browser tab with this Pico session from the local Pico Browser Bridge."
            elif permitted and item.tool_name not in registered:
                hint = "This Pico runtime did not register the required tool."
            result.append(
                CapabilityStatus(
                    id=item.id,
                    tool_name=item.tool_name,
                    toolset=item.toolset,
                    label=item.label,
                    risk=item.risk,
                    approval=item.approval,
                    configured=configured,
                    available=available,
                    permitted=permitted,
                    state=state,
                    setup_hint=hint,
                )
            )
        return result

    @classmethod
    def profiles(cls) -> list[dict[str, str]]:
        return [
            {"id": profile.id, "label": profile.label, "description": profile.description}
            for profile in cls.PROFILES.values()
        ]
