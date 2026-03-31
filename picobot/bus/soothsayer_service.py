"""Soothsayer integration service for Picobot.

This service sends activity events to Soothsayer's control plane
so that Picobot's activity is visible in the Soothsayer dashboard.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from picobot.config.schema import SoothsayerConfig


@dataclass
class ActivityEvent:
    """An activity event to sync to Soothsayer."""

    type: str  # session_start, session_end, message_received, message_sent, error, etc.
    channel: str  # telegram, whatsapp, discord, etc.
    user_id: str | None = None
    user_name: str | None = None
    message: str = ""
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "channelType": self.channel,
            "userId": self.user_id,
            "userName": self.user_name,
            "message": self.message,
            "metadata": self.metadata or {},
        }


class SoothsayerService:
    """Service for syncing Picobot activity to Soothsayer control plane."""

    def __init__(self, config: SoothsayerConfig):
        self.config = config
        self._running = False
        self._event_queue: asyncio.Queue[ActivityEvent] = asyncio.Queue()
        self._client: httpx.AsyncClient | None = None
        self._send_task: asyncio.Task | None = None

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    @property
    def api_url(self) -> str:
        base = self.config.url.rstrip("/")
        return f"{base}/api"

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def start(self) -> None:
        """Start the Soothsayer sync service."""
        if not self.is_enabled:
            logger.debug("Soothsayer integration disabled")
            return

        if not self.config.url:
            logger.warning("Soothsayer URL not configured")
            return

        self._running = True
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            headers=self._get_headers(),
            timeout=10.0,
        )
        self._send_task = asyncio.create_task(self._process_queue())
        logger.info("Soothsayer sync service started ({}: {})", self.config.url, self.config.workspace_id)

    async def stop(self) -> None:
        """Stop the Soothsayer sync service."""
        self._running = False

        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info("Soothsayer sync service stopped")

    async def track_event(self, event: ActivityEvent) -> None:
        """Queue an activity event for syncing to Soothsayer."""
        if not self.is_enabled:
            return

        if event.type not in self.config.events:
            return

        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Soothsayer event queue full, dropping event: {}", event.type)

    def track_session_start(
        self,
        channel: str,
        user_id: str | None = None,
        user_name: str | None = None,
    ) -> None:
        """Track a new session start."""
        asyncio.create_task(
            self.track_event(
                ActivityEvent(
                    type="session_start",
                    channel=channel,
                    user_id=user_id,
                    user_name=user_name,
                    message="Session started",
                )
            )
        )

    def track_session_end(
        self,
        channel: str,
        user_id: str | None = None,
        user_name: str | None = None,
        message: str = "Session ended",
    ) -> None:
        """Track a session end."""
        asyncio.create_task(
            self.track_event(
                ActivityEvent(
                    type="session_end",
                    channel=channel,
                    user_id=user_id,
                    user_name=user_name,
                    message=message,
                )
            )
        )

    def track_message(
        self,
        direction: str,  # "received" or "sent"
        channel: str,
        user_id: str | None = None,
        user_name: str | None = None,
        preview: str = "",
    ) -> None:
        """Track a message (sent or received)."""
        msg_type = "message_sent" if direction == "sent" else "message_received"
        asyncio.create_task(
            self.track_event(
                ActivityEvent(
                    type=msg_type,
                    channel=channel,
                    user_id=user_id,
                    user_name=user_name,
                    message=f"Message {direction}: {preview[:100]}",
                )
            )
        )

    def track_error(
        self,
        channel: str,
        error: str,
        user_id: str | None = None,
    ) -> None:
        """Track an error."""
        asyncio.create_task(
            self.track_event(
                ActivityEvent(
                    type="error",
                    channel=channel,
                    user_id=user_id,
                    message=f"Error: {error[:200]}",
                )
            )
        )

    async def _process_queue(self) -> None:
        """Process events from the queue and send to Soothsayer."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=self.config.sync_interval_s,
                )
                await self._send_event(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Error processing Soothsayer event queue: {}", e)

    async def _send_event(self, event: ActivityEvent) -> None:
        """Send a single event to Soothsayer."""
        if not self._client:
            return

        try:
            # Use the public webhook endpoint
            response = await self._client.post(
                "/picobot/webhook/activity",
                json={
                    "picobotId": self.config.workspace_id,  # Using workspace_id as picobotId
                    "activity": event.to_dict(),
                },
            )
            if response.status_code in (200, 201, 202):
                logger.debug("Sent {} event to Soothsayer", event.type)
            else:
                logger.warning(
                    "Failed to send event to Soothsayer: {} - {}",
                    response.status_code,
                    response.text,
                )
        except httpx.TimeoutException:
            logger.warning("Timeout sending event to Soothsayer: {}", event.type)
        except Exception as e:
            logger.error("Error sending event to Soothsayer: {}", e)

    async def update_health(self, status: str, health_data: dict) -> None:
        """Update Picobot health status in Soothsayer."""
        if not self.is_enabled or not self._client:
            return

        try:
            await self._client.post(
                "/picobot/webhook/health",
                json={
                    "channelStatus": self.config.workspace_id,
                    "status": status,
                    "health": {
                        **health_data,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                },
            )
        except Exception as e:
            logger.debug("Error updating Soothsayer health: {}", e)

    async def poll_and_execute_commands(self) -> list[dict]:
        """Poll for pending commands and execute them. Returns executed commands."""
        if not self.is_enabled or not self._client:
            return []

        executed = []
        try:
            response = await self._client.get(
                f"/picobot/commands/pending?picobotId={self.config.workspace_id}"
            )
            if response.status_code == 200:
                data = response.json()
                commands = data.get("data", data) if isinstance(data, dict) else data
                for cmd in commands:
                    await self._execute_command(cmd)
                    executed.append(cmd)
        except Exception as e:
            logger.debug("Error polling commands from Soothsayer: {}", e)
        return executed

    async def _execute_command(self, command: dict) -> None:
        """Execute a single command from Soothsayer."""
        if not self._client:
            return

        cmd_id = command.get("id")
        cmd_type = command.get("commandType")
        payload = command.get("payload", {})

        try:
            await self._client.post(f"/picobot/commands/{cmd_id}/acknowledge")
        except Exception:
            pass

        result = {"success": False, "error": "Command executor not configured"}

        if cmd_type == "send_message":
            from picobot.bus.queue import _message_bus
            from picobot.bus.events import OutboundMessage
            bus = _message_bus
            if bus:
                channel = payload.get("channel", "telegram")
                message = payload.get("message", "")
                user_id = payload.get("userId")
                outbound = OutboundMessage(
                    channel=channel,
                    chat_id=user_id,
                    content=message,
                )
                await bus.publish_outbound(outbound)
                result = {"success": True, "channel": channel, "userId": user_id}
                logger.info("Queued outbound message for {} to user {}", channel, user_id)

        try:
            await self._client.post(
                f"/picobot/commands/{cmd_id}/complete",
                json={"result": result},
            )
        except Exception:
            pass


# Global instance
_soothsayer_service: SoothsayerService | None = None


def init_soothsayer_service(config: SoothsayerConfig) -> SoothsayerService:
    """Initialize the global Soothsayer service."""
    global _soothsayer_service
    _soothsayer_service = SoothsayerService(config)
    return _soothsayer_service


def get_soothsayer_service() -> SoothsayerService | None:
    """Get the global Soothsayer service instance."""
    return _soothsayer_service
