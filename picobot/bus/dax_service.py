"""DAX polling service for Picobot.

This service polls DAX for run status and approval notifications.
Runs as a background task that monitors active workflow runs.
"""

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import httpx
from loguru import logger

from picobot.bus.events import OutboundMessage
from picobot.bus.dax_auth import get_admin_numbers, is_authorized, get_default_url


@dataclass
class ActiveRun:
    """Tracks an active DAX run."""
    run_id: str
    chat_id: str
    channel: str = "whatsapp"
    created_at: datetime = field(default_factory=datetime.now)
    notified_waiting: bool = False
    notified_completed: bool = False
    notified_failed: bool = False
    notified_approval_ids: set = field(default_factory=set)


class CircuitBreaker:
    """Simple circuit breaker for DAX API calls."""
    
    def __init__(self, failure_threshold: int = 5):
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0
        self.open_until: datetime | None = None
        self._lock = asyncio.Lock()
    
    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            self.consecutive_failures = 0
            self.open_until = None
    
    async def record_failure(self) -> None:
        """Record a failed call."""
        async with self._lock:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.failure_threshold:
                self.open_until = datetime.now()
                logger.warning(f"Circuit breaker opened after {self.consecutive_failures} failures")
    
    async def is_open(self) -> bool:
        """Check if circuit breaker is open (failing fast)."""
        async with self._lock:
            if self.open_until is None:
                return False
            if datetime.now() > self.open_until:
                self.consecutive_failures = 0
                self.open_until = None
                return False
            return True
    
    def reset(self) -> None:
        """Reset the circuit breaker."""
        self.consecutive_failures = 0
        self.open_until = None


POLL_INTERVALS = [
    (0, 5, 2),
    (5, 30, 5),
    (30, 300, 15),
    (300, None, 60),
]


def _get_poll_interval(elapsed_seconds: float) -> float:
    """Get appropriate poll interval based on elapsed time."""
    for start, end, interval in POLL_INTERVALS:
        if end is None:
            return interval
        if start <= elapsed_seconds < end:
            return interval
    return 60


