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
    requires_github_cli: bool = False
    requires_calendar_token: bool = False


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
            description="Chat, explicit memory, artifacts, and approved skills. No external tools or mission action proposals.",
            tool_names=("list_skills", "get_skill"),
        ),
        "research": SessionProfile(
            id="research",
            label="Research",
            description="Personal work plus web search and page fetching. No external writes.",
            tool_names=("list_skills", "get_skill", "web_search", "web_fetch", "governed_mcp_read"),
        ),
        "browser-review": SessionProfile(
            id="browser-review",
            label="Browser review",
            description="Read one browser tab you explicitly share with this session. No browser writes.",
            tool_names=("list_skills", "get_skill", "browser_read_shared_tab"),
        ),
        "browser-action": SessionProfile(
            id="browser-action",
            label="Browser action",
            description="Propose bounded navigate, click, or non-sensitive type actions on one shared tab. Every write requires approval.",
            tool_names=("list_skills", "get_skill", "browser_read_shared_tab", "browser_action"),
        ),
        "mission-work": SessionProfile(
            id="mission-work",
            label="Mission work",
            description="Governed mission work with approved workflow blueprints and explicit action approval.",
            tool_names=("list_skills", "get_skill", "save_mission_artifact_draft"),
        ),
        "github-review": SessionProfile(
            id="github-review",
            label="GitHub review",
            description="Read GitHub pull requests through the authenticated GitHub CLI. No comments, approvals, merges, or pushes.",
            tool_names=("list_skills", "get_skill", "github_pr"),
        ),
        "delegated-research": SessionProfile(
            id="delegated-research",
            label="Delegated research",
            description="Run bounded read-only parallel research through a local subagent. No file writes, shell, approvals, or external mutations.",
            tool_names=("list_skills", "get_skill", "spawn"),
        ),
        "calendar-read": SessionProfile(
            id="calendar-read",
            label="Calendar read",
            description="Read upcoming calendar events through the locally configured Google Calendar token. No event writes.",
            tool_names=("list_skills", "get_skill", "calendar"),
        ),
        "workspace-inspect": SessionProfile(
            id="workspace-inspect",
            label="Workspace inspect",
            description="Inspect files and directories in Pico's configured workspace. No file writes, shell commands, or external tools.",
            tool_names=("list_skills", "get_skill", "read_file", "list_dir"),
        ),
        "workspace-run": SessionProfile(
            id="workspace-run",
            label="Workspace diagnostics",
            description="Run bounded read-only workspace diagnostics. File writes, network access, package installs, and shell mutation are blocked.",
            tool_names=("list_skills", "get_skill", "exec"),
        ),
        "workspace-build": SessionProfile(
            id="workspace-build",
            label="Workspace build",
            description="Draft bounded workspace file and command changes for explicit owner approval. Pico never executes a change directly from chat.",
            tool_names=("list_skills", "get_skill", "propose_workspace_change"),
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
            profiles=("personal-work", "research", "browser-review", "browser-action", "mission-work", "github-review", "delegated-research", "calendar-read", "workspace-inspect", "workspace-run", "workspace-build"),
        ),
        CapabilitySpec(
            id="skills.read",
            tool_name="get_skill",
            toolset="skills",
            label="Read a skill guide",
            risk="read",
            approval="none",
            profiles=("personal-work", "research", "browser-review", "browser-action", "mission-work", "github-review", "delegated-research", "calendar-read", "workspace-inspect", "workspace-run", "workspace-build"),
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
            id="browser.write",
            tool_name="browser_action",
            toolset="browser",
            label="Propose a bounded browser action",
            risk="mutating",
            approval="explicit",
            profiles=("browser-action",),
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
        CapabilitySpec(
            id="missions.save_artifact_draft",
            tool_name="save_mission_artifact_draft",
            toolset="missions",
            label="Propose draft mission artifact",
            risk="mutating",
            approval="explicit",
            profiles=("mission-work",),
        ),
        CapabilitySpec(
            id="github.pull_request",
            tool_name="github_pr",
            toolset="github",
            label="Inspect GitHub pull request",
            risk="read",
            approval="none",
            profiles=("github-review",),
            requires_github_cli=True,
        ),
        CapabilitySpec(
            id="delegation.spawn",
            tool_name="spawn",
            toolset="delegation",
            label="Run bounded read-only delegated research",
            risk="read",
            approval="none",
            profiles=("delegated-research",),
        ),
        CapabilitySpec(
            id="calendar.read",
            tool_name="calendar",
            toolset="calendar",
            label="Read upcoming calendar events",
            risk="read",
            approval="none",
            profiles=("calendar-read",),
            requires_calendar_token=True,
        ),
        CapabilitySpec(
            id="workspace.read_file",
            tool_name="read_file",
            toolset="workspace",
            label="Read a workspace file",
            risk="read",
            approval="none",
            profiles=("workspace-inspect",),
        ),
        CapabilitySpec(
            id="workspace.list_dir",
            tool_name="list_dir",
            toolset="workspace",
            label="List a workspace directory",
            risk="read",
            approval="none",
            profiles=("workspace-inspect",),
        ),
        CapabilitySpec(
            id="workspace.diagnostics",
            tool_name="exec",
            toolset="workspace",
            label="Run workspace diagnostics",
            risk="read",
            approval="none",
            profiles=("workspace-run",),
        ),
        CapabilitySpec(
            id="workspace.propose_change",
            tool_name="propose_workspace_change",
            toolset="workspace",
            label="Propose a workspace change",
            risk="draft",
            approval="explicit",
            profiles=("workspace-build",),
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
        github_configured: bool = False,
        calendar_configured: bool = False,
    ) -> list[CapabilityStatus]:
        profile = self.profile(profile_id)
        registered = set(registered_tools)
        result = []
        for item in self.CAPABILITIES:
            permitted = item.tool_name in profile.tool_names
            configured = not item.requires_setup or web_search_configured
            if item.requires_browser_share:
                configured = browser_shared
            if item.requires_github_cli:
                configured = github_configured
            if item.requires_calendar_token:
                configured = calendar_configured
            available = item.tool_name in registered and configured
            state = "ready" if permitted and available else "needs_setup" if permitted else "not_in_profile"
            hint = None
            if item.requires_setup and not configured:
                hint = "Configure a supported search provider before using this capability."
            elif item.requires_browser_share and not configured:
                hint = "Share one current browser tab with this Pico session from the local Pico Browser Bridge."
            elif item.requires_github_cli and not configured:
                hint = "Install GitHub CLI (gh) and authenticate it locally before using this capability."
            elif item.requires_calendar_token and not configured:
                hint = "Complete the supported local Google Calendar OAuth setup before using this capability."
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
