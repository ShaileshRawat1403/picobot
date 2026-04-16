"""Webhook system for inbound triggers and outbound notifications."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any, Callable

import httpx
from loguru import logger

from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus


class WebhookManager:
    """Manage webhooks for Picobot."""

    def __init__(self, bus: MessageBus):
        self.bus = bus
        self._handlers: dict[str, Callable] = {}
        self._outbound_config: dict[str, Any] = {}

    def register_handler(self, event: str, handler: Callable) -> None:
        """Register a handler for a webhook event."""
        self._handlers[event] = handler
        logger.info("Registered webhook handler for event: {}", event)

    def configure_outbound(
        self,
        url: str,
        secret: str = "",
        events: list[str] | None = None,
    ) -> None:
        """Configure outbound webhook."""
        self._outbound_config = {
            "url": url,
            "secret": secret,
            "events": events or ["run_completed", "run_failed", "approval_required"],
        }
        logger.info("Configured outbound webhook to: {}", url)

    async def send_webhook(
        self,
        event: str,
        payload: dict[str, Any],
    ) -> bool:
        """Send outbound webhook notification."""
        if not self._outbound_config.get("url"):
            logger.debug("Outbound webhook not configured")
            return False

        if event not in self._outbound_config.get("events", []):
            logger.debug("Event {} not in webhook events", event)
            return False

        url = self._outbound_config["url"]
        secret = self._outbound_config.get("secret", "")

        body = {
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "data": payload,
        }

        headers = {"Content-Type": "application/json"}
        if secret:
            body["signature"] = self._sign_payload(body, secret)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=body, headers=headers)
                if response.status_code < 400:
                    logger.info("Webhook sent: {} - {}", event, response.status_code)
                    return True
                logger.warning("Webhook failed: {} - {}", event, response.status_code)
                return False
        except Exception as e:
            logger.error("Webhook error: {}", e)
            return False

    @staticmethod
    def _sign_payload(payload: dict, secret: str) -> str:
        """Sign webhook payload with HMAC-SHA256."""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def verify_signature(payload: dict, secret: str, signature: str) -> bool:
        """Verify webhook signature."""
        expected = WebhookManager._sign_payload(payload, secret)
        return hmac.compare_digest(expected, signature)

    async def handle_inbound(
        self,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> InboundMessage | None:
        """Handle inbound webhook request."""
        from picobot.config.loader import load_config

        config = load_config()
        webhook_cfg = config.webhook

        if not webhook_cfg.inbound_enabled:
            return None

        if path != webhook_cfg.inbound_path:
            return None

        secret = webhook_cfg.inbound_secret
        signature = headers.get("x-webhook-signature", "")

        if secret and signature:
            try:
                payload = json.loads(body)
                if not self.verify_signature(payload, secret, signature):
                    logger.warning("Invalid webhook signature")
                    return None
            except Exception as e:
                logger.warning("Webhook validation error: {}", e)
                return None

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in webhook")
            return None

        content = data.get("content", data.get("message", ""))
        sender_id = data.get("sender_id", data.get("user", "webhook"))
        chat_id = data.get("chat_id", data.get("channel", "webhook")) or "webhook"

        return InboundMessage(
            channel="webhook",
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=str(content),
            metadata={"webhook": True, "raw": data},
        )


def create_webhook_manager(bus: MessageBus) -> WebhookManager:
    """Create and configure webhook manager."""
    from picobot.config.loader import load_config

    manager = WebhookManager(bus)

    config = load_config()
    webhook_cfg = config.webhook

    if webhook_cfg.enabled and webhook_cfg.url:
        manager.configure_outbound(
            url=webhook_cfg.url,
            secret=webhook_cfg.secret,
        )

    return manager
