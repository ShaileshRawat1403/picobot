"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from picobot.agent.context import ContextBuilder
from picobot.agent.subagent import SubagentManager
from picobot.artifacts.store import ArtifactStore
from picobot.agent.tools.calendar import CalendarTool
from picobot.agent.tools.browser import BrowserReadSharedTabTool
from picobot.agent.tools.cron import CronTool
from picobot.agent.tools.dax import DaxTool
from picobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from picobot.agent.tools.message import MessageTool
from picobot.agent.tools.registry import ToolRegistry
from picobot.agent.tools.shell import ExecTool
from picobot.agent.tools.spawn import SpawnTool
from picobot.agent.tools.web import WebFetchTool, WebSearchTool
from picobot.bus.analytics import get_analytics
from picobot.bus.dax_queue import get_dax_queue
from picobot.bus.events import InboundMessage, OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.providers.base import LLMProvider
from picobot.operations import CapabilityRegistry, ProposedActionStore, ToolActivityStore
from picobot.runs import RunRecord, RunStore
from picobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from picobot.config.schema import ChannelsConfig, DaxConfig, ExecToolConfig, SkillConfig, WebSearchConfig
    from picobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000
    _MAX_SESSION_HISTORY_MESSAGES = 40

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        dax_config: DaxConfig | None = None,
        skill_config: dict[str, SkillConfig] | None = None,
    ):
        from picobot.config.schema import ExecToolConfig, SkillConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.dax_config = dax_config
        self.skill_config = skill_config or {}

        self.analytics = get_analytics(workspace)
        self.context = ContextBuilder(workspace, skill_config=self.skill_config)
        self.sessions = session_manager or SessionManager(workspace)
        self.capabilities = CapabilityRegistry()
        self.tool_activity = ToolActivityStore(workspace)
        self.proposed_actions = ProposedActionStore(workspace)
        self.artifacts = ArtifactStore(workspace)
        self.runs = RunStore(workspace)
        self._queued_run_ids: dict[int, str] = {}
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self._last_error: dict[str, str] | None = None
        self._dax_service = None
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            )
        )
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(BrowserReadSharedTabTool(workspace=self.workspace))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

        dax_enabled = os.environ.get("DAX_ENABLED", "").lower() == "true"
        if self.dax_config and self.dax_config.enabled:
            dax_enabled = True

        if dax_enabled:
            self._init_dax()

        self.tools.register(CalendarTool())

        from picobot.agent.tools.skills import ListSkillsTool, GetSkillTool
        from picobot.agent.skills import SkillsLoader

        skills_loader = SkillsLoader(self.workspace, skill_config=self.skill_config)
        self.tools.register(ListSkillsTool(skills_loader))
        self.tools.register(GetSkillTool(skills_loader))

    def _init_dax(self) -> None:
        """Initialize DAX polling service and tool."""
        from picobot.bus.dax_service import init_dax_service

        dax_url = self.dax_config.url if self.dax_config else None
        admin_numbers = self.dax_config.admin_numbers if self.dax_config else None
        failure_threshold = self.dax_config.max_consecutive_failures if self.dax_config else 5

        self._dax_service = init_dax_service(
            dax_url=dax_url,
            send_callback=self.bus.publish_outbound,
            admin_numbers=admin_numbers,
            failure_threshold=failure_threshold,
        )

        dax_tool_config = {
            "url": dax_url,
            "admin_numbers": admin_numbers,
        }
        dax_queue = get_dax_queue(self.workspace)
        self.tools.register(DaxTool(config=dax_tool_config, dax_queue=dax_queue))
        logger.info("DAX tool registered with queue")

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from picobot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron", "browser_read_shared_tab"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""

        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'

        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        allowed_tools: set[str] | None = None,
        activity_context: dict[str, str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, Any]]:
        """Run the agent iteration loop."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        response_meta: dict[str, Any] = {}
        total_usage: dict[str, int] = {}
        failed = False

        while iteration < self.max_iterations:
            iteration += 1

            tool_defs = self.tools.get_definitions(allowed_tools)

            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tool_defs,
                model=self.model,
            )

            # Trusted usage only: summed from the provider response and bounded
            # to the three known integer counters. Everything else is ignored.
            for key, value in (response.usage or {}).items():
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}:
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        total_usage[key] = total_usage.get(key, 0) + value

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [tc.to_openai_tool_call() for tc in response.tool_calls]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(
                        tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                    )
                    if activity_context:
                        capability = self.capabilities.capability_for_tool(tool_call.name)
                        outcome = "success"
                        if result.startswith("Error:"):
                            outcome = (
                                "blocked"
                                if "not permitted by this session" in result
                                else "error"
                            )
                        self.tool_activity.record(
                            owner_id=activity_context["owner_id"],
                            session_key=activity_context["session_key"],
                            profile_id=activity_context["profile_id"],
                            capability_id=capability.id if capability else None,
                            tool_name=tool_call.name,
                            risk=capability.risk if capability else "unknown",
                            outcome=outcome,
                        )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                if response.provider_name:
                    response_meta["served_by"] = response.provider_name
                if response.model_name:
                    response_meta["served_model"] = response.model_name
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    failed = True
                    self._last_error = {
                        "provider": response.provider_name or "unknown",
                        "model": response.model_name or self.model,
                        "error": clean or "Unknown error",
                    }
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages,
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        # Internal run-ledger facts, popped by _run_turn before outbound metadata.
        response_meta["_usage"] = total_usage
        response_meta["_failed"] = failed

        return final_content, tools_used, messages, response_meta

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()

        if self._dax_service:
            self._dax_service.start()
            logger.info("DAX polling service started")

        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            cmd = msg.content.strip().lower()
            if cmd == "/stop":
                await self._handle_stop(msg)
            elif cmd == "/restart":
                await self._handle_restart(msg)
            else:
                # A normal chat turn gets a durable queued record before it
                # waits for the processing lock. Commands do not execute the
                # model loop and retain their existing command semantics.
                if not cmd.startswith("/"):
                    try:
                        self._queued_run_ids[id(msg)] = self._queue_run(msg).id
                    except Exception:
                        logger.exception("Could not queue run for session {}", msg.session_key)
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(
                    lambda t, k=msg.session_key, message_id=id(msg): (
                        self._queued_run_ids.pop(message_id, None),
                        self._active_tasks.get(k, []) and self._active_tasks[k].remove(t)
                        if t in self._active_tasks.get(k, [])
                        else None
                    )
                )

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel the current task for this session, never its queued backlog."""
        owner_id = self._run_owner_id(msg)
        try:
            current_run = self.runs.get_active(owner_id, msg.session_key)
        except Exception:
            current_run = None
        tasks = self._active_tasks.get(msg.session_key, [])
        current_task = next((task for task in tasks if not task.done()), None)
        cancelled = int(current_task is not None and current_task.cancel())
        if current_task is not None:
            try:
                await current_task
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key) if current_task else 0
        total = cancelled + sub_cancelled
        cancelled_run = None
        if current_run is not None:
            try:
                latest_run = self.runs.get(owner_id, current_run.id)
                if latest_run.state in {"queued", "running", "waiting_for_approval"}:
                    latest_run = self.runs.cancel(owner_id, latest_run.id)
                if latest_run.state == "cancelled":
                    cancelled_run = latest_run
            except Exception:
                logger.warning("Could not cancel run record for session {}", msg.session_key)
        if cancelled_run:
            content = (
                f"Stopped {total} task(s); run {cancelled_run.id[:8]} cancelled."
            )
        elif total:
            content = f"Stopped {total} task(s)."
        else:
            content = "No active task to stop."
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
            )
        )

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Restarting...",
            )
        )

        async def _do_restart():
            await asyncio.sleep(1)
            # Use -m picobot instead of sys.argv[0] for Windows compatibility
            # (sys.argv[0] may be just "picobot" without full path on Windows)
            os.execv(sys.executable, [sys.executable, "-m", "picobot"] + sys.argv[1:])

        asyncio.create_task(_do_restart())

    async def _handle_resolve_command(
        self, msg: InboundMessage, cmd: str, session
    ) -> OutboundMessage | None:
        parts = cmd.split()
        if len(parts) < 3:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Usage: /resolve approve|deny <run_id> [approval_id]\nOr: /resolve approve|deny (resolves latest)",
            )

        action = parts[1].lower()
        if action not in ("approve", "deny"):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Action must be 'approve' or 'deny'",
            )

        run_id = parts[2] if len(parts) > 2 else ""
        approval_id = parts[3] if len(parts) > 3 else None

        if not self._dax_service:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="DAX is not enabled. Configure DAX to resolve approvals.",
            )

        resolve_action = (
            "resolve_latest_approval" if not (run_id and approval_id) else "resolve_approval"
        )
        tool = self.tools.get("dax")
        if not tool:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="DAX tool not available",
            )

        try:
            if resolve_action == "resolve_latest_approval":
                result = await tool.execute(action=resolve_action, decision=action)
            else:
                result = await tool.execute(
                    action=resolve_action,
                    run_id=run_id,
                    approval_id=approval_id,
                    decision=action,
                )
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"✅ {action.title()} processed: {result}",
                metadata={"approval_resolved": True},
            )
        except Exception as e:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Error resolving approval: {e}",
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        queued_run_id = self._queued_run_ids.pop(id(msg), None)
        owner_id = self._run_owner_id(msg)
        try:
            async with self._processing_lock:
                self.analytics.track("message", channel=msg.channel, model=self.model)
                response = await self._process_message(msg, queued_run_id=queued_run_id)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="",
                            metadata=msg.metadata or {},
                        )
                    )
        except asyncio.CancelledError:
            self._finish_queued_run(owner_id, queued_run_id, cancelled=True)
            logger.info("Task cancelled for session {}", msg.session_key)
            raise
        except Exception:
            self._finish_queued_run(owner_id, queued_run_id, cancelled=False)
            logger.exception("Error processing message for session {}", msg.session_key)
            self.analytics.track("error", channel=msg.channel, model=self.model)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                )
            )

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, Exception):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        if self._dax_service:
            self._dax_service.stop()
        logger.info("Agent loop stopping")

    def _provider_name(self) -> str:
        """Return a bounded label for the effective provider serving a turn."""
        provider = getattr(self.provider, "primary", self.provider)
        for attr in ("name", "provider_name", "display_name"):
            value = getattr(provider, attr, None)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:120]
        return type(provider).__name__

    def _queue_run(self, msg: InboundMessage) -> RunRecord:
        """Persist a queued normal-message turn before it waits for execution."""
        session = self.sessions.get_or_create(self._run_session_key(msg))
        profile = self._session_profile(session)
        allowed_tools = self.capabilities.allowed_tools(profile.id, self.tools.tool_names)
        return self.runs.create(
            owner_id=self._run_owner_id(msg),
            session_key=session.key,
            capability_profile=profile.id,
            policy_revision=self._policy_revision(profile.id, allowed_tools),
            provider=self._provider_name(),
            model=self.model,
        )

    @staticmethod
    def _run_session_key(msg: InboundMessage) -> str:
        """Match scheduled/system turn identity to its eventual agent session."""
        if msg.channel != "system":
            return msg.session_key
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        return f"{channel}:{chat_id}"

    @staticmethod
    def _run_owner_id(msg: InboundMessage) -> str:
        if msg.channel != "system":
            return AgentLoop._owner_id(msg.channel, msg.sender_id)
        channel = msg.chat_id.split(":", 1)[0] if ":" in msg.chat_id else "cli"
        return AgentLoop._owner_id(channel, msg.sender_id)

    def _finish_queued_run(self, owner_id: str, run_id: str | None, *, cancelled: bool) -> None:
        """Terminalize a queued record if setup failed before the model loop."""
        if not run_id:
            return
        try:
            run = self.runs.get(owner_id, run_id)
            if run.state in {"completed", "failed", "cancelled"}:
                return
            if cancelled:
                self.runs.cancel(owner_id, run_id)
            else:
                self.runs.fail(owner_id, run_id, error_summary="The turn stopped before completing.")
        except Exception:
            logger.warning("Could not terminalize queued run {}", run_id[:8])

    @staticmethod
    def _policy_revision(profile_id: str, allowed_tools) -> str:
        """A non-secret signature of the effective profile and tool allow-list."""
        signature = ",".join(sorted(allowed_tools or []))
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        return f"{profile_id}@{digest}"

    @staticmethod
    def _safe_error_summary(value: str | None) -> str:
        """Bound and scrub an error summary so receipts never expose secrets."""
        if not value:
            return "The model returned an error."
        secret_markers = ("api_key", "apikey", "authorization", "bearer ", "sk-", "x-api-key", "token=")
        kept = [
            line
            for line in value.splitlines()
            if not any(marker in line.lower() for marker in secret_markers)
        ]
        cleaned = " ".join(" ".join(kept).split())
        if not cleaned:
            return "The model returned an error."
        return cleaned[:300]

    def _run_evidence_counts(self, owner_id: str, session_key: str, run: RunRecord) -> dict[str, int]:
        """Best-effort observable counts for one run window.

        Only records created at or after the run was queued count. Failures are
        bounded to zero rather than blocking the run's terminal transition.
        """
        approvals = 0
        try:
            approvals = sum(
                1
                for item in self.proposed_actions.list(owner_id, session_key, limit=100)
                if item.created_at >= run.created_at and item.status in {"approved", "executed"}
            )
        except Exception:
            logger.warning("Could not count approvals for run {}", run.id[:8])
        artifacts = 0
        try:
            artifacts = sum(
                1
                for item in self.artifacts.list(owner_id, session_key=session_key, limit=100)
                if item.created_at >= run.created_at
            )
        except Exception:
            logger.warning("Could not count artifacts for run {}", run.id[:8])
        return {"approvals_count": approvals, "artifact_count": artifacts}

    async def _run_turn(
        self,
        *,
        owner_id: str,
        session: Session,
        profile,
        allowed_tools: set[str],
        messages: list[dict],
        activity_context: dict[str, str],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        mission_id: str | None = None,
        result_ref: str | None = None,
        queued_run_id: str | None = None,
    ) -> tuple[str | None, list[dict], dict[str, Any], RunRecord]:
        """Create a queued run, execute one turn, and persist the terminal state."""
        provider = self._provider_name()
        if queued_run_id:
            run = self.runs.get(owner_id, queued_run_id)
        else:
            run = self.runs.create(
                owner_id=owner_id,
                session_key=session.key,
                capability_profile=profile.id,
                policy_revision=self._policy_revision(profile.id, allowed_tools),
                provider=provider,
                model=self.model,
                mission_id=mission_id,
            )
        run = self.runs.mark_running(owner_id, run.id, provider=provider, model=self.model)
        try:
            final_content, tools_used, all_msgs, response_meta = await self._run_agent_loop(
                messages,
                allowed_tools=allowed_tools,
                activity_context=activity_context,
                on_progress=on_progress,
            )
            run_usage = response_meta.pop("_usage", {})
            run_failed = response_meta.pop("_failed", False)
            run_meta = {
                "provider": response_meta.get("served_by") or provider,
                "model": response_meta.get("served_model") or self.model,
                "usage": run_usage,
                "tool_activity_count": len(tools_used),
                "result_ref": result_ref,
                **self._run_evidence_counts(owner_id, session.key, run),
            }
            if run_failed:
                final_content = self._safe_error_summary(final_content)
                run = self.runs.fail(
                    owner_id,
                    run.id,
                    error_summary=self._safe_error_summary(final_content),
                    **run_meta,
                )
            else:
                run = self.runs.complete(owner_id, run.id, **run_meta)
        except asyncio.CancelledError:
            logger.info("Run {} cancelled for session {}", run.id[:8], session.key)
            try:
                self.runs.cancel(owner_id, run.id)
            except Exception:
                logger.warning("Could not cancel run {} after cancellation", run.id[:8])
            raise
        except Exception:
            logger.exception("Run {} failed for session {}", run.id[:8], session.key)
            try:
                self.runs.fail(
                    owner_id, run.id, error_summary="The turn stopped before completing."
                )
            except Exception:
                logger.warning("Could not fail run {} after an internal error", run.id[:8])
            raise
        return final_content, all_msgs, response_meta, run

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        queued_run_id: str | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            profile = self._session_profile(session)
            allowed_tools = self.capabilities.allowed_tools(profile.id, self.tools.tool_names)
            owner_id = self._owner_id(channel, msg.sender_id)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = self._history_for_prompt(session)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                owner_id=owner_id,
                system_prompt=self._context_snapshot(session),
            )
            final_content, all_msgs, _, run = await self._run_turn(
                owner_id=owner_id,
                session=session,
                profile=profile,
                allowed_tools=allowed_tools,
                messages=messages,
                activity_context={
                    "owner_id": owner_id,
                    "session_key": session.key,
                    "profile_id": profile.id,
                },
                queued_run_id=queued_run_id,
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
                metadata={"run_id": run.id, "run_receipt": run.turn_receipt()},
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        owner_id = self._owner_id(msg.channel, msg.sender_id)
        profile = self._session_profile(session)
        allowed_tools = self.capabilities.allowed_tools(profile.id, self.tools.tool_names)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="New session started."
            )
        if cmd.startswith("/remember") or cmd.startswith("/memory"):
            return self._handle_memory_command(msg, owner_id)
        if cmd.startswith("/resolve"):
            return await self._handle_resolve_command(msg, cmd, session)
        if cmd == "/help":
            lines = [
                "🐈 picobot commands:",
                "/new — Start a new conversation",
                "/resolve approve|deny [run_id] [approval_id] — Resolve DAX approval",
                "/status — Show runtime status",
                "/model — Show active model routing",
                "/remember <fact> — Save a personal memory",
                "/memory list|search|why|forget — Manage personal memory",
                "/last_error — Show the most recent LLM error",
                "/stop — Stop the current task",
                "/restart — Restart the bot",
                "/help — Show available commands",
            ]
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(lines),
            )
        if cmd == "/status":
            dax_state = "enabled" if self._dax_service else "disabled"
            lines = [
                "picobot status",
                f"Model: {self.model}",
                f"Workspace: {self.workspace}",
                f"DAX: {dax_state}",
                f"MCP connected: {'yes' if self._mcp_connected else 'no'}",
                f"Inbound queue: {self.bus.inbound_size}",
                f"Outbound queue: {self.bus.outbound_size}",
            ]
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines)
            )
        if cmd.startswith("/model"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1:
                lines = [f"Primary model: {self.model}"]
                fallback_model = getattr(self.provider, "fallback_model", None)
                if fallback_model:
                    lines.append(f"Fallback model: {fallback_model}")
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines)
                )
            else:
                self.model = parts[1].strip()
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"✨ Model updated to: `{self.model}`",
                )

        if cmd.startswith("/system "):
            system_prompt = msg.content[8:].strip()
            soul_path = self.workspace / "SOUL.md"
            try:
                soul_path.write_text(system_prompt, encoding="utf-8")
                session.metadata.pop("pico_system_prompt", None)
                self.sessions.save(session)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="✨ System prompt (SOUL.md) updated successfully. This will take effect on the next message.",
                )
            except Exception as e:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Failed to update system prompt: {e}",
                )
        if cmd == "/last_error":
            if not self._last_error:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="No recent LLM error recorded.",
                )
            lines = [
                "Last LLM error",
                f"Provider: {self._last_error.get('provider', 'unknown')}",
                f"Model: {self._last_error.get('model', 'unknown')}",
                f"Error: {self._last_error.get('error', 'unknown')}",
            ]
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines)
            )
        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = self._history_for_prompt(session)
        recalled_memory = self.context.personal_memory.recall(owner_id, msg.content)
        self.context.personal_memory.record_use(
            owner_id,
            [item.id for item in recalled_memory],
            session_key=session.key,
        )
        self._record_turn_context(session, history, recalled_memory)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            owner_id=owner_id,
            system_prompt=self._context_snapshot(session),
            recalled_memory=recalled_memory,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        message_id = msg.metadata.get("message_id")
        final_content, all_msgs, response_meta, run = await self._run_turn(
            owner_id=owner_id,
            session=session,
            profile=profile,
            allowed_tools=allowed_tools,
            messages=initial_messages,
            activity_context={
                "owner_id": owner_id,
                "session_key": session.key,
                "profile_id": profile.id,
            },
            on_progress=on_progress or _bus_progress,
            result_ref=f"message:{message_id}" if isinstance(message_id, str) else None,
            queued_run_id=queued_run_id,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        meta.update(response_meta)
        meta["run_id"] = run.id
        meta["run_receipt"] = run.turn_receipt()
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    @staticmethod
    def _owner_id(channel: str, sender_id: str) -> str:
        """Scope personal memory to a channel identity, never the chat alone."""
        return f"{channel}:{sender_id or 'anonymous'}"

    def _context_snapshot(self, session: Session) -> str:
        """Keep the system prompt stable for the lifetime of a session."""
        snapshot = session.metadata.get("pico_system_prompt")
        if isinstance(snapshot, str) and snapshot.strip():
            return snapshot
        profile = self._session_profile(session)
        snapshot = self.context.build_system_prompt() + (
            "\n\n# Session capability profile\n\n"
            f"Active profile: {profile.label}. {profile.description}\n"
            "Use only tool definitions available in this session. Do not claim access to other tools."
        )
        session.metadata["pico_system_prompt"] = snapshot
        return snapshot

    def _session_profile(self, session: Session):
        """Resolve a server-owned profile and persist a safe default if needed."""
        profile = self.capabilities.resolve(session.metadata.get("pico_operation_profile"))
        if session.metadata.get("pico_operation_profile") != profile.id:
            session.metadata["pico_operation_profile"] = profile.id
        return profile

    def _history_for_prompt(self, session: Session) -> list[dict[str, Any]]:
        """Bound raw transcript context; durable facts live in personal memory."""
        return session.get_history(max_messages=self._MAX_SESSION_HISTORY_MESSAGES)

    @staticmethod
    def _record_turn_context(session: Session, history: list[dict[str, Any]], memories) -> None:
        """Persist an inspectable record of context used for one model turn.

        Values remain in the memory store. The session records only opaque
        memory ids, the history size, and a timestamp so the Workbench can
        explain context without maintaining a second hidden memory store.
        """
        session.metadata["pico_last_context"] = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "history_message_count": len(history),
            "memory_ids": [item.id for item in memories],
        }

    def _handle_memory_command(self, msg: InboundMessage, owner_id: str) -> OutboundMessage:
        """Handle explicit, user-controlled personal-memory operations."""
        raw = msg.content.strip()
        lower = raw.lower()
        store = self.context.personal_memory

        try:
            if lower == "/remember" or lower == "/memory":
                return self._memory_usage(msg)

            if lower.startswith("/remember "):
                item = store.remember(owner_id, raw[len("/remember "):])
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=(
                        f"Remembered ({item.kind}; id {item.id[:8]}). "
                        "Use `/memory why <id>` to inspect it or `/memory forget <id>` to retire it."
                    ),
                )

            parts = raw.split(maxsplit=2)
            if len(parts) < 2:
                return self._memory_usage(msg)
            action = parts[1].lower()
            argument = parts[2].strip() if len(parts) == 3 else ""

            if action == "propose":
                if not argument:
                    return self._memory_usage(msg)
                item = store.propose(
                    owner_id,
                    argument,
                    source_type="explicit_user_proposal",
                )
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=(
                        f"Learning candidate {item.id[:8]} proposed for review. "
                        f"Use `/memory confirm {item.id[:8]}` to make it recallable."
                    ),
                )

            if action == "list":
                status = argument.lower() or None
                items = store.list(owner_id, status=status, limit=20)
                if not items:
                    return OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content="No personal memories found."
                    )
                lines = ["Personal memory:"]
                lines.extend(
                    f"- [{item.status}] {item.id[:8]} ({item.kind}): {item.value}"
                    for item in items
                )
                return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines))

            if action == "search":
                if not argument:
                    return self._memory_usage(msg)
                items = store.recall(owner_id, argument, limit=8)
                if not items:
                    return OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content="No confirmed memories matched that search."
                    )
                lines = ["Confirmed personal memory:"]
                lines.extend(f"- {item.id[:8]} ({item.kind}): {item.value}" for item in items)
                return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines))

            if action in {"why", "confirm", "reject", "forget"}:
                if not argument:
                    return self._memory_usage(msg)
                item_id = store.resolve_id(owner_id, argument)
                if action == "why":
                    item = store.get(owner_id, item_id)
                    lifecycle = ", ".join(
                        f"{event['event_type']}→{event['status']}" for event in store.history(owner_id, item_id)
                    )
                    lines = [
                        f"Memory {item.id[:8]}",
                        f"Status: {item.status}",
                        f"Kind/scope: {item.kind}/{item.scope}",
                        f"Source: {item.source_type}" + (f" ({item.source_ref})" if item.source_ref else ""),
                        f"Confidence: {item.confidence:.0%}",
                        f"Created: {item.created_at}",
                        f"Lifecycle: {lifecycle}",
                        f"Value: {item.value}",
                    ]
                    return OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines)
                    )
                target_status = {"confirm": "confirmed", "reject": "rejected", "forget": "forgotten"}[action]
                item = store.transition(owner_id, item_id, target_status)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Memory {item.id[:8]} is now {item.status}.",
                )

            return self._memory_usage(msg)
        except (KeyError, ValueError) as exc:
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"Memory: {exc}")

    @staticmethod
    def _memory_usage(msg: InboundMessage) -> OutboundMessage:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=(
                "Memory controls:\n"
                "/remember <fact>\n"
                "/memory propose <candidate>\n"
                "/memory list [confirmed|proposed|rejected|forgotten]\n"
                "/memory search <words>\n"
                "/memory why <id>\n"
                "/memory confirm|reject|forget <id>"
            ),
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if (
                role == "tool"
                and isinstance(content, str)
                and len(content) > self._TOOL_RESULT_MAX_CHARS
            ):
                entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(
                    ContextBuilder._RUNTIME_CONTEXT_TAG
                ):
                    clean_content = ContextBuilder.strip_runtime_context(content)
                    if clean_content.strip():
                        entry["content"] = clean_content
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if (
                            c.get("type") == "text"
                            and isinstance(c.get("text"), str)
                            and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                        ):
                            continue  # Strip runtime context from multimodal messages
                        if c.get("type") == "image_url" and c.get("image_url", {}).get(
                            "url", ""
                        ).startswith("data:image/"):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress
        )
        return response.content if response else ""
