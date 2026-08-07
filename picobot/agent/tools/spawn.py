"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any

from picobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from picobot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

    def set_context(self, channel: str, chat_id: str, session_key: str | None = None) -> None:
        """Set the origin context for subagent announcements.

        ``session_key`` must be the session's real key.  Deriving one here from
        the channel and chat id produced a different string from the one the
        inbound message carries, so ``cancel_by_session`` never matched and a
        cancelled turn left its subagents running -- and billing -- in the
        background.  The caller owns the key; this tool only records it.
        """
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        if session_key:
            self._session_key = session_key

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        )
