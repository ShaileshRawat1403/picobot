import asyncio
from pathlib import Path

import pytest

from picobot.agent.loop import AgentLoop
from picobot.agent.tools.browser import BrowserReadSharedTabTool
from picobot.agent.tools.browser_action import BrowserActionTool
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.operations.browser_bridge import BrowserBridgeStore
from picobot.operations.browser_executor import BrowserActionExecutor
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


def test_browser_commands_are_owner_bound_typed_and_token_gated(tmp_path: Path):
    store = BrowserBridgeStore(tmp_path / "workspace")
    shared, token = _share(store)
    command = store.queue_command(
        "web:browser:owner-a",
        "web:web:owner-a:session-a",
        shared.id,
        "click",
        {"selector": "button#continue"},
        "fingerprint-1",
    )

    assert command.status == "queued"
    assert "payload" not in command.to_dict()
    with pytest.raises(ValueError, match="no longer shared"):
        store.claim_next(shared.id, "wrong-token")
    assert store.claim_next(shared.id, token)["payload"] == {"selector": "button#continue"}
    with pytest.raises(ValueError, match="no longer shared"):
        store.complete_command(shared.id, "wrong-token", command.id, success=True, result_summary="done")

    completed = store.complete_command(
        shared.id,
        token,
        command.id,
        success=True,
        result_summary="Click dispatched.",
    )
    assert completed.status == "succeeded"
    assert store.command("web:browser:owner-a", "web:web:owner-a:session-a", command.id).status == "succeeded"
    with pytest.raises(KeyError):
        store.command("web:browser:owner-b", "web:web:owner-b:session-b", command.id)


def test_browser_commands_reject_sensitive_targets_and_inputs(tmp_path: Path):
    store = BrowserBridgeStore(tmp_path / "workspace")
    shared, _ = _share(store)
    common = ("web:browser:owner-a", "web:web:owner-a:session-a", shared.id)
    with pytest.raises(ValueError, match="Sensitive"):
        store.queue_command(*common, "navigate", {"url": "https://example.com/login"}, "fingerprint")
    with pytest.raises(ValueError, match="secret"):
        store.queue_command(
            *common,
            "type",
            {"selector": "#notes", "text": "api_key: sk-example-secret"},
            "fingerprint",
        )


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
async def test_browser_action_tool_only_stages_from_explicit_browser_action_profile(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = BrowserBridgeStore(workspace)
    shared, _ = _share(store)
    tool = BrowserActionTool(workspace)
    tool.set_turn_context(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-review",
    )
    assert "browser-action profile" in await tool.execute(
        operation="click", selector="button#continue", summary="Continue the public workflow"
    )

    tool.set_turn_context(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-action",
        queued_run_id="run-browser-1",
    )
    result = await tool.execute(
        operation="click", selector="button#continue", summary="Continue the public workflow"
    )
    assert "proposed for review" in result
    action = tool.action_store.list("web:browser:owner-a", "web:web:owner-a:session-a")[0]
    assert action.tool_name == "browser_action"
    assert action.profile_id == "browser-action"
    assert action.initiating_run_id == "run-browser-1"
    assert shared.id in (action.payload or "")


@pytest.mark.asyncio
async def test_browser_executor_requires_approval_and_dispatches_only_typed_command(tmp_path: Path):
    workspace = tmp_path / "workspace"
    bridge = BrowserBridgeStore(workspace)
    shared, token = _share(bridge)
    tool = BrowserActionTool(workspace)
    tool.set_turn_context(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-action",
        queued_run_id="run-browser-2",
    )
    await tool.execute(operation="click", selector="button#continue", summary="Continue the public workflow")
    action = tool.action_store.list("web:browser:owner-a", "web:web:owner-a:session-a")[0]
    executor = BrowserActionExecutor(workspace, timeout_s=2)

    blocked = await executor.execute_action(
        "web:browser:owner-a",
        "web:web:owner-a:session-a",
        action.id,
        payload_fingerprint=action.payload_fingerprint,
        profile_id="browser-action",
    )
    assert blocked["failure_category"] == "approval_required"

    approved = tool.action_store.resolve(
        "web:browser:owner-a",
        action.id,
        "web:web:owner-a:session-a",
        "approve",
        payload_fingerprint=action.payload_fingerprint,
    )
    running = asyncio.create_task(
        executor.execute_action(
            "web:browser:owner-a",
            "web:web:owner-a:session-a",
            approved.id,
            payload_fingerprint=approved.payload_fingerprint,
            profile_id="browser-action",
        )
    )
    command = None
    for _ in range(50):
        command = bridge.claim_next(shared.id, token)
        if command:
            break
        await asyncio.sleep(0.02)
    assert command and command["operation"] == "click"
    assert command["tab_id"] == shared.extension_tab_id
    assert "owner_id" not in command and "session_key" not in command
    bridge.complete_command(shared.id, token, command["id"], success=True, result_summary="Click dispatched")
    result = await running
    assert result["status"] == "executed"


@pytest.mark.asyncio
async def test_browser_executor_rechecks_fingerprint_and_revoked_share(tmp_path: Path):
    workspace = tmp_path / "workspace"
    bridge = BrowserBridgeStore(workspace)
    shared, _ = _share(bridge)
    tool = BrowserActionTool(workspace)
    tool.set_turn_context(
        owner_id="web:browser:owner-a",
        session_key="web:web:owner-a:session-a",
        profile_id="browser-action",
    )
    await tool.execute(operation="click", selector="button#continue", summary="Continue the public workflow")
    action = tool.action_store.list("web:browser:owner-a", "web:web:owner-a:session-a")[0]
    tool.action_store.resolve(
        "web:browser:owner-a",
        action.id,
        "web:web:owner-a:session-a",
        "approve",
        payload_fingerprint=action.payload_fingerprint,
    )
    mismatch = await BrowserActionExecutor(workspace, timeout_s=0.1).execute_action(
        "web:browser:owner-a",
        "web:web:owner-a:session-a",
        action.id,
        payload_fingerprint="wrong",
        profile_id="browser-action",
    )
    assert mismatch["failure_category"] == "payload_mismatch"

    await tool.execute(operation="click", selector="button#continue", summary="Continue again")
    second = tool.action_store.list("web:browser:owner-a", "web:web:owner-a:session-a")[0]
    tool.action_store.resolve(
        "web:browser:owner-a",
        second.id,
        "web:web:owner-a:session-a",
        "approve",
        payload_fingerprint=second.payload_fingerprint,
    )
    bridge.revoke("web:browser:owner-a", "web:web:owner-a:session-a")
    revoked = await BrowserActionExecutor(workspace, timeout_s=0.1).execute_action(
        "web:browser:owner-a",
        "web:web:owner-a:session-a",
        second.id,
        payload_fingerprint=second.payload_fingerprint,
        profile_id="browser-action",
    )
    assert revoked["failure_category"] == "browser_precondition"


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
