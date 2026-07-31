from pathlib import Path

import pytest

from picobot.agent.loop import AgentLoop
from picobot.agent.tools.browser import BrowserReadSharedTabTool
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.operations.browser_bridge import BrowserBridgeStore
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _BrowserRecordingProvider(LLMProvider):
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
                tool_calls=[ToolCallRequest(id="browser-1", name="browser_read_shared_tab", arguments={})],
            )
        return LLMResponse(content="I inspected the explicitly shared tab.")


def _share(store: BrowserBridgeStore):
    code = store.create_ticket("web:browser:owner-a", "web:web:owner-a:session-a")
    return store.share(
        code,
        42,
        "https://example.com/docs",
        "Example documentation",
        "Safe visible text\napi_key: sk-this-must-not-be-stored-or-returned",
    )


def test_browser_bridge_requires_one_time_pairing_and_exact_share_token(tmp_path: Path):
    store = BrowserBridgeStore(tmp_path / "workspace")
    shared, token = _share(store)

    assert shared.domain == "example.com"
    assert "sk-this" not in shared.snapshot_text
    assert "[redacted]" in shared.snapshot_text
    assert "snapshot_text" not in shared.to_dict()
    with pytest.raises(KeyError):
        store.get("web:browser:owner-b", shared.session_key)
    with pytest.raises(ValueError, match="invalid or expired"):
        store.share(
            "used-code",
            42,
            "https://example.com/docs",
            "Example documentation",
            "text",
        )
    with pytest.raises(ValueError, match="no longer shared"):
        store.refresh(shared.id, "wrong", 42, "https://example.com/docs", "Example", "text")

    refreshed = store.refresh(
        shared.id, token, 42, "https://example.com/new", "New page", "Fresh visible text"
    )
    assert refreshed.url == "https://example.com/new"
    assert refreshed.snapshot_text == "Fresh visible text"


@pytest.mark.asyncio
async def test_browser_bridge_blocks_sensitive_tabs_and_tool_reads_only_bound_session(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = BrowserBridgeStore(workspace)
    code = store.create_ticket("web:browser:owner-a", "web:web:owner-a:session-a")
    with pytest.raises(ValueError, match="Sensitive"):
        store.share(code, 42, "https://example.com/login", "Sign in", "Never capture this")

    shared, _ = _share(store)
    tool = BrowserReadSharedTabTool(workspace)
    tool.set_context("web", "web:owner-a:session-a")
    result = await tool.execute(max_characters=500)
    assert shared.id
    assert tool._owner_id == "web:browser:owner-a"
    assert tool._session_key == "web:web:owner-a:session-a"
    assert "Example documentation" in result
    assert "[redacted]" in result

    tool.set_context("web", "web:owner-b:session-a")
    assert "No browser tab is shared" in await tool.execute()


@pytest.mark.asyncio
async def test_agent_can_read_only_the_tab_paired_to_its_browser_review_session(tmp_path: Path):
    provider = _BrowserRecordingProvider()
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path)
    session = agent.sessions.get_or_create("web:web:owner-a:session-a")
    session.metadata["pico_operation_profile"] = "browser-review"
    agent.sessions.save(session)
    store = BrowserBridgeStore(tmp_path)
    code = store.create_ticket("web:browser:owner-a", "web:web:owner-a:session-a")
    store.share(code, 42, "https://example.com/docs", "Example docs", "Reviewable shared text")

    response = await agent._process_message(
        InboundMessage(channel="web", sender_id="browser:owner-a", chat_id="web:owner-a:session-a", content="Inspect the shared page")
    )

    assert response is not None and response.content == "I inspected the explicitly shared tab."
    assert {item["function"]["name"] for item in provider.calls[0]["tools"]} == {
        "list_skills", "get_skill", "browser_read_shared_tab"
    }
    assert any(
        "Reviewable shared text" in str(message.get("content", ""))
        for message in provider.calls[1]["messages"]
    )