class DaxPollingService:
    """Background service that polls DAX for workflow status and approvals."""
    
    def __init__(
        self,
        dax_url: str | None = None,
        send_callback: Callable[[OutboundMessage], None] | None = None,
        admin_numbers: list[str] | None = None,
        failure_threshold: int = 5,
    ):
        self.dax_url = get_default_url(dax_url)
        self.send_callback = send_callback
        self.admin_numbers = get_admin_numbers(admin_numbers)
        self._active_runs: dict[str, ActiveRun] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._circuit_breaker = CircuitBreaker(failure_threshold)
    
    @property
    def is_available(self) -> bool:
        """Check if DAX service is available (circuit breaker not open)."""
        return not self._circuit_breaker.is_open() if hasattr(self._circuit_breaker, 'is_open') else True
    
    def start(self) -> None:
        """Start the polling service."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._startup_and_poll())
        logger.info("DAX polling service started")

    async def _startup_and_poll(self) -> None:
        """Initialize active runs from DAX on startup, then poll."""
        try:
            # Brief delay to ensure DAX server is ready if they start simultaneously
            await asyncio.sleep(2)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.dax_url}/soothsayer/overview")
                if resp.status_code == 200:
                    data = resp.json()
                    active = data.get("activeRuns", [])
                    for run in active:
                        # Find runs created by picobot. We might not know chat_id here unless stored in run metadata
                        # We'll track it and try to extract chat_id if available, or fallback to an admin number
                        run_id = run["runId"]
                        chat_id = "unknown"
                        # We can fetch run details to get the chat_id from metadata
                        try:
                            detail_resp = await client.get(f"{self.dax_url}/soothsayer/runs/{run_id}")
                            if detail_resp.status_code == 200:
                                detail_data = detail_resp.json()
                                meta = detail_data.get("metadata", {})
                                if meta.get("sourceSystem") == "picobot" or meta.get("chatId"):
                                    chat_id = meta.get("chatId", self.admin_numbers[0] if self.admin_numbers else "unknown")
                                    if chat_id != "unknown":
                                        self.track_run(run_id, chat_id, channel="telegram")
                        except Exception as e:
                            logger.error(f"Failed to fetch metadata for active run {run_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch active runs on startup: {e}")
            
        await self._poll_loop()
    
    def stop(self) -> None:
        """Stop the polling service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("DAX polling service stopped")
    
    def track_run(self, run_id: str, chat_id: str, channel: str = "whatsapp") -> None:
        """Start tracking a run for status updates."""
        self._active_runs[run_id] = ActiveRun(
            run_id=run_id,
            chat_id=chat_id,
            channel=channel,
        )
        logger.info(f"Tracking DAX run {run_id} for chat {chat_id}")
    
    def stop_tracking(self, run_id: str) -> None:
        """Stop tracking a run."""
        self._active_runs.pop(run_id, None)
        logger.info(f"Stopped tracking DAX run {run_id}")
    
    async def health_check(self) -> bool:
        """Check if DAX service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.dax_url}/health")
                return response.status_code == 200
        except Exception:
            return False
    
    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                if await self._circuit_breaker.is_open():
                    await asyncio.sleep(30)
                    continue
                    
                for run_id, run in list(self._active_runs.items()):
                    await self._poll_run(run)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                await self._circuit_breaker.record_failure()
                await asyncio.sleep(5)
    
    async def _poll_run(self, run: ActiveRun) -> None:
        """Poll a single run for status updates."""
        elapsed = (datetime.now() - run.created_at).total_seconds()
        poll_interval = _get_poll_interval(elapsed)
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Check recovery status first
                recovery_resp = await client.get(f"{self.dax_url}/soothsayer/runs/{run.run_id}/recovery")
                if recovery_resp.status_code == 200:
                    rec_data = recovery_resp.json()
                    if rec_data.get("needsRecovery"):
                        logger.info(f"Run {run.run_id} needs recovery, attempting to recover...")
                        if self.send_callback and not getattr(run, "notified_recovery", False):
                            self.send_callback(OutboundMessage(
                                channel=run.channel,
                                chat_id=run.chat_id,
                                content="🔄 *Recovering Interrupted Workflow*\n\nYour workflow was interrupted. I am attempting to reconstruct its state and resume it.",
                                metadata={"run_id": run.run_id},
                            ))
                            setattr(run, "notified_recovery", True)
                        
                        await client.post(f"{self.dax_url}/soothsayer/runs/{run.run_id}/recover")
                
                detail = await client.get(f"{self.dax_url}/soothsayer/runs/{run.run_id}")
                if detail.status_code != 200:
                    await self._circuit_breaker.record_failure()
                    return
                
                await self._circuit_breaker.record_success()
                data = detail.json()
                
                status = data.get("status")
                
                if status == "waiting_approval" and not run.notified_waiting:
                    await self._notify_waiting(run, data)
                    run.notified_waiting = True
                
                elif status == "completed" and not run.notified_completed:
                    await self._notify_completed(run, data)
                    run.notified_completed = True
                    self.stop_tracking(run.run_id)
                
                elif status == "failed" and not run.notified_failed:
                    await self._notify_failed(run, data)
                    run.notified_failed = True
                    self.stop_tracking(run.run_id)
                    
        except Exception as e:
            logger.error(f"Failed to poll run {run.run_id}: {e}")
            await self._circuit_breaker.record_failure()
    
    @staticmethod
    def format_approval_message(approval: dict) -> str:
        """Format approval request for WhatsApp."""
        risk_color = approval.get("riskColor", "gray")
        risk_emoji = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}.get(risk_color, "⚪")
        
        lines = [
            "⚠️ *Approval Required*",
            "",
            f"*Type*: {approval.get('typeLabel', approval.get('type', 'Unknown'))}",
            f"*Risk*: {risk_emoji} {approval.get('riskLabel', approval.get('risk', 'Unknown'))}",
            "",
        ]
        
        if approval.get("titleEnriched"):
            lines.append(f"📄 {approval.get('titleEnriched')}")
        elif approval.get("title"):
            lines.append(f"📄 {approval.get('title')}")
        
        if approval.get("whatHappensNext"):
            what_next = approval["whatHappensNext"]
            lines.append("")
            lines.append("*What happens after approval:*")
            lines.append(f"→ {what_next.get('afterApprove', 'Proceed with operation.')}")
        
        lines.append("")
        lines.append("---")
        lines.append("Reply with:")
        lines.append("• `approve` - to proceed")
        lines.append("• `deny` - to cancel")
        
        return "\n".join(lines)
    
    @staticmethod
    def get_approval_buttons(run_id: str, approval_id: str) -> list[list[dict]]:
        """Get inline buttons for approve/deny."""
        return [[
            {"text": "✅ Approve", "callback_data": f"approve_{run_id}"},
            {"text": "❌ Deny", "callback_data": f"deny_{run_id}"},
        ]]
    
    async def _notify_waiting(self, run: ActiveRun, data: dict) -> None:
        """Notify user that approval is needed."""
        if not is_authorized(run.chat_id, self.admin_numbers):
            logger.warning(f"Skipping notification to unauthorized chat: {run.chat_id}")
            return
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                approvals = await client.get(f"{self.dax_url}/soothsayer/runs/{run.run_id}/approvals")
                if approvals.status_code != 200:
                    return
                
                pending = [a for a in approvals.json() if a.get("status") == "pending"]
                if not pending:
                    return
                
                for approval in pending:
                    approval_id = approval.get("approvalId")
                    if approval_id in run.notified_approval_ids:
                        continue
                    
                    message = self.format_approval_message(approval)
                    inline_buttons = self.get_approval_buttons(run.run_id, approval_id)
                    
                    if self.send_callback:
                        self.send_callback(OutboundMessage(
                            channel=run.channel,
                            chat_id=run.chat_id,
                            content=message,
                            metadata={"run_id": run.run_id, "approval_id": approval_id},
                            inline_buttons=inline_buttons,
                        ))
                    
                    run.notified_approval_ids.add(approval_id)
                    
        except Exception as e:
            logger.error(f"Failed to notify waiting: {e}")
    
    async def _notify_completed(self, run: ActiveRun, data: dict) -> None:
        """Notify user that workflow completed."""
        if not is_authorized(run.chat_id, self.admin_numbers):
            logger.warning(f"Skipping completion notification to unauthorized chat: {run.chat_id}")
            return
        
        terminal = data.get("terminalReasonLabel", "Completed")
        
        message = [
            "✅ *Workflow Completed*",
            "",
            f"Result: {terminal}",
            "",
            "Check your files for the results.",
        ]
        
        if self.send_callback:
            self.send_callback(OutboundMessage(
                channel=run.channel,
                chat_id=run.chat_id,
                content="\n".join(message),
                metadata={"run_id": run.run_id},
            ))
    
    async def _notify_failed(self, run: ActiveRun, data: dict) -> None:
        """Notify user that workflow failed."""
        if not is_authorized(run.chat_id, self.admin_numbers):
            logger.warning(f"Skipping failure notification to unauthorized chat: {run.chat_id}")
            return
        
        terminal_label = data.get("terminalReasonLabel", "Failed")
        terminal_desc = data.get("terminalReasonDescription", "")
        
        message = ["❌ *Workflow Failed*", "", f"Reason: {terminal_label}"]
        if terminal_desc:
            message.append(terminal_desc)
        
        if self.send_callback:
            self.send_callback(OutboundMessage(
                channel=run.channel,
                chat_id=run.chat_id,
                content="\n".join(message),
                metadata={"run_id": run.run_id},
            ))


_dax_service: DaxPollingService | None = None


def get_dax_service() -> DaxPollingService | None:
    """Get the global DAX polling service instance."""
    return _dax_service


def init_dax_service(
    dax_url: str | None = None,
    send_callback: Callable[[OutboundMessage], None] | None = None,
    admin_numbers: list[str] | None = None,
    failure_threshold: int = 5,
) -> DaxPollingService:
    """Initialize the global DAX polling service."""
    global _dax_service
    _dax_service = DaxPollingService(
        dax_url=dax_url,
        send_callback=send_callback,
        admin_numbers=admin_numbers,
        failure_threshold=failure_threshold,
    )
    return _dax_service
