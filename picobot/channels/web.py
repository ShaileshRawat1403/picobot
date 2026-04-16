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
    """Web channel that provides a WebSocket server for web-based chat interfaces."""

    name = "web"
    display_name = "Web"

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._server = None
        self._clients: set[websockets.WebSocketServerProtocol] = set()
        self._http_server = None

    async def start(self) -> None:
        """Start the WebSocket and HTTP server."""
        host = getattr(self.config, "host", "0.0.0.0")
        port = getattr(self.config, "port", 18791)

        self._running = True
        logger.info(
            "Starting Web server on http://{}:{} (WebSocket: ws://{}:{})", host, port, host, port
        )

        # Load index.html
        web_dir = Path(__file__).parent.parent / "web"
        self._index_html = (web_dir / "index.html").read_text("utf-8")

        self._server = await websockets.serve(
            self._handle_connection,
            host,
            port,
        )

        # Start HTTP server for HTML and API on port+1
        self._http_server = await asyncio.start_server(self._handle_http, host, port + 1)

        while self._running:
            await asyncio.sleep(1)

    async def _handle_http(self, reader, writer):
        """Handle HTTP requests for HTML and API."""
        try:
            data = await reader.read(10000)
            request = data.decode()
            lines = request.split("\n")
            path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"

            if path == "/":
                response = self._index_html.encode()
                body = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                    + str(len(response)).encode()
                    + b"\r\n\r\n"
                    + response
                )
                writer.write(body)
            elif path == "/api/settings":
                from picobot.config.loader import load_config
                from picobot.config.paths import get_workspace_path

                try:
                    config = load_config()
                    workspace = get_workspace_path()
                    soul_path = workspace / "SOUL.md"
                    system_prompt = soul_path.read_text("utf-8") if soul_path.exists() else ""

                    model = config.agents.defaults.model
                    dax = {
                        "enabled": config.dax.enabled,
                        "url": config.dax.url,
                        "admins": config.dax.admin_numbers,
                    }

                    mcp_servers = []
                    for name, server in config.tools.mcp_servers.items():
                        mcp_servers.append({"name": name, "type": server.type or "unknown"})

                    tools = {
                        "shell": {"enabled": True, "timeout": config.tools.exec.timeout},
                        "web_search": {"enabled": bool(config.tools.web.search.api_key)},
                        "web_fetch": {"enabled": True},
                        "filesystem": {"enabled": True},
                    }

                    response = json.dumps(
                        {
                            "model": model,
                            "system_prompt": system_prompt,
                            "workspace": str(workspace),
                            "dax": dax,
                            "mcp_servers": mcp_servers,
                            "tools": tools,
                        }
                    ).encode()

                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(response)).encode()
                        + b"\r\n\r\n"
                        + response
                    )
                except Exception as e:
                    logger.error(f"Settings API error: {e}")
                    err = json.dumps({"error": str(e)}).encode()
                    writer.write(
                        b"HTTP/1.1 500 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(err)).encode()
                        + b"\r\n\r\n"
                        + err
                    )
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        except Exception as e:
            logger.error(f"HTTP error: {e}")
        finally:
            writer.close()

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._http_server:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None

        # Close all active client connections
        if self._clients:
            await asyncio.gather(
                *[client.close() for client in self._clients], return_exceptions=True
            )
            self._clients.clear()

    async def send(self, msg: OutboundMessage) -> None:
        """Send an outbound message to all connected web clients or a specific one."""
        if not self._clients:
            return

        payload = json.dumps(
            {
                "type": "message",
                "content": msg.content,
                "chat_id": msg.chat_id,
                "metadata": msg.metadata,
            },
            ensure_ascii=False,
        )

        # Broadcast to all clients
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
                # Handle incoming messages
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.remove(websocket)
            logger.info("Web client disconnected. Total clients: {}", len(self._clients))
