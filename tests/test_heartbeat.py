"""Contract tests for heartbeat spend guards.

The heartbeat decides on its own whether the task file is worth a provider
call.  These tests assert the negative -- that a scaffolded HEARTBEAT.md never
reaches the model -- because a path that should not spend must be proven not to
spend, not merely believed not to.
"""

import asyncio
from typing import Any

from picobot.heartbeat.service import HeartbeatService
from picobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from picobot.utils.helpers import sync_workspace_templates


class RecordingProvider(LLMProvider):
    """Counts every chat call; the test decides what it returns."""

    def __init__(self, response: LLMResponse):
        super().__init__()
        self.response = response
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
        return self.response

    def get_default_model(self) -> str:
        return "test-model"


def _skip_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="1", name="heartbeat", arguments={"action": "skip"})],
    )


def _run_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(id="1", name="heartbeat", arguments={"action": "run", "tasks": "Do the thing"})
        ],
    )


def _seeded_workspace(tmp_path, content: str | None = None):
    """Create a workspace with the shipped template, optionally overridden."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sync_workspace_templates(workspace)
    if content is not None:
        (workspace / "HEARTBEAT.md").write_text(content, encoding="utf-8")
    return workspace


def test_untouched_shipped_template_has_no_actionable_tasks(tmp_path):
    workspace = _seeded_workspace(tmp_path)
    template = (workspace / "HEARTBEAT.md").read_text(encoding="utf-8")
    assert HeartbeatService._has_actionable_tasks(template) is False


def test_untouched_template_never_reaches_the_provider(tmp_path):
    workspace = _seeded_workspace(tmp_path)
    provider = RecordingProvider(_skip_response())
    service = HeartbeatService(workspace, provider, "test-model")

    asyncio.run(service._tick())

    assert provider.calls == 0


def test_bulleted_task_reaches_the_provider(tmp_path):
    workspace = _seeded_workspace(tmp_path, "- Ship the onboarding flow\n")
    provider = RecordingProvider(_skip_response())
    service = HeartbeatService(workspace, provider, "test-model")

    asyncio.run(service._tick())

    assert provider.calls == 1


def test_run_decision_executes_and_notifies(tmp_path):
    workspace = _seeded_workspace(tmp_path, "- Ship the onboarding flow\n")
    provider = RecordingProvider(_run_response())
    executed: list[str] = []
    notified: list[str] = []

    async def on_execute(tasks: str) -> str:
        executed.append(tasks)
        return "Delivered"

    async def on_notify(result: str) -> None:
        notified.append(result)

    service = HeartbeatService(
        workspace, provider, "test-model", on_execute=on_execute, on_notify=on_notify
    )

    asyncio.run(service._tick())

    assert provider.calls == 1
    assert executed == ["Do the thing"]
    assert notified == ["Delivered"]


def test_bulleted_task_is_actionable():
    assert HeartbeatService._has_actionable_tasks("- Ship the onboarding flow") is True


def test_unchecked_checkbox_is_actionable():
    assert HeartbeatService._has_actionable_tasks("- [ ] Draft the brief") is True
    assert HeartbeatService._has_actionable_tasks("* [ ] Draft the brief") is True


def test_checked_checkbox_is_not_actionable():
    assert HeartbeatService._has_actionable_tasks("- [x] Draft the brief") is False
    assert HeartbeatService._has_actionable_tasks("- [X] Draft the brief") is False


def test_empty_bullet_is_not_actionable():
    assert HeartbeatService._has_actionable_tasks("-   ") is False
    assert HeartbeatService._has_actionable_tasks("-\n") is False


def test_empty_checkbox_is_not_actionable():
    assert HeartbeatService._has_actionable_tasks("- [ ]") is False
    assert HeartbeatService._has_actionable_tasks("- [x]") is False


def test_task_inside_html_comment_is_not_actionable():
    content = "- [ ] Visible task\n<!-- - [ ] Hidden task inside a comment -->\n"
    assert HeartbeatService._has_actionable_tasks(content) is True
    commented = "<!-- - [ ] Only a hidden task -->\n"
    assert HeartbeatService._has_actionable_tasks(commented) is False


def test_prose_alone_is_not_actionable():
    prose = (
        "This file is checked every 30 minutes by your picobot agent.\n"
        "Add tasks below that you want the agent to work on periodically.\n"
    )
    assert HeartbeatService._has_actionable_tasks(prose) is False
