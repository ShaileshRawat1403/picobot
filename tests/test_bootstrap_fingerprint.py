from __future__ import annotations

from picobot.agent.loop import AgentLoop
from picobot.bus.queue import MessageBus
from picobot.providers.base import LLMProvider, LLMResponse


class _NoopProvider(LLMProvider):
    async def chat(self, messages, tools=None, model=None, **kwargs):
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "test-model"


def test_bootstrap_file_edit_mid_session_rebuilds_cached_system_prompt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap = workspace / "AGENTS.md"
    bootstrap.write_text("First instruction set.\n", encoding="utf-8")

    agent = AgentLoop(bus=MessageBus(), provider=_NoopProvider(), workspace=workspace)
    session = agent.sessions.get_or_create("telegram:chat-1")

    first = agent._context_snapshot(session)
    assert "First instruction set." in first
    first_fingerprint = session.metadata["pico_bootstrap_fingerprint"]
    assert first_fingerprint == agent.context.bootstrap_fingerprint()

    assert agent._context_snapshot(session) == first
    assert session.metadata["pico_bootstrap_fingerprint"] == first_fingerprint

    bootstrap.write_text(
        "A much longer replacement instruction set that changes size and content.\n",
        encoding="utf-8",
    )
    rebuilt = agent._context_snapshot(session)
    assert "First instruction set." not in rebuilt
    assert "A much longer replacement instruction set" in rebuilt
    assert session.metadata["pico_bootstrap_fingerprint"] != first_fingerprint
