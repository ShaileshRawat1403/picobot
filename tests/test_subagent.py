"""Contract tests for subagent spend controls.

Each spawned child runs its own model loop, so the ceiling is on parallel
spend, not just on processes: at most ``MAX_CONCURRENT_SUBAGENTS`` children,
a wall-clock cap per child, and cancellation keyed by the session's real key
so a stopped turn actually stops its billing.
"""

import asyncio
from typing import Any

from picobot.agent.subagent import SubagentManager
from picobot.agent.tools.spawn import SpawnTool
from picobot.bus.queue import MessageBus
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class BlockingProvider(LLMProvider):
    """Never answers; used to keep children alive so concurrency is observable."""

    def __init__(self):
        super().__init__()
        self.release: asyncio.Event | None = None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        assert self.release is not None
        await self.release.wait()
        return LLMResponse(content="done", finish_reason="stop")

    def get_default_model(self) -> str:
        return "test-model"


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def test_fourth_concurrent_spawn_is_refused(tmp_path):
    async def scenario():
        provider = BlockingProvider()
        provider.release = asyncio.Event()
        manager = SubagentManager(provider, tmp_path, MessageBus())
        started = [
            await manager.spawn("task", label=f"child-{i}", session_key="web:abc123")
            for i in range(3)
        ]
        assert all("started" in result for result in started)
        assert manager.get_running_count() == 3

        refused = await manager.spawn("fourth task", label="child-4", session_key="web:abc123")

        assert refused.startswith("Not started:")
        assert "limit 3" in refused
        assert manager.get_running_count() == 3

        await manager.cancel_by_session("web:abc123")

    asyncio.run(scenario())


def test_cancel_by_session_cancels_only_that_sessions_children(tmp_path):
    async def scenario():
        provider = BlockingProvider()
        provider.release = asyncio.Event()
        manager = SubagentManager(provider, tmp_path, MessageBus())
        await manager.spawn("task A", session_key="web:aaa")
        await manager.spawn("task B", session_key="web:bbb")
        assert manager.get_running_count() == 2

        cancelled = await manager.cancel_by_session("web:aaa")
        assert cancelled == 1
        await _wait_until(lambda: manager.get_running_count() == 1)
        assert provider.release.is_set() is False  # the other child was not touched

        cancelled = await manager.cancel_by_session("web:bbb")
        assert cancelled == 1
        await _wait_until(lambda: manager.get_running_count() == 0)

    asyncio.run(scenario())


def test_cancel_propagates_cancelled_error_and_announces_nothing(tmp_path):
    async def scenario():
        provider = BlockingProvider()
        provider.release = asyncio.Event()
        bus = MessageBus()
        manager = SubagentManager(provider, tmp_path, bus)
        await manager.spawn("blocking task", session_key="web:abc123")
        child = next(iter(manager._running_tasks.values()))
        assert child.done() is False

        cancelled = await manager.cancel_by_session("web:abc123")

        assert cancelled == 1
        assert child.cancelled()
        # A swallowed CancelledError would have announced an error result.
        assert bus.inbound_size == 0
        await _wait_until(lambda: manager.get_running_count() == 0)

    asyncio.run(scenario())


def test_child_that_exceeds_its_time_limit_stops_and_announces(tmp_path, monkeypatch):
    async def scenario():
        class SlowProvider(LLMProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            async def chat(
                self,
                messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None,
                model: str | None = None,
                max_tokens: int = 4096,
                temperature: float = 0.7,
                reasoning_effort: str | None = None,
                tool_choice: str | dict[str, Any] | None = None,
            ) -> LLMResponse:
                self.calls += 1
                await asyncio.sleep(0.02)
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallRequest(id="1", name="list_dir", arguments={"path": str(tmp_path)})
                    ],
                )

            def get_default_model(self) -> str:
                return "test-model"

        monkeypatch.setattr(SubagentManager, "_TIMEOUT_SECONDS", 0.12)
        monkeypatch.setattr(SubagentManager, "_MAX_ITERATIONS", 100)
        provider = SlowProvider()
        bus = MessageBus()
        manager = SubagentManager(provider, tmp_path, bus)
        await manager.spawn("never finishing", session_key="web:abc123")

        await _wait_until(lambda: manager.get_running_count() == 0, timeout=5.0)

        announced = await bus.consume_inbound()
        assert "reached its time limit" in announced.content
        assert announced.sender_id == "subagent"

    asyncio.run(scenario())


def test_spawn_tool_records_the_session_key_it_is_given():
    captured: dict = {}

    class FakeManager:
        async def spawn(self, **kwargs):
            captured.update(kwargs)
            return "started"

    tool = SpawnTool(FakeManager())
    assert tool._session_key == "cli:direct"

    tool.set_context("web", "session_identity_0001", "web:session_identity_0001")
    asyncio.run(tool.execute("do the thing"))

    assert tool._session_key == "web:session_identity_0001"
    assert captured["session_key"] == "web:session_identity_0001"
    assert captured["origin_channel"] == "web"
    assert captured["origin_chat_id"] == "session_identity_0001"

    # A later context call without a key must not re-derive one.
    tool.set_context("cli", "direct")
    assert tool._session_key == "web:session_identity_0001"
