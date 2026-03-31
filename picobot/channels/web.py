"""Web channel implementation using WebSocket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import websockets
from loguru import logger

from picobot.bus.events import OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.base import BaseChannel


class WebChannel(BaseChannel):
    """
    Web channel that provides a WebSocket server for web-based chat interfaces.
    """

    name = "web"
    display_name = "Web"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._server = None
        self._clients: set[websockets.WebSocketServerProtocol] = set()

    async def start(self) -> None:
        """Start the WebSocket and HTTP server."""
        host = getattr(self.config, "host", "0.0.0.0")
        port = getattr(self.config, "port", 18791)

        self._running = True
        logger.info("Starting Web server on http://{}:{} (WebSocket: ws://{}:{})", host, port, host, port)

        # Load index.html
        web_dir = Path(__file__).parent.parent / "web"
        self._index_html = (web_dir / "index.html").read_text("utf-8")

        self._server = await websockets.serve(
            self._handle_connection, host, port,
            process_request=self._process_request
        )

        while self._running:
            await asyncio.sleep(1)

    async def _process_request(self, path: str, request_headers: Any) -> tuple[int, dict[str, str], bytes] | None:
        """Serve index.html for HTTP GET requests to root."""
        if path == "/":
            return 200, {"Content-Type": "text/html; charset=utf-8"}, self._index_html.encode("utf-8")
        return None

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Close all active client connections
        if self._clients:
            await asyncio.gather(
                *[client.close() for client in self._clients],
                return_exceptions=True
            )
            self._clients.clear()

    async def send(self, msg: OutboundMessage) -> None:
        """Send an outbound message to all connected web clients or a specific one."""
        if not self._clients:
            return

        payload = json.dumps({
            "type": "message",
            "content": msg.content,
            "chat_id": msg.chat_id,
            "metadata": msg.metadata,
        }, ensure_ascii=False)

        # If chat_id matches a specific client ID (e.g. session ID), we could target it.
        # For now, broadcast to all clients for simplicity.
        disconnected = set()
        for client in self._clients:
            try:
                await client.send(payload)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)

        for client in disconnected:
            self._clients.remove(client)

    async def _handle_connection(self, websocket: websockets.WebSocketServerProtocol):
        """Handle a new WebSocket client connection."""
        self._clients.add(websocket)
        logger.info("New web client connected. Total clients: {}", len(self._clients))

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get("type") == "message":
                        content = data.get("content")
                        chat_id = data.get("chat_id", "web-session")
                        sender_id = data.get("sender_id", "web-user")

                        await self._handle_message(
                            sender_id=sender_id,
                            chat_id=chat_id,
                            content=content,
                            metadata=data.get("metadata", {})
                        )
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON from web client")
                except Exception as e:
                    logger.error("Error handling web client message: {}", e)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.remove(websocket)
            logger.info("Web client disconnected. Total clients: {}", len(self._clients))
