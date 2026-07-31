"""Tests for CH4 Governed Skills and MCP Registry."""

import asyncio
from pathlib import Path
import pytest

from picobot.agent.loop import AgentLoop
from picobot.agent.tools.base import Tool
from picobot.agent.tools.registry import ToolRegistry
from picobot.bus.queue import MessageBus
from picobot.config.schema import MCPServerConfig
from picobot.operations import CapabilityRegistry, GovernedRegistryStore, ProposedActionStore, ToolActivityStore
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class DummyProvider(LLMProvider):
    name = "dummy"

    def __init__(self, response_content: str = "Hello", tool_calls: list[ToolCallRequest] | None = None):
        super().__init__()
        self.response_content = response_content
        self._tool_calls = tool_calls or []

    async def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        if self._tool_calls:
            calls = self._tool_calls
            self._tool_calls = []  # clear so it doesn't loop forever
            return LLMResponse(content=None, tool_calls=calls)
        return LLMResponse(content=self.response_content)

    def get_default_model(self) -> str:
        return "dummy-model"


class DummyReadTool(Tool):
    name = "mcp_server1_read_stuff"
    description = "Read data"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return "read result"


class DummyMutatingTool(Tool):
    name = "mcp_server1_mutate_stuff"
    description = "Mutate data"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return "mutate result"


