"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
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
from picobot.agent.tools.browser_action import BrowserActionTool
from picobot.agent.tools.cron import CronTool
from picobot.agent.tools.dax import DaxTool
from picobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from picobot.agent.tools.github import GitHubPullRequestTool
from picobot.agent.tools.message import MessageTool
from picobot.agent.tools.registry import ToolRegistry
from picobot.agent.tools.shell import ExecTool
from picobot.agent.tools.spawn import SpawnTool
from picobot.agent.tools.web import WebFetchTool, WebSearchTool
from picobot.agent.tools.workspace_change import ProposeWorkspaceChangeTool
from picobot.bus.analytics import get_analytics
from picobot.bus.dax_queue import get_dax_queue
from picobot.bus.events import InboundMessage, OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.context.compactor import CompactionService, ProviderContextSummarizer
from picobot.context.planner import estimate_tokens
from picobot.missions import MissionStore
from picobot.providers.base import LLMProvider
from picobot.operations import CapabilityRegistry, GovernedRegistryStore, ProposedActionStore, ToolActivityStore
from picobot.runs import RunRecord, RunStore
from picobot.session.manager import Session, SessionManager
from picobot.tasks import TaskRecord, TaskStore

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
        runtime_policy_service=None,
        provider_factory=None,
        compaction: CompactionService | None = None,
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
        self.runtime_policy_service = runtime_policy_service
        self.provider_factory = provider_factory
        self.compaction = compaction or CompactionService(workspace)
        self._serving_providers: dict[str, LLMProvider] = {}
        self._queued_policy_snapshots: dict[str, Any] = {}

        self.analytics = get_analytics(workspace)
        self.context = ContextBuilder(workspace, skill_config=self.skill_config)
        self.sessions = session_manager or SessionManager(workspace)
        self.capabilities = CapabilityRegistry()
        self.governed_registry = GovernedRegistryStore(workspace)
        self.tool_activity = ToolActivityStore(workspace)
        self.proposed_actions = ProposedActionStore(workspace)
        self.artifacts = ArtifactStore(workspace)
        self.runs = RunStore(workspace)
        self.missions = MissionStore(workspace)
        self.tasks = TaskStore(workspace)
        self.tools = ToolRegistry()
        self._active_async_tasks: dict[str, asyncio.Task[Any]] = {}

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
        self.tools.register(GitHubPullRequestTool())
        self.tools.register(BrowserReadSharedTabTool(workspace=self.workspace))
        self.tools.register(
            BrowserActionTool(
                workspace=self.workspace,
                action_store=self.proposed_actions,
            )
        )
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
        from picobot.agent.tools.mission_artifact import SaveMissionArtifactDraftTool

        skills_loader = SkillsLoader(self.workspace, skill_config=self.skill_config)
        self.tools.register(ListSkillsTool(skills_loader))
        self.tools.register(GetSkillTool(skills_loader))
        self.tools.register(
            SaveMissionArtifactDraftTool(
                workspace=self.workspace,
                action_store=self.proposed_actions,
                mission_store=self.missions,
            )
        )
        self.tools.register(
            ProposeWorkspaceChangeTool(
                workspace=self.workspace,
                action_store=self.proposed_actions,
            )
        )
        self.governed_registry.sync_inventory(skills_loader, self._mcp_servers)

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

    def cancel_task_run(self, task_id: str) -> None:
        """Cancel active asyncio task associated with a running task."""
        if async_task := self._active_async_tasks.get(task_id):
            if not async_task.done():
                async_task.cancel()
            self._active_async_tasks.pop(task_id, None)

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from picobot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(
                self._mcp_servers, self.tools, self._mcp_stack, governed_registry=self.governed_registry
            )
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


    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        *,
        owner_id: str | None = None,
        session_key: str | None = None,
        profile_id: str = "personal-work",
        queued_run_id: str | None = None,
        mission_id: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info."""
        for name in (
            "message",
            "spawn",
            "cron",
            "browser_read_shared_tab",
            "browser_action",
            "save_mission_artifact_draft",
            "propose_workspace_change",
        ):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))
                if hasattr(tool, "set_turn_context") and owner_id and session_key:
                    tool.set_turn_context(
                        owner_id=owner_id,
                        session_key=session_key,
                        profile_id=profile_id,
                        queued_run_id=queued_run_id,
                        mission_id=mission_id,
                    )

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
        serving_provider: LLMProvider | None = None,
        serving_model: str | None = None,
        reasoning_effort: str | None = None,
        max_turns: int | None = None,
    ) -> tuple[str | None, list[str], list[dict], dict[str, Any]]:
        """Run the agent iteration loop."""
        provider = serving_provider or self.provider
        model = serving_model or self.model
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        response_meta: dict[str, Any] = {}
        total_usage: dict[str, int] = {}
        failed = False

        effective_max = min(self.max_iterations, max_turns) if max_turns and max_turns > 0 else self.max_iterations

        while iteration < effective_max:
            iteration += 1

            tool_defs = self.tools.get_definitions(allowed_tools)

            chat_kwargs = {"messages": messages, "tools": tool_defs, "model": model}
            if reasoning_effort is not None:
                chat_kwargs["reasoning_effort"] = reasoning_effort
            response = await provider.chat_with_retry(**chat_kwargs)

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

                    capability = self.capabilities.capability_for_tool(tool_call.name)
                    governed_entry = self.governed_registry.get_entry_for_tool(tool_call.name)
                    tool_risk = governed_entry.risk if governed_entry else (capability.risk if capability else "read")
                    capability_id = capability.id if capability else (governed_entry.id if governed_entry else None)

                    # ``allowed_tools`` is calculated at submission time, but
                    # re-check the durable profile/registry boundary at
                    # execution time.  This prevents a future caller from
                    # passing an arbitrary allow-list to this lower-level
                    # loop and making a governed tool callable.
                    is_permitted = tool_call.name in (allowed_tools or set())
                    if activity_context:
                        profile = self.capabilities.resolve(activity_context.get("profile_id"))
                        is_permitted = is_permitted and self.governed_registry.is_tool_allowed(
                            tool_call.name,
                            profile.id,
                            profile.tool_names,
                        )

                    if not is_permitted:
                        result = "Error: Tool is not permitted by this session"
                    elif (
                        activity_context
                        and activity_context.get("profile_id") == "workspace-inspect"
                        and tool_call.name in {"read_file", "list_dir"}
                    ):
                        result = self._workspace_inspect_guard(tool_call.arguments)
                        if result is None:
                            result = await self.tools.execute(
                                tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                            )
                    elif (
                        activity_context
                        and activity_context.get("profile_id") == "workspace-run"
                        and tool_call.name == "exec"
                    ):
                        result = self._workspace_run_guard(tool_call.arguments)
                        if result is None:
                            result = await self.tools.execute(
                                tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                            )
                    elif tool_risk == "mutating":
                        if tool_call.name == "save_mission_artifact_draft":
                            result = await self.tools.execute(
                                tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                            )
                        else:
                            is_approved = False
                            if activity_context:
                                actions = self.proposed_actions.list(
                                    activity_context["owner_id"], activity_context["session_key"]
                                )
                                is_approved = any(a.tool_name == tool_call.name and a.status == "approved" for a in actions)
                            if not is_approved:
                                result = "Error: Action requires owner approval in Operations before execution"
                            else:
                                result = await self.tools.execute(
                                    tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                                )
                    else:
                        result = await self.tools.execute(
                            tool_call.name, tool_call.arguments, allowed_names=allowed_tools
                        )

                    if activity_context:
                        outcome = "success"
                        if result.startswith("Error:"):
                            outcome = (
                                "blocked"
                                if "not permitted by this session" in result or "requires owner approval" in result
                                else "error"
                            )
                        self.tool_activity.record(
                            owner_id=activity_context["owner_id"],
                            session_key=activity_context["session_key"],
                            profile_id=activity_context["profile_id"],
                            capability_id=capability_id,
                            tool_name=tool_call.name,
                            risk=tool_risk,
                            outcome=outcome,
                            run_id=activity_context.get("run_id"),
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
                        "model": response.model_name or model,
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

        if final_content is None and iteration >= effective_max:
            logger.warning("Max iterations ({}) reached", effective_max)
            failed = True
            response_meta["_budget_exhausted"] = True
            final_content = (
                f"I reached the maximum number of tool call iterations ({effective_max}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        # Internal run-ledger facts, popped by _run_turn before outbound metadata.
        response_meta["_usage"] = total_usage
        response_meta["_failed"] = failed

        return final_content, tools_used, messages, response_meta

    def _workspace_inspect_guard(self, arguments: Any) -> str | None:
        """Keep the read-only workspace profile inside Pico's configured root."""
        if not isinstance(arguments, dict):
            return "Error: Workspace inspection parameters must be an object"
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "Error: Workspace inspection requires a path"
        try:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = self.workspace / candidate
            resolved = candidate.resolve()
            resolved.relative_to(self.workspace.resolve())
        except (OSError, ValueError):
            return "Error: Workspace inspection is limited to Pico's configured workspace"
        return None

    def _workspace_run_guard(self, arguments: Any) -> str | None:
        """Allow only bounded, read-only diagnostics in ``workspace-run``."""
        if not isinstance(arguments, dict):
            return "Error: Workspace diagnostics parameters must be an object"
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return "Error: Workspace diagnostics requires a command"
        command = command.strip()
        if len(command) > 2_000:
            return "Error: Workspace diagnostic commands are limited to 2000 characters"
        if re.search(r"(?:;|&&|\|\||[<>`]|\$\(|\b(?:curl|wget|ssh|scp|rsync|nc|ping|docker|podman|npm|pip|pip3|brew|apt|apt-get|yum|dnf|cargo|go|make|cmake)\b)", command, re.IGNORECASE):
            return "Error: Workspace diagnostics allow read-only local commands only"
        if ".." in command:
            return "Error: Workspace diagnostics reject path traversal"

        try:
            tokens = shlex.split(command)
        except ValueError:
            return "Error: Workspace diagnostic command has invalid quoting"
        if not tokens:
            return "Error: Workspace diagnostics requires a command"
        base = os.path.basename(tokens[0]).lower()
        allowed = {
            "cat", "head", "tail", "less", "more", "grep", "rg", "find", "ls", "dir",
            "stat", "file", "wc", "du", "df", "tree", "awk", "sed", "cut", "sort",
            "uniq", "tr", "jq", "yq", "ps", "uptime", "uname", "hostname", "whoami",
            "id", "which", "whereis", "type", "command", "date", "echo", "printf", "seq",
            "git",
        }
        if base not in allowed:
            return f"Error: Workspace diagnostic command '{base}' is not permitted"
        lowered_tokens = {token.lower() for token in tokens[1:]}
        if lowered_tokens & {"-exec", "-execdir", "-delete", "-i", "--output", "--upload-pack"}:
            return "Error: Workspace diagnostics reject mutating command options"
        if base == "git":
            subcommand = next((token for token in tokens[1:] if not token.startswith("-")), "")
            if subcommand not in {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files"}:
                return "Error: Workspace diagnostics allow read-only git inspection only"

        working_dir = arguments.get("working_dir")
        try:
            candidate = Path(working_dir).expanduser() if isinstance(working_dir, str) and working_dir else self.workspace
            if not candidate.is_absolute():
                candidate = self.workspace / candidate
            candidate.resolve().relative_to(self.workspace.resolve())
            for raw_path in ExecTool._extract_absolute_paths(command):
                Path(os.path.expanduser(raw_path)).resolve().relative_to(self.workspace.resolve())
        except (OSError, ValueError):
            return "Error: Workspace diagnostics are limited to Pico's configured workspace"
        return None

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

    @staticmethod
    def _provider_label(provider: LLMProvider) -> str:
        """Return a bounded label for any provider instance."""
        provider = getattr(provider, "primary", provider)
        for attr in ("name", "provider_name", "display_name"):
            value = getattr(provider, attr, None)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:120]
        return type(provider).__name__

    def _provider_name(self) -> str:
        """Return a bounded label for the effective provider serving a turn."""
        return self._provider_label(self.provider)

    def _effective_policy(self, session: Session):
        """Resolve the durable runtime policy for one session, or None when disabled."""
        service = self.runtime_policy_service
        if service is None:
            return None
        try:
            return service.resolve_effective(
                session.metadata,
                fallback_model=self.model,
                fallback_provider=self._provider_name(),
            )
        except Exception:
            logger.warning("Runtime policy resolution failed; using process defaults")
            return None

    def _policy_for_submitted_turn(self, session: Session, queued_run_id: str | None):
        """Use the submission snapshot when a queued turn reaches the runner."""
        if queued_run_id:
            snapshot = self._queued_policy_snapshots.get(queued_run_id)
            if snapshot is not None:
                return snapshot
        return self._effective_policy(session)

    def _serving_resources(self, session: Session, *, policy=None):
        """Resolve (provider, model, reasoning_effort, policy) for one turn.

        The durable policy wins when it selects a provider/model; otherwise the
        loop's startup provider and model serve the turn. A provider selected by
        policy is built lazily through ``provider_factory`` and cached.
        """
        if policy is None:
            policy = self._effective_policy(session)
        if policy is None or policy.source == "default":
            return self.provider, self.model, None, None
        serving = self.provider
        model = policy.model or self.model
        if policy.provider and policy.provider != self._provider_name():
            if self.provider_factory is not None:
                serving = self._serving_providers.get(policy.provider)
                if serving is None:
                    serving = self.provider_factory(policy.provider, model)
                    self._serving_providers[policy.provider] = serving
            else:
                logger.warning(
                    "Policy selects provider {} but no provider factory is available; serving with {}",
                    policy.provider,
                    self._provider_name(),
                )
        return serving, model, policy.reasoning_effort, policy

    def _active_mission_for_session(self, session: Session, owner_id: str):
        """Return the active mission selected for a session, or None."""
        mission_id = session.metadata.get("pico_active_mission_id")
        if not mission_id or not isinstance(mission_id, str):
            return None
        try:
            mission = self.missions.get(owner_id, mission_id)
            if mission.session_key == session.key and mission.state == "active":
                return mission
        except KeyError:
            pass
        return None

    def _mission_for_turn(
        self, session: Session, owner_id: str, queued_run_id: str | None
    ):
        """Resolve the mission context from the run submission snapshot.

        A queued turn must not silently inherit a mission selected later in the
        same chat.  The run ledger is the source of truth once a run exists;
        direct turns still read the current server-owned selection.
        """
        if not queued_run_id:
            return self._active_mission_for_session(session, owner_id)
        try:
            run = self.runs.get(owner_id, queued_run_id)
            if not run.mission_id or run.session_key != session.key:
                return None
            mission = self.missions.get(owner_id, run.mission_id)
            if mission.session_key == session.key and mission.state == "active":
                return mission
        except KeyError:
            pass
        return None

    def _mission_artifact_tool_is_ready(self, owner_id: str, mission) -> bool:
        """Expose the draft proposal tool only for an actionable mission step.

        This keeps an approval-only tool out of unrelated chats and avoids
        letting the model guess which of several missions it should affect.
        The executor rechecks the same conditions immediately before a write.
        """
        if mission is None:
            return False
        blueprint = self.missions.get_approved_blueprint(owner_id, mission.id)
        return bool(blueprint and any(step.state == "active" for step in blueprint.steps))

    def _blueprint_for_turn(self, owner_id: str, mission, queued_run_id: str | None):
        """Return only blueprint context that still agrees with a queued run.

        A run records the human-selected step at submission.  If a human
        advances or replaces the blueprint while that run waits for the
        processing lock, injecting a different step would misrepresent the
        run's evidence.  In that case we retain the mission context but omit
        the stale blueprint block rather than guessing.
        """
        if not mission:
            return None
        blueprint = self.missions.get_approved_blueprint(owner_id, mission.id)
        if not queued_run_id or blueprint is None:
            return blueprint
        try:
            run = self.runs.get(owner_id, queued_run_id)
        except KeyError:
            return None
        if run.mission_id != mission.id:
            return None
        active_step = next(
            (step for step in blueprint.steps if step.state in {"active", "blocked"}), None
        )
        if active_step is None or active_step.step_id != run.blueprint_step_id:
            return None
        return blueprint

    def _queue_run(self, msg: InboundMessage) -> RunRecord:
        """Persist a queued normal-message turn before it waits for execution."""
        owner_id = self._run_owner_id(msg)
        session = self.sessions.get_or_create(self._run_session_key(msg))
        active_mission = self._active_mission_for_session(session, owner_id)
        blueprint_step_id = None
        if active_mission:
            approved_bp = self.missions.get_approved_blueprint(owner_id, active_mission.id)
            if approved_bp:
                active_step = next((s for s in approved_bp.steps if s.state in {"active", "blocked"}), None)
                if active_step:
                    blueprint_step_id = active_step.step_id

        profile = self._session_profile(session)
        allowed_tools = self.capabilities.allowed_tools(
            profile.id, self.tools.tool_names, governed_registry=self.governed_registry
        )
        serving, model, _effort, policy = self._serving_resources(session)
        run = self.runs.create(
            owner_id=owner_id,
            session_key=session.key,
            capability_profile=profile.id,
            policy_revision=self._policy_revision(profile.id, allowed_tools, policy=policy),
            provider=self._provider_label(serving),
            model=model,
            mission_id=active_mission.id if active_mission else None,
            blueprint_step_id=blueprint_step_id,
        )
        # The run is submitted now, even when it must wait for the processing
        # lock. Keep its resolved non-secret policy so an edit made while it is
        # queued applies only to the next submitted turn.
        self._queued_policy_snapshots[run.id] = policy
        return run

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
                run = self.runs.cancel(owner_id, run_id)
            else:
                run = self.runs.fail(owner_id, run_id, error_summary="The turn stopped before completing.")
            if run.mission_id:
                self.missions.record_run_terminal_event(
                    owner_id, run.mission_id, run.id, run.state, error_summary=run.error_summary
                )
        except Exception:
            logger.warning("Could not terminalize queued run {}", run_id[:8])

    @staticmethod
    def _policy_revision(profile_id: str, allowed_tools, *, policy=None) -> str:
        """A non-secret signature of the profile, tool allow-list, and runtime policy."""
        signature = ",".join(sorted(allowed_tools or []))
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        revision = f"{profile_id}@{digest}"
        if policy is None:
            return revision
        return (
            f"{revision}~v{policy.version or 0}:"
            f"{policy.provider or '-'}:{policy.model or '-'}:"
            f"{policy.reasoning_effort or '-'}:{policy.response_mode}"
        )

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
        task_id: str | None = None,
        result_ref: str | None = None,
        queued_run_id: str | None = None,
    ) -> tuple[str | None, list[dict], dict[str, Any], RunRecord]:
        """Create a queued run, execute one turn, and persist the terminal state."""
        queued_policy = (
            self._queued_policy_snapshots.pop(queued_run_id, None)
            if queued_run_id
            else None
        )
        serving, model, reasoning_effort, policy = self._serving_resources(
            session, policy=queued_policy
        )
        provider = self._provider_label(serving)
        if queued_run_id:
            run = self.runs.get(owner_id, queued_run_id)
        else:
            run = self.runs.create(
                owner_id=owner_id,
                session_key=session.key,
                capability_profile=profile.id,
                policy_revision=self._policy_revision(profile.id, allowed_tools, policy=policy),
                provider=provider,
                model=model,
                mission_id=mission_id,
                task_id=task_id,
            )
        run_task_id = task_id or getattr(run, "task_id", None)
        run = self.runs.mark_running(owner_id, run.id, provider=provider, model=model)
        activity_context["run_id"] = run.id
        self._set_tool_context(
            activity_context.get("channel", "web"),
            activity_context.get("chat_id") or "direct",
            activity_context.get("message_id"),
            owner_id=owner_id,
            session_key=session.key,
            profile_id=profile.id,
            queued_run_id=run.id,
            mission_id=mission_id,
        )
        if run_task_id:
            try:
                self.tasks.mark_running(owner_id, run_task_id, run_id=run.id, session_key=session.key)
            except Exception as exc:
                logger.error("Could not mark task {} running: {}", run_task_id[:8], exc)
                self.runs.fail(
                    owner_id,
                    run.id,
                    error_summary=f"Task linkage failure: {exc}",
                )
                try:
                    self.tasks.fail(
                        owner_id,
                        run_task_id,
                        failure_category="linkage_failure",
                        result_summary=self._safe_error_summary(f"Task linkage failure: {exc}"),
                        session_key=session.key,
                    )
                except Exception:
                    pass
                if session.metadata.get("pico_active_task_id") == run_task_id:
                    session.metadata.pop("pico_active_task_id", None)
                    self.sessions.save(session)
                raise

        task_obj = self.tasks.get(owner_id, run_task_id) if run_task_id else None
        timeout_sec = task_obj.max_elapsed_sec if task_obj else None

        try:
            if timeout_sec and timeout_sec > 0:
                final_content, tools_used, all_msgs, response_meta = await asyncio.wait_for(
                    self._run_agent_loop(
                        messages,
                        allowed_tools=allowed_tools,
                        activity_context=activity_context,
                        on_progress=on_progress,
                        serving_provider=serving,
                        serving_model=model,
                        reasoning_effort=reasoning_effort,
                        max_turns=task_obj.max_turns if task_obj else None,
                    ),
                    timeout=float(timeout_sec),
                )
            else:
                final_content, tools_used, all_msgs, response_meta = await self._run_agent_loop(
                    messages,
                    allowed_tools=allowed_tools,
                    activity_context=activity_context,
                    on_progress=on_progress,
                    serving_provider=serving,
                    serving_model=model,
                    reasoning_effort=reasoning_effort,
                    max_turns=task_obj.max_turns if task_obj else None,
                )
            run_usage = response_meta.pop("_usage", {})
            run_failed = response_meta.pop("_failed", False)
            budget_exhausted = response_meta.pop("_budget_exhausted", False)
            run_meta = {
                "provider": response_meta.get("served_by") or provider,
                "model": response_meta.get("served_model") or model,
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
                if run_task_id:
                    try:
                        self.tasks.fail(
                            owner_id,
                            run_task_id,
                            failure_category="budget_exhausted" if budget_exhausted else "execution_error",
                            result_summary=self._safe_error_summary(final_content),
                            session_key=session.key,
                        )
                    except Exception:
                        pass
                    if session.metadata.get("pico_active_task_id") == run_task_id:
                        session.metadata.pop("pico_active_task_id", None)
                        self.sessions.save(session)
            else:
                staged_proposals = [
                    p
                    for p in self.proposed_actions.list(owner_id, session.key, limit=100)
                    if p.status in {"proposed", "approved"} and p.initiating_run_id == run.id
                ]
                if staged_proposals:
                    run = self.runs.wait_for_approval(owner_id, run.id)
                    if run_task_id:
                        try:
                            self.tasks.mark_waiting_for_approval(
                                owner_id, run_task_id, session_key=session.key
                            )
                        except Exception:
                            pass
                else:
                    run = self.runs.complete(owner_id, run.id, **run_meta)
                    if run_task_id:
                        try:
                            self.tasks.complete(
                                owner_id,
                                run_task_id,
                                result_summary=f"Run {run.id[:8]} completed successfully.",
                                result_ref=result_ref or f"run:{run.id}",
                                session_key=session.key,
                            )
                        except Exception:
                            pass
                        if session.metadata.get("pico_active_task_id") == run_task_id:
                            session.metadata.pop("pico_active_task_id", None)
                            self.sessions.save(session)
        except asyncio.TimeoutError:
            logger.warning("Run {} timed out after {}s for session {}", run.id[:8], timeout_sec, session.key)
            try:
                run = self.runs.cancel(owner_id, run.id)
            except Exception:
                pass
            if run_task_id:
                try:
                    self.tasks.fail(
                        owner_id,
                        run_task_id,
                        failure_category="budget_exhausted",
                        result_summary=f"Task execution exceeded maximum allowed time limit ({timeout_sec}s).",
                        session_key=session.key,
                    )
                except Exception:
                    pass
                if session.metadata.get("pico_active_task_id") == run_task_id:
                    session.metadata.pop("pico_active_task_id", None)
                    self.sessions.save(session)
            final_content = "Task execution exceeded maximum allowed time limit."
            all_msgs = messages
            response_meta = {"_failed": True}
        except asyncio.CancelledError:
            logger.info("Run {} cancelled for session {}", run.id[:8], session.key)
            try:
                run = self.runs.cancel(owner_id, run.id)
            except Exception:
                logger.warning("Could not cancel run {} after cancellation", run.id[:8])
            if getattr(run, "mission_id", None):
                try:
                    self.missions.record_run_terminal_event(
                        owner_id, run.mission_id, run.id, "cancelled"
                    )
                except Exception:
                    pass
            if run_task_id:
                try:
                    self.tasks.cancel(owner_id, run_task_id, session_key=session.key)
                except Exception:
                    pass
                if session.metadata.get("pico_active_task_id") == run_task_id:
                    session.metadata.pop("pico_active_task_id", None)
                    self.sessions.save(session)
            raise
        except Exception:
            logger.exception("Run {} failed for session {}", run.id[:8], session.key)
            try:
                run = self.runs.fail(
                    owner_id, run.id, error_summary="The turn stopped before completing."
                )
            except Exception:
                logger.warning("Could not fail run {} after an internal error", run.id[:8])
            if getattr(run, "mission_id", None):
                try:
                    self.missions.record_run_terminal_event(
                        owner_id, run.mission_id, run.id, "failed", error_summary=run.error_summary
                    )
                except Exception:
                    pass
            if run_task_id:
                try:
                    self.tasks.fail(
                        owner_id,
                        run_task_id,
                        failure_category="execution_error",
                        result_summary="The task turn stopped before completing.",
                        session_key=session.key,
                    )
                except Exception:
                    pass
                if session.metadata.get("pico_active_task_id") == run_task_id:
                    session.metadata.pop("pico_active_task_id", None)
                    self.sessions.save(session)
            raise

        if getattr(run, "mission_id", None):
            try:
                self.missions.record_run_terminal_event(
                    owner_id, run.mission_id, run.id, run.state, error_summary=run.error_summary
                )
            except Exception:
                pass
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
            owner_id = self._owner_id(channel, msg.sender_id)
            active_mission = self._mission_for_turn(session, owner_id, queued_run_id)
            allowed_tools = self.capabilities.allowed_tools(
                profile.id, self.tools.tool_names, governed_registry=self.governed_registry
            )
            if not self._mission_artifact_tool_is_ready(owner_id, active_mission):
                allowed_tools.discard("save_mission_artifact_draft")
            self._set_tool_context(
                channel,
                chat_id,
                msg.metadata.get("message_id"),
                owner_id=owner_id,
                session_key=session.key,
                profile_id=profile.id,
                queued_run_id=queued_run_id,
                mission_id=active_mission.id if active_mission else None,
            )
            history = await self._history_for_prompt(
                session, owner_id, estimate_tokens(msg.content), queued_run_id=queued_run_id
            )
            active_blueprint = self._blueprint_for_turn(owner_id, active_mission, queued_run_id)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                owner_id=owner_id,
                system_prompt=self._context_snapshot(
                    session, self._policy_for_submitted_turn(session, queued_run_id)
                ),
                active_mission=active_mission,
                active_blueprint=active_blueprint,
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
                mission_id=active_mission.id if active_mission else None,
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
        active_mission = self._mission_for_turn(session, owner_id, queued_run_id)
        allowed_tools = self.capabilities.allowed_tools(
            profile.id, self.tools.tool_names, governed_registry=self.governed_registry
        )
        if not self._mission_artifact_tool_is_ready(owner_id, active_mission):
            allowed_tools.discard("save_mission_artifact_draft")

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
                "/status — Show runtime and session status",
                "/recap — Show a concise local session recap",
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
        if cmd in {"/status", "/recap"}:
            lines = self._session_status_lines(session, owner_id, recap=cmd == "/recap")
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
        self._set_tool_context(
            msg.channel,
            msg.chat_id,
            msg.metadata.get("message_id"),
            owner_id=owner_id,
            session_key=session.key,
            profile_id=profile.id,
            queued_run_id=queued_run_id,
            mission_id=active_mission.id if active_mission else None,
        )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = await self._history_for_prompt(
            session, owner_id, estimate_tokens(msg.content), queued_run_id=queued_run_id
        )
        recalled_memory = self.context.personal_memory.recall(owner_id, msg.content)
        self.context.personal_memory.record_use(
            owner_id,
            [item.id for item in recalled_memory],
            session_key=session.key,
        )
        self._record_turn_context(session, history, recalled_memory)
        active_blueprint = self._blueprint_for_turn(owner_id, active_mission, queued_run_id)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            owner_id=owner_id,
            system_prompt=self._context_snapshot(
                session, self._policy_for_submitted_turn(session, queued_run_id)
            ),
            recalled_memory=recalled_memory,
            active_mission=active_mission,
            active_blueprint=active_blueprint,
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
                "channel": msg.channel,
                "chat_id": msg.chat_id,
                "message_id": message_id,
            },
            on_progress=on_progress or _bus_progress,
            result_ref=f"message:{message_id}" if isinstance(message_id, str) else None,
            queued_run_id=queued_run_id,
            mission_id=active_mission.id if active_mission else None,
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

    def _session_status_lines(
        self, session: Session, owner_id: str, *, recap: bool = False
    ) -> list[str]:
        """Build a bounded, observable status projection for one session.

        This deliberately reports durable state and counts only. It never
        includes prompts, tool arguments, credentials, or hidden reasoning.
        """
        profile = self._session_profile(session)
        active_mission = self._active_mission_for_session(session, owner_id)
        active_task = self.tasks.get_active(owner_id, session.key)
        active_run = self.runs.get_active(owner_id, session.key)
        latest_run = self.runs.list(owner_id, session_key=session.key, limit=1)
        pending_actions = [
            action
            for action in self.proposed_actions.list(owner_id, session.key, limit=100)
            if action.status in {"proposed", "approved"}
        ]
        message_count = len(
            [message for message in session.messages if message.get("role") in {"user", "assistant"}]
        )
        mission_text = (
            f"{active_mission.title} [{active_mission.state}]"
            if active_mission
            else "none"
        )
        task_text = f"{active_task.title} [{active_task.state}]" if active_task else "none"
        run = active_run or (latest_run[0] if latest_run else None)
        run_text = f"{run.state} ({run.id[:8]})" if run else "none"
        heading = "picobot session recap" if recap else "picobot status"
        return [
            heading,
            f"Session: {session.key}",
            f"Messages: {message_count}",
            f"Profile: {profile.label} ({profile.id})",
            f"Model: {self.model}",
            f"Mission: {mission_text}",
            f"Task: {task_text}",
            f"Latest run: {run_text}",
            f"Approvals waiting: {len(pending_actions)}",
            f"DAX: {'enabled' if self._dax_service else 'disabled'}",
            f"MCP connected: {'yes' if self._mcp_connected else 'no'}",
            f"Inbound queue: {self.bus.inbound_size}",
            f"Outbound queue: {self.bus.outbound_size}",
        ]

    def _context_snapshot(self, session: Session, policy=None) -> str:
        """Return the stable session prompt plus a truthful per-turn style preference.

        The durable base prompt stays cached in the session. Runtime policy is
        deliberately resolved per turn, so an owner-visible policy change takes
        effect for later turns without rewriting history or the session's
        recorded identity prompt.
        """
        snapshot = session.metadata.get("pico_system_prompt")
        if isinstance(snapshot, str) and snapshot.strip():
            base_prompt = snapshot
        else:
            profile = self._session_profile(session)
            base_prompt = self.context.build_system_prompt() + (
                "\n\n# Session capability profile\n\n"
                f"Active profile: {profile.label}. {profile.description}\n"
                "Use only tool definitions available in this session. Do not claim access to other tools."
            )
            session.metadata["pico_system_prompt"] = base_prompt

        response_mode = getattr(policy, "response_mode", "default")
        if response_mode == "concise":
            return base_prompt + (
                "\n\n# Response preference for this turn\n\n"
                "Be concise. Lead with the answer and include only the detail needed to act."
            )
        if response_mode == "detailed":
            return base_prompt + (
                "\n\n# Response preference for this turn\n\n"
                "Be thorough when it helps. Explain material decisions and tradeoffs, but do not pad the answer."
            )
        return base_prompt

    def _session_profile(self, session: Session):
        """Resolve a server-owned profile and persist a safe default if needed."""
        profile = self.capabilities.resolve(session.metadata.get("pico_operation_profile"))
        if session.metadata.get("pico_operation_profile") != profile.id:
            session.metadata["pico_operation_profile"] = profile.id
        return profile

    async def _history_for_prompt(
        self,
        session: Session,
        owner_id: str,
        current_request_tokens: int = 0,
        queued_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the budget-bounded model window for one turn.

        The window is deterministic: it keeps everything below the budget,
        compacts older eligible history into a durable handoff while the
        protected recent tail stays verbatim, or falls back to the largest
        safe trailing slice.  The source session transcript is never changed.
        """
        policy = self._policy_for_submitted_turn(session, queued_run_id)
        serving, model, _effort, _policy = self._serving_resources(session, policy=policy)
        summarizer = self.compaction.summarizer or ProviderContextSummarizer(serving, model)
        window = await self.compaction.build_window(
            session.get_history(),
            owner_id=owner_id,
            session_key=session.key,
            budget_tokens=self.context_window_tokens,
            current_request_tokens=current_request_tokens,
            provider=self._provider_label(serving),
            model=model,
            summarizer=summarizer,
        )
        return window.messages

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

    async def run_direct_task(
        self,
        owner_id: str,
        session_key: str,
        task_id: str,
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[TaskRecord, RunRecord]:
        """Execute a direct task turn through Pico's existing agent run pipeline."""
        await self._connect_mcp()
        session = self.sessions.get_or_create(session_key)

        task = self.tasks.get(owner_id, task_id)
        if task.session_key != session_key:
            raise ValueError("Session mismatch for direct task execution")
        if task.state != "queued":
            raise ValueError(f"Task in state '{task.state}' cannot be executed")

        active_mission = self._active_mission_for_session(session, owner_id)
        if not active_mission and task.mission_id:
            try:
                m = self.missions.get(owner_id, task.mission_id)
                if m.session_key == session_key and m.state == "active":
                    active_mission = m
                    session.metadata["pico_active_mission_id"] = m.id
                    self.sessions.save(session)
            except KeyError:
                pass

        if (
            not active_mission
            or active_mission.id != task.mission_id
            or active_mission.state != "active"
            or active_mission.session_key != session_key
        ):
            raise ValueError("Active mission mismatch or inactive for direct task execution")

        profile = self._session_profile(session)
        if profile.id != task.capability_profile:
            raise ValueError(
                f"Session profile '{profile.id}' does not match task capability snapshot '{task.capability_profile}'"
            )

        allowed_tools = self.capabilities.allowed_tools(
            profile.id, self.tools.tool_names, governed_registry=self.governed_registry
        )

        prompt_content = f"Task: {task.title}\nObjective: {task.objective}"
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=prompt_content,
            system_prompt="You are executing a bounded direct task under an active mission.",
            active_mission=active_mission,
        )

        channel, _, chat_id = session_key.partition(":")
        self._set_tool_context(
            channel or "web",
            chat_id or "task",
            owner_id=owner_id,
            session_key=session_key,
            profile_id=profile.id,
            mission_id=active_mission.id if active_mission else None,
        )

        activity_context = {
            "owner_id": owner_id,
            "session_key": session_key,
            "profile_id": profile.id,
            "channel": "direct_task",
        }

        current_async_task = asyncio.current_task()
        if current_async_task:
            self._active_async_tasks[task.id] = current_async_task

        try:
            _content, _msgs, _meta, run = await self._run_turn(
                owner_id=owner_id,
                session=session,
                profile=profile,
                allowed_tools=allowed_tools,
                messages=messages,
                activity_context=activity_context,
                on_progress=on_progress,
                task_id=task.id,
                mission_id=active_mission.id if active_mission else None,
            )
        except asyncio.CancelledError:
            try:
                self.tasks.cancel(owner_id, task.id, session_key, run_store=self.runs)
            except Exception:
                pass
            raise
        finally:
            self._active_async_tasks.pop(task.id, None)

        updated_task = self.tasks.get(owner_id, task.id)
        return updated_task, run
