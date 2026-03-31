"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
from collections import OrderedDict

from loguru import logger

from picobot.bus.events import OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.base import BaseChannel
from picobot.config.schema import WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @wppconnect-team/wppconnect to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"
    display_name = "WhatsApp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._ws = None
        self._connected = False
        self._authenticated = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws

                    # Send auth token if configured
                    if self.config.bridge_token:
                        logger.debug("Sending auth token to bridge...")
                        await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
                    else:
                        self._authenticated = True # Assume open if no token

                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._authenticated = False
                self._ws = None
                if self._running:
                    logger.warning("WhatsApp bridge connection error: {}. Reconnecting in 5s...", e)
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False
        self._authenticated = False

        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content
            }
            # Handle media if present in OutboundMessage metadata or content
            # (Assuming picobot's OutboundMessage might have media info)
            if hasattr(msg, 'media') and msg.media:
                payload["media"] = msg.media

            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            sender = data.get("sender", "")
            content = data.get("content", "")
            message_id = data.get("id", "")

            if message_id:
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = None
                while len(self._processed_message_ids) > 1000:
                    self._processed_message_ids.popitem(last=False)

            # Extract just the phone number as sender_id for the agent
            sender_id = sender.split("@")[0] if "@" in sender else sender

            # Filter by allow_from if configured in picobot
            if self.config.allow_from:
                # Normalize numbers for comparison
                def normalize(n): return ''.join(filter(str.isdigit, n))[-10:]
                norm_sender = normalize(sender_id)
                if not any(normalize(allowed) == norm_sender for allowed in self.config.allow_from):
                    logger.debug("Ignoring message from unauthorized sender: {}", sender_id)
                    return

            # Extract media paths
            media_paths = data.get("media") or []
            if media_paths:
                for p in media_paths:
                    mime, _ = mimetypes.guess_type(p)
                    media_type = "image" if mime and mime.startswith("image/") else "file"
                    media_tag = f"[{media_type}: {p}]"
                    content = f"{content}\n{media_tag}" if content else media_tag

            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False)
                }
            )

        elif msg_type == "status":
            status = data.get("status")
            logger.info("WhatsApp bridge status: {}", status)
            if status == "authenticated":
                self._authenticated = True
            elif status == "isLogged":
                self._connected = True

        elif msg_type == "qr":
            logger.info("New WhatsApp QR code received. Scan it in the terminal or bridge UI.")
            # If we were in a CLI, we could print it here, but bridge usually prints it.

        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get('error') or data.get('message'))
            if data.get('error') == 'Invalid token':
                self._running = False # Stop reconnecting if auth fails