def test_disabled_skill_or_server_never_enters_tool_definitions(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    # Register an MCP server that is disabled
    mcp_config = {"server1": MCPServerConfig(command="echo", enabled=False)}
    store.sync_inventory(mcp_servers_config=mcp_config)

    entry = store.get("mcp:server1")
    assert entry is not None
    assert entry.enabled is False
    assert entry.readiness == "disabled"

    # Add tool to registry
    tools = ToolRegistry()
    tools.register(DummyReadTool())
    store.update_entry("mcp:server1", discovered_tools=["mcp_server1_read_stuff"])

    capabilities = CapabilityRegistry()
    allowed = capabilities.allowed_tools("research", tools.tool_names, governed_registry=store)
    assert "mcp_server1_read_stuff" not in allowed


def test_untested_and_failed_mcp_entries_never_enter_tool_definitions(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    mcp_config = {
        "untested_server": MCPServerConfig(command="echo", enabled=True),
        "failed_server": MCPServerConfig(command="echo", enabled=True),
    }
    store.sync_inventory(mcp_servers_config=mcp_config)

    # Set discovered tools for both
    store.update_entry("mcp:untested_server", readiness="untested", discovered_tools=["mcp_untested_server_tool"])
    store.update_entry("mcp:failed_server", readiness="failed", safe_diagnostic="Connection refused", discovered_tools=["mcp_failed_server_tool"])

    tools = ToolRegistry()
    capabilities = CapabilityRegistry()

    # Neither should enter tool definitions
    allowed = capabilities.allowed_tools("research", ["mcp_untested_server_tool", "mcp_failed_server_tool"], governed_registry=store)
    assert "mcp_untested_server_tool" not in allowed
    assert "mcp_failed_server_tool" not in allowed


def test_profile_forbidden_tool_absent_even_when_enabled_and_ready(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    mcp_config = {"ready_server": MCPServerConfig(command="echo", enabled=True)}
    store.sync_inventory(mcp_servers_config=mcp_config)

    # Server is ready, allowed for research profile only
    store.update_entry(
        "mcp:ready_server",
        readiness="ready",
        discovered_tools=["mcp_ready_server_tool"],
        allowed_profiles=["research"],
    )

    capabilities = CapabilityRegistry()
    # When active profile is 'personal-work', tool must be absent
    allowed_personal = capabilities.allowed_tools("personal-work", ["mcp_ready_server_tool"], governed_registry=store)
    assert "mcp_ready_server_tool" not in allowed_personal

    # When active profile is 'research', tool should be allowed
    allowed_research = capabilities.allowed_tools("research", ["mcp_ready_server_tool"], governed_registry=store)
    assert "mcp_ready_server_tool" in allowed_research


def test_read_only_configured_tool_exposed_only_when_all_gates_pass(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    mcp_config = {"read_server": MCPServerConfig(command="echo", enabled=True)}
    store.sync_inventory(mcp_servers_config=mcp_config)

    store.update_entry(
        "mcp:read_server",
        enabled=True,
        readiness="ready",
        risk="read",
        discovered_tools=["mcp_read_server_tool"],
        allowed_profiles=["research"],
    )

    capabilities = CapabilityRegistry()
    allowed = capabilities.allowed_tools("research", ["mcp_read_server_tool"], governed_registry=store)
    assert "mcp_read_server_tool" in allowed

    # The fourth gate is a capability entitlement in the actual profile, not
    # an implicit MCP-server bypass.  Personal work has no MCP entitlement.
    assert "mcp_read_server_tool" not in capabilities.allowed_tools(
        "personal-work", ["mcp_read_server_tool"], governed_registry=store
    )


@pytest.mark.asyncio
async def test_mutating_mcp_tool_is_not_model_callable_even_with_an_approval(tmp_path: Path):
    bus = MessageBus()
    provider = DummyProvider(
        tool_calls=[ToolCallRequest(id="tc1", name="mcp_server1_mutate_stuff", arguments={})]
    )
    agent = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
    agent.tools.register(DummyMutatingTool())

    # Register mcp:server1 as mutating and ready
    agent.governed_registry.sync_inventory(
        mcp_servers_config={"server1": MCPServerConfig(command="echo", risk="mutating")}
    )
    agent.governed_registry.update_entry(
        "mcp:server1",
        enabled=True,
        readiness="ready",
        risk="mutating",
        discovered_tools=["mcp_server1_mutate_stuff"],
        allowed_profiles=["personal-work", "research"],
    )

    # No current profile contains mutation authority, so this never enters a
    # model-visible tool definition.  An approval alone must not widen that
    # authority; a future mutation profile must also bind a proposal to the
    # requested action before an executor can exist.
    assert "mcp_server1_mutate_stuff" not in agent.capabilities.allowed_tools(
        "research", agent.tools.tool_names, governed_registry=agent.governed_registry
    )

    action = agent.proposed_actions.stage(
        owner_id="owner1",
        session_key="sess1",
        profile_id="research",
        capability_id="mcp:server1",
        tool_name="mcp_server1_mutate_stuff",
        target="target1",
        summary="mutate stuff",
    )
    agent.proposed_actions.resolve("owner1", action.id, "sess1", "approve")

    # Defense in depth: even a lower-level caller passing a forged allow-list
    # cannot bypass the registry/profile check at execution time.
    _res_content, _tools_used, _messages, _response_meta = await agent._run_agent_loop(
        initial_messages=[{"role": "user", "content": "do mutation"}],
        allowed_tools={"mcp_server1_mutate_stuff"},
        activity_context={"owner_id": "owner1", "session_key": "sess1", "profile_id": "research"},
        serving_provider=provider,
    )
    activities = agent.tool_activity.list("owner1", "sess1")
    assert len(activities) == 1
    assert activities[0].outcome == "blocked"
    assert activities[0].risk == "mutating"


@pytest.mark.asyncio
async def test_bounded_probe_timeout_and_safe_error_handling(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    store.sync_inventory(mcp_servers_config={"slow_server": MCPServerConfig(command="sleep 10")})

    async def slow_probe(entry_id: str):
        await asyncio.sleep(10)
        return True, "Connected", ["tool1"]

    # Probe with 0.1s timeout
    entry = await store.probe("mcp:slow_server", probe_func=slow_probe, timeout=0.1)
    assert entry.readiness == "failed"
    assert "timed out" in entry.safe_diagnostic.lower()

    # Exception probe returning secret error
    async def secret_error_probe(entry_id: str):
        raise ValueError("Secret key sk-1234567890abcdef failed auth at /tmp/secret")

    entry2 = await store.probe("mcp:slow_server", probe_func=secret_error_probe, timeout=1.0)
    assert entry2.readiness == "failed"
    assert "sk-1234567890abcdef" not in entry2.safe_diagnostic
    assert "/tmp/secret" not in entry2.safe_diagnostic
    assert "[REDACTED]" in entry2.safe_diagnostic


def test_safe_public_registry_response_contains_no_secrets_or_arguments(tmp_path: Path):
    store = GovernedRegistryStore(tmp_path)
    store.sync_inventory(mcp_servers_config={"test_server": MCPServerConfig(command="echo")})
    store.update_entry(
        "mcp:test_server",
        safe_diagnostic="Auth header bearer token=secret_token_123",
        discovered_tools=["mcp_test_server_run"],
    )

    entry = store.get("mcp:test_server")
    data = entry.to_dict()

    # Must contain safe metadata only
    assert data["id"] == "mcp:test_server"
    assert "secret_token_123" not in data["safe_diagnostic"]
    assert "[REDACTED]" in data["safe_diagnostic"]
    assert data["discovered_tools"] == ["mcp_test_server_run"]
    assert "api_key" not in data
    assert "arguments" not in data
