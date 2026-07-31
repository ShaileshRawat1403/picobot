from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
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


def test_capability_registry_uses_server_owned_profiles_and_safe_status():
    registry = CapabilityRegistry()

    assert registry.allowed_tools("personal-work", {"list_skills", "web_search"}) == {
        "list_skills"
    }
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

    profile = channel._set_browser_session_profile(client_id, session_id, "research")
    operations = channel._browser_operations(client_id, session_id)

    assert profile["id"] == "research"
    assert operations["profile"]["id"] == "research"
    assert any(item["id"] == "research.search" and item["state"] == "ready" for item in operations["capabilities"])
    assert "api_key" not in str(operations).lower()
    browser_profile = channel._set_browser_session_profile(client_id, session_id, "browser-review")
    assert browser_profile["id"] == "browser-review"
    operations = channel._browser_operations(client_id, session_id)
    browser_read = next(item for item in operations["capabilities"] if item["id"] == "browser.read_shared_tab")
    assert browser_read["state"] == "needs_setup"


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
