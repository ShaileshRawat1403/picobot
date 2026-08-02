from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.missions import MissionStore
from picobot.operations import CapabilityRegistry, ToolActivityStore
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _ProfileRecordingProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append({"tools": tools or [], "messages": messages})
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tool-1", name="list_skills", arguments={})],
            )
        return LLMResponse(content="Done.")


class _WorkspaceInspectProvider(LLMProvider):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.calls: list[dict] = []

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append({"tools": tools or [], "messages": messages})
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="workspace-read-1",
                        name="read_file",
                        arguments={"path": self.path},
                    )
                ],
            )
        return LLMResponse(content="Inspected.")


class _WorkspaceRunProvider(LLMProvider):
    def __init__(self, command: str):
        super().__init__()
        self.command = command
        self.calls: list[dict] = []

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append({"tools": tools or [], "messages": messages})
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="workspace-run-1",
                        name="exec",
                        arguments={"command": self.command},
                    )
                ],
            )
        return LLMResponse(content="Checked.")


def test_capability_registry_uses_server_owned_profiles_and_safe_status():
    registry = CapabilityRegistry()

    assert registry.allowed_tools("personal-work", {"list_skills", "web_search", "save_mission_artifact_draft"}) == {
        "list_skills"
    }
    assert registry.allowed_tools("mission-work", {"list_skills", "get_skill", "save_mission_artifact_draft"}) == {
        "list_skills",
        "get_skill",
        "save_mission_artifact_draft",
    }
    assert registry.allowed_tools("browser-review", {"list_skills", "get_skill", "browser_read_shared_tab", "save_mission_artifact_draft"}) == {
        "list_skills",
        "get_skill",
        "browser_read_shared_tab",
    }
    assert registry.allowed_tools("github-review", {"list_skills", "get_skill", "github_pr", "web_search"}) == {
        "list_skills",
        "get_skill",
        "github_pr",
    }
    assert registry.allowed_tools("delegated-research", {"list_skills", "get_skill", "spawn", "exec"}) == {
        "list_skills",
        "get_skill",
        "spawn",
    }
    assert registry.allowed_tools("personal-work", {"spawn", "exec"}) == set()
    assert registry.allowed_tools("calendar-read", {"list_skills", "get_skill", "calendar", "web_search"}) == {
        "list_skills",
        "get_skill",
        "calendar",
    }
    assert registry.allowed_tools(
        "workspace-inspect",
        {"list_skills", "get_skill", "read_file", "list_dir", "write_file", "edit_file", "exec"},
    ) == {"list_skills", "get_skill", "read_file", "list_dir"}
    assert registry.allowed_tools(
        "workspace-build",
        {"list_skills", "get_skill", "propose_workspace_change", "write_file", "exec"},
    ) == {"list_skills", "get_skill", "propose_workspace_change"}
    with pytest.raises(ValueError, match="Unknown Pico capability profile"):
        registry.profile("everything")

    statuses = registry.statuses(
        "research", {"list_skills", "get_skill", "web_search", "web_fetch"}, web_search_configured=True
    )
    search = next(item for item in statuses if item.id == "research.search")
    assert search.permitted is True
    assert search.available is True
    assert search.risk == "read"
    assert search.approval == "none"

    # Verify save_artifact_draft is permitted only in mission-work
    draft_pw = next(item for item in registry.statuses("personal-work", {"save_mission_artifact_draft"}, web_search_configured=False) if item.id == "missions.save_artifact_draft")
    assert draft_pw.permitted is False

    draft_res = next(item for item in registry.statuses("research", {"save_mission_artifact_draft"}, web_search_configured=False) if item.id == "missions.save_artifact_draft")
    assert draft_res.permitted is False

    draft_br = next(item for item in registry.statuses("browser-review", {"save_mission_artifact_draft"}, web_search_configured=False) if item.id == "missions.save_artifact_draft")
    assert draft_br.permitted is False

    draft_mw = next(item for item in registry.statuses("mission-work", {"save_mission_artifact_draft"}, web_search_configured=False) if item.id == "missions.save_artifact_draft")
    assert draft_mw.permitted is True

    github = next(
        item
        for item in registry.statuses(
            "github-review", {"github_pr"}, web_search_configured=False, github_configured=True
        )
        if item.id == "github.pull_request"
    )
    assert github.permitted is True and github.available is True and github.risk == "read"
    github_missing = next(
        item
        for item in registry.statuses(
            "github-review", {"github_pr"}, web_search_configured=False, github_configured=False
        )
        if item.id == "github.pull_request"
    )
    assert github_missing.state == "needs_setup"
    assert "GitHub CLI" in (github_missing.setup_hint or "")

    calendar = next(
        item
        for item in registry.statuses(
            "calendar-read", {"calendar"}, web_search_configured=False, calendar_configured=False
        )
        if item.id == "calendar.read"
    )
    assert calendar.permitted is True and calendar.state == "needs_setup"
    assert "Google Calendar" in (calendar.setup_hint or "")

    workspace_read = next(
        item
        for item in registry.statuses(
            "workspace-inspect", {"read_file", "list_dir"}, web_search_configured=False
        )
        if item.id == "workspace.read_file"
    )
    assert workspace_read.permitted is True and workspace_read.available is True
    assert "write_file" not in registry.allowed_tools(
        "workspace-inspect", {"write_file", "edit_file"}
    )

    # Verify profiles list includes mission-work
    profile_ids = [p["id"] for p in registry.profiles()]
    assert "mission-work" in profile_ids
    assert "github-review" in profile_ids
    assert "delegated-research" in profile_ids
    assert "calendar-read" in profile_ids
    assert "workspace-inspect" in profile_ids
    assert "workspace-build" in profile_ids


def test_activity_evidence_is_session_and_owner_scoped_without_payloads(tmp_path: Path):
    store = ToolActivityStore(tmp_path / "workspace")
    recorded = store.record(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="research",
        capability_id="research.search",
        tool_name="web_search",
        risk="read",
        outcome="success",
    )

    assert store.list("web:browser:owner-a", "web:web:owner-a:session-a") == [recorded]
    assert store.list("web:browser:owner-b", "web:web:owner-a:session-a") == []
    assert not hasattr(recorded, "arguments")
    assert not hasattr(recorded, "result")


@pytest.mark.asyncio
async def test_agent_provider_surface_and_execution_follow_session_profile(tmp_path: Path):
    provider = _ProfileRecordingProvider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)

    response = await agent._process_message(
        InboundMessage(channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="List skills")
    )

    assert response is not None and response.content == "Done."
    visible_tools = {item["function"]["name"] for item in provider.calls[0]["tools"]}
    assert visible_tools == {"list_skills", "get_skill"}
    activity = agent.tool_activity.list("web:browser:owner-a", "web:chat-a")
    assert [(item.tool_name, item.outcome, item.profile_id) for item in activity] == [
        ("list_skills", "success", "personal-work")
    ]

    blocked = await agent.tools.execute("web_search", {"query": "must not execute"}, {"list_skills"})
    assert "not permitted by this session" in blocked


@pytest.mark.asyncio
async def test_workspace_inspect_profile_reads_only_inside_configured_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("private workbench note\n", encoding="utf-8")
    provider = _WorkspaceInspectProvider("notes.txt")
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace)
    session = agent.sessions.get_or_create("web:chat-a")
    session.metadata["pico_operation_profile"] = "workspace-inspect"
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="Inspect notes")
    )

    assert response is not None and response.content == "Inspected."
    visible_tools = {item["function"]["name"] for item in provider.calls[0]["tools"]}
    assert visible_tools == {"list_skills", "get_skill", "read_file", "list_dir"}
    assert "private workbench note" in str(provider.calls[1]["messages"])

    outside = await agent._run_agent_loop(
        [{"role": "user", "content": "inspect"}],
        allowed_tools={"read_file"},
        activity_context={
            "owner_id": "web:browser:owner-a",
            "session_key": "web:chat-a",
            "profile_id": "workspace-inspect",
        },
        serving_provider=_WorkspaceInspectProvider("../outside.txt"),
    )
    assert any(
        "Workspace inspection is limited" in str(message.get("content"))
        for message in outside[2]
        if message.get("role") == "tool"
    )


@pytest.mark.asyncio
async def test_workspace_run_profile_allows_diagnostics_and_blocks_mutation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = _WorkspaceRunProvider("git status --short")
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace)
    session = agent.sessions.get_or_create("web:chat-a")
    session.metadata["pico_operation_profile"] = "workspace-run"
    agent.sessions.save(session)

    response = await agent._process_message(
        InboundMessage(channel="web", sender_id="browser:owner-a", chat_id="chat-a", content="Check status")
    )
    assert response is not None and response.content == "Checked."
    assert {item["function"]["name"] for item in provider.calls[0]["tools"]} == {"list_skills", "get_skill", "exec"}

    blocked = await agent._run_agent_loop(
        [{"role": "user", "content": "diagnose"}],
        allowed_tools={"exec"},
        activity_context={
            "owner_id": "web:browser:owner-a",
            "session_key": "web:chat-a",
            "profile_id": "workspace-run",
        },
        serving_provider=_WorkspaceRunProvider("python3 -c 'open(\"created.txt\", \"w\").write(\"x\")'"),
    )
    assert any(
        "not permitted" in str(message.get("content"))
        for message in blocked[2]
        if message.get("role") == "tool"
    )


def test_browser_operations_profile_is_scoped_and_exposes_no_config_values(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(
        workspace_path=workspace,
        tools=SimpleNamespace(web=SimpleNamespace(search=SimpleNamespace(provider="brave"))),
    )
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    client_id = "browser_identity_0001"
    session_id = "session_identity_0001"

    personal = channel._set_browser_session_profile(client_id, session_id, "personal-work")
    personal_operations = channel._browser_operations(client_id, session_id)
    assert personal["id"] == "personal-work"
    assert {item["id"] for item in personal_operations["capabilities"]} == {
        "skills.list",
        "skills.read",
    }
    assert all(item["state"] != "not_in_profile" for item in personal_operations["capabilities"])

    profile = channel._set_browser_session_profile(client_id, session_id, "research")
    mission_store = MissionStore(workspace)
    mission = mission_store.create(
        owner_id=channel._memory_owner(client_id),
        session_key=channel._session_key(client_id, session_id),
        title="Research boundary",
        objective="Keep mission context separate from governed authority.",
    )
    mission = mission_store.transition(channel._memory_owner(client_id), mission.id, "active")
    channel._set_browser_session_active_mission(client_id, session_id, mission.id)
    operations = channel._browser_operations(client_id, session_id)

    assert profile["id"] == "research"
    assert operations["profile"]["id"] == "research"
    assert operations["active_mission"]["id"] == mission.id
    assert "attached as context" in operations["profile"]["description"]
    session = channel._session_manager().get_or_create(channel._session_key(client_id, session_id))
    assert session.metadata["pico_operation_profile"] == "research"
    assert any(item["id"] == "research.search" and item["state"] == "ready" for item in operations["capabilities"])
    assert "api_key" not in str(operations).lower()
    browser_profile = channel._set_browser_session_profile(client_id, session_id, "browser-review")
    assert browser_profile["id"] == "browser-review"
    operations = channel._browser_operations(client_id, session_id)
    browser_read = next(item for item in operations["capabilities"] if item["id"] == "browser.read_shared_tab")
    assert browser_read["state"] == "needs_setup"

    mission_profile = channel._set_browser_session_profile(client_id, session_id, "mission-work")
    mission_operations = channel._browser_operations(client_id, session_id)
    assert mission_profile["id"] == "mission-work"
    assert {item["id"] for item in mission_operations["capabilities"]} == {
        "skills.list",
        "skills.read",
        "missions.save_artifact_draft",
    }
    draft = next(item for item in mission_operations["capabilities"] if item["id"] == "missions.save_artifact_draft")
    assert draft["state"] == "ready"


def test_browser_share_status_is_owner_session_scoped_and_never_exposes_snapshot_or_token(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(
        workspace_path=workspace,
        tools=SimpleNamespace(web=SimpleNamespace(search=SimpleNamespace(provider="brave"))),
    )
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    client_id, session_id = "browser_identity_0001", "session_identity_0001"
    channel._set_browser_session_profile(client_id, session_id, "browser-review")
    code = channel._browser_bridge_store().create_ticket(
        channel._memory_owner(client_id), channel._session_key(client_id, session_id)
    )
    tab, token = channel._browser_bridge_store().share(
        code, 42, "https://example.com/docs", "Example docs", "api_key: sk-redact-this-value"
    )

    operations = channel._browser_operations(client_id, session_id)
    assert operations["shared_browser_tab"]["id"] == tab.id
    assert "snapshot_text" not in str(operations)
    assert token not in str(operations)
    browser_read = next(item for item in operations["capabilities"] if item["id"] == "browser.read_shared_tab")
    assert browser_read["state"] == "ready"
    assert channel._browser_operations("browser_identity_0002", session_id)["shared_browser_tab"] is None


def test_browser_operations_exposes_only_actions_for_its_owner_and_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(
        workspace_path=workspace,
        tools=SimpleNamespace(web=SimpleNamespace(search=SimpleNamespace(provider="brave"))),
    )
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    client_id = "browser_identity_0001"
    session_id = "session_identity_0001"
    channel._set_browser_session_profile(client_id, session_id, "research")
    own = channel._action_store().stage(
        owner_id=channel._memory_owner(client_id),
        session_key=channel._session_key(client_id, session_id),
        profile_id="browser-review",
        capability_id="browser.form_submit",
        tool_name="browser_submit",
        target="example.com",
        summary="Submit a reviewed form.",
    )
    channel._action_store().stage(
        owner_id=channel._memory_owner("browser_identity_0002"),
        session_key=channel._session_key("browser_identity_0002", session_id),
        profile_id="browser-review",
        capability_id="browser.form_submit",
        tool_name="browser_submit",
        target="other.example",
        summary="Do not expose this action.",
    )

    operations = channel._browser_operations(client_id, session_id)

    assert [item["id"] for item in operations["actions"]] == [own.id]
