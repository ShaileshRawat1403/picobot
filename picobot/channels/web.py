"""Web channel implementation using WebSocket."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import websockets
from loguru import logger

from picobot.bus.events import OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.base import BaseChannel


class WebChannel(BaseChannel):
    """Web channel that provides a WebSocket server for web-based chat interfaces."""

    name = "web"
    display_name = "Web"
    _BROWSER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
    _LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
    _SESSION_TITLE_KEY = "pico_web_title"
    _MAX_SESSION_TITLE_LENGTH = 72
    _OPERATIONS_TOOL_NAMES = {
        "list_skills",
        "get_skill",
        "web_search",
        "web_fetch",
        "browser_read_shared_tab",
    }

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._server = None
        self._clients: dict[websockets.WebSocketServerProtocol, str] = {}
        self._chat_clients: dict[str, websockets.WebSocketServerProtocol] = {}
        self._http_server = None

    async def start(self) -> None:
        """Start the WebSocket and HTTP server."""
        host = getattr(self.config, "host", "0.0.0.0")
        port = getattr(self.config, "port", 18791)
        if host not in self._LOCAL_HOSTS:
            raise ValueError(
                "Pico's web workbench is local-only. Bind it to 127.0.0.1 until authenticated remote hosting is implemented."
            )

        self._running = True
        logger.info(
            "Starting Web UI on http://{}:{} (WebSocket: ws://{}:{})", host, port + 1, host, port
        )

        # Load index.html
        web_dir = Path(__file__).parent.parent / "web"
        self._index_html = (web_dir / "index.html").read_text("utf-8").replace(
            "__PICO_WS_PORT__", str(port)
        ).replace("__PICO_API_PORT__", str(port + 1))

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
            data = await reader.read(65536)
            request_head, _, body = data.partition(b"\r\n\r\n")
            request = request_head.decode("utf-8", errors="replace")
            lines = request.split("\n")
            headers = self._request_headers(lines[1:])
            method = lines[0].split(" ", 1)[0].upper() if lines else "GET"
            request_target = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
            request_url = urlsplit(request_target)
            path = request_url.path
            query = parse_qs(request_url.query)

            if path.startswith("/api/") and not self._is_local_http_peer(writer):
                self._write_response(writer, 403, b'{"error":"Pico web API is local-only"}')
                return

            if path == "/":
                response = self._index_html.encode()
                body = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                    + str(len(response)).encode()
                    + b"\r\n\r\n"
                    + response
                )
                writer.write(body)
            elif path == "/api/health":
                response = json.dumps(
                    {
                        "status": "ok",
                        "channel": self.name,
                        "connected_clients": len(self._clients),
                    }
                ).encode()
                self._write_response(writer, 200, response)
            elif path == "/api/artifacts":
                try:
                    client_id = self._browser_id_from_query(query)
                    owner_id = self._memory_owner(client_id)
                    store = self._artifact_store()
                    if method == "POST":
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        title = payload.get("title")
                        content = payload.get("content")
                        if not isinstance(title, str) or not isinstance(content, str):
                            raise ValueError("Artifact title and content are required")
                        artifact = store.create(
                            owner_id=owner_id,
                            session_key=self._session_key(client_id, session_id),
                            title=title,
                            content=content,
                            kind=str(payload.get("kind") or "note"),
                            content_type=str(payload.get("content_type") or "text/markdown"),
                        )
                        self._write_response(
                            writer, 201, json.dumps({"artifact": asdict(artifact)}).encode()
                        )
                    else:
                        session_id = self._single_query_value(query, "session_id")
                        session_key = (
                            self._session_key(client_id, session_id) if session_id is not None else None
                        )
                        artifacts = store.list(owner_id, session_key=session_key)
                        self._write_response(
                            writer,
                            200,
                            json.dumps({"artifacts": [asdict(item) for item in artifacts]}).encode(),
                        )
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/artifacts/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    owner_id = self._memory_owner(client_id)
                    store = self._artifact_store()
                    artifact_path = path.removeprefix("/api/artifacts/").strip("/")
                    if artifact_path.endswith("/download") and method == "GET":
                        artifact_id = artifact_path.removesuffix("/download").rstrip("/")
                        artifact = store.get(owner_id, artifact_id)
                        content = store.read_content(owner_id, artifact_id).encode("utf-8")
                        self._write_raw_response(
                            writer,
                            200,
                            content,
                            artifact.content_type,
                            f'attachment; filename="{artifact_id}-v{artifact.revision}"',
                        )
                    elif artifact_path.endswith("/revisions") and method == "POST":
                        artifact_id = artifact_path.removesuffix("/revisions").rstrip("/")
                        payload = self._json_body(body)
                        content = payload.get("content")
                        if not isinstance(content, str):
                            raise ValueError("Artifact content is required")
                        artifact = store.revise(owner_id, artifact_id, content)
                        self._write_response(
                            writer, 200, json.dumps({"artifact": asdict(artifact)}).encode()
                        )
                    elif method == "GET" and "/" not in artifact_path:
                        artifact = store.get(owner_id, artifact_path)
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {
                                    "artifact": asdict(artifact),
                                    "content": store.read_content(owner_id, artifact.id),
                                    "revisions": [
                                        asdict(revision)
                                        for revision in store.revisions(owner_id, artifact.id)
                                    ],
                                },
                                ensure_ascii=False,
                            ).encode(),
                        )
                    else:
                        self._write_response(writer, 404, self._json_error("Artifact route was not found"))
                except (ValueError, KeyError, FileNotFoundError) as exc:
                    self._write_response(writer, 404, self._json_error(str(exc)))
            elif path == "/api/missions":
                try:
                    client_id = self._browser_id_from_query(query)
                    if method == "POST":
                        payload = self._json_body(body)
                        mission = self._create_browser_mission(
                            client_id,
                            payload.get("session_id"),
                            payload.get("title"),
                            payload.get("objective"),
                            payload.get("current_step"),
                        )
                        self._write_response(
                            writer, 201, json.dumps({"mission": mission}, ensure_ascii=False).encode()
                        )
                    else:
                        session_id = self._single_query_value(query, "session_id")
                        state = self._single_query_value(query, "state") or None
                        missions = self._list_browser_missions(client_id, session_id=session_id, state=state)
                        self._write_response(
                            writer, 200, json.dumps({"missions": missions}, ensure_ascii=False).encode()
                        )
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/missions/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    mission_path = path.removeprefix("/api/missions/").strip("/")
                    mission_id, _, operation = mission_path.partition("/")
                    if not mission_id:
                        raise ValueError("Mission route was not found")
                    if method == "GET" and not operation:
                        self._write_response(
                            writer,
                            200,
                            json.dumps(self._browser_mission_detail(client_id, mission_id), ensure_ascii=False).encode(),
                        )
                    elif method == "POST" and operation == "transition":
                        payload = self._json_body(body)
                        mission = self._transition_browser_mission(
                            client_id, mission_id, payload.get("state"), payload.get("blocked_reason")
                        )
                        self._write_response(
                            writer, 200, json.dumps({"mission": mission}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation == "step":
                        mission = self._set_browser_mission_step(
                            client_id, mission_id, self._json_body(body).get("current_step")
                        )
                        self._write_response(
                            writer, 200, json.dumps({"mission": mission}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation == "checkpoints":
                        payload = self._json_body(body)
                        checkpoint = self._add_browser_mission_checkpoint(
                            client_id, mission_id, payload.get("kind"), payload.get("summary")
                        )
                        self._write_response(
                            writer,
                            201,
                            json.dumps({"checkpoint": checkpoint}, ensure_ascii=False).encode(),
                        )
                    else:
                        raise ValueError("Mission route was not found")
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/runs":
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = self._single_query_value(query, "session_id")
                    response = json.dumps(
                        self._browser_runs(client_id, session_id), ensure_ascii=False
                    ).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/sessions":
                try:
                    client_id = self._browser_id_from_query(query)
                    response = json.dumps(
                        {"sessions": self._list_browser_sessions(client_id)}, ensure_ascii=False
                    ).encode()
                    self._write_response(writer, 200, response)
                except ValueError as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/runtime/policy":
                try:
                    client_id = self._browser_id_from_query(query)
                    service = self._policy_service()
                    session_id = self._single_query_value(query, "session_id")
                    if session_id is not None:
                        session_id = self._valid_browser_id(session_id)
                        self._require_browser_session(client_id, session_id)
                        manager = self._session_manager()
                        session = manager.get_or_create(self._session_key(client_id, session_id))
                        if method == "PUT":
                            summary = service.set_session_override(session, self._json_body(body))
                            session.updated_at = datetime.now()
                            manager.save(session)
                            response = json.dumps(summary, ensure_ascii=False).encode()
                        else:
                            response = json.dumps(
                                service.session_summary(session), ensure_ascii=False
                            ).encode()
                    elif method == "PUT":
                        response = json.dumps(
                            service.set_global(self._json_body(body)), ensure_ascii=False
                        ).encode()
                    else:
                        response = json.dumps(service.summary(), ensure_ascii=False).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/operations":
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = self._single_query_value(query, "session_id")
                    response = json.dumps(
                        self._browser_operations(client_id, session_id), ensure_ascii=False
                    ).encode()
                    self._write_response(writer, 200, response)
                except ValueError as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/providers":
                try:
                    # Provider setup is local-workbench only. The opaque browser
                    # identity keeps this mutation path consistent with the rest
                    # of Pico's local owner-scoped API surface.
                    self._browser_id_from_query(query)
                    setup = self._provider_setup()
                    if method == "GET":
                        response = json.dumps(setup.inventory(), ensure_ascii=False).encode()
                    elif method == "POST":
                        payload = self._json_body(body)
                        response = json.dumps(
                            setup.choose_default(payload.get("provider"), payload.get("model")),
                            ensure_ascii=False,
                        ).encode()
                    else:
                        raise ValueError("Provider route was not found")
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/providers/custom-endpoint" and method == "POST":
                try:
                    self._browser_id_from_query(query)
                    payload = self._json_body(body)
                    response = json.dumps(
                        self._provider_setup().configure_custom_endpoint(
                            payload.get("endpoint"), payload.get("model"), payload.get("api_key")
                        ),
                        ensure_ascii=False,
                    ).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/providers/"):
                try:
                    self._browser_id_from_query(query)
                    provider_path = path.removeprefix("/api/providers/").strip("/")
                    provider_name, _, operation = provider_path.partition("/")
                    setup = self._provider_setup()
                    if method == "POST" and operation == "api-key":
                        response = json.dumps(
                            {
                                "provider": setup.configure_api_key(
                                    provider_name, self._json_body(body).get("api_key")
                                )
                            },
                            ensure_ascii=False,
                        ).encode()
                    elif method == "POST" and operation == "test":
                        response = json.dumps(
                            await setup.test_connection(provider_name), ensure_ascii=False
                        ).encode()
                    else:
                        raise ValueError("Provider route was not found")
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path == "/api/browser-bridge/tickets":
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = self._valid_browser_id(self._json_body(body).get("session_id"))
                    self._require_browser_session(client_id, session_id)
                    code = self._browser_bridge_store().create_ticket(
                        self._memory_owner(client_id), self._session_key(client_id, session_id)
                    )
                    self._write_response(writer, 201, json.dumps({"pairing_code": code}).encode())
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path == "/api/browser-bridge/share":
                try:
                    payload = self._json_body(body)
                    tab, token = self._browser_bridge_store().share(
                        payload.get("pairing_code"),
                        payload.get("tab_id"),
                        payload.get("url"),
                        payload.get("title"),
                        payload.get("visible_text"),
                    )
                    self._write_response(
                        writer,
                        201,
                        json.dumps({"share": tab.to_dict(), "bridge_token": token}, ensure_ascii=False).encode(),
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path == "/api/browser-bridge/refresh":
                try:
                    payload = self._json_body(body)
                    tab = self._browser_bridge_store().refresh(
                        payload.get("share_id"),
                        headers.get("x-pico-bridge-token"),
                        payload.get("tab_id"),
                        payload.get("url"),
                        payload.get("title"),
                        payload.get("visible_text"),
                    )
                    self._write_response(writer, 200, json.dumps({"share": tab.to_dict()}, ensure_ascii=False).encode())
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path == "/api/browser-bridge/revoke":
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = self._valid_browser_id(self._json_body(body).get("session_id"))
                    self._browser_bridge_store().revoke(
                        self._memory_owner(client_id), self._session_key(client_id, session_id)
                    )
                    self._write_response(writer, 200, b'{"revoked":true}')
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/actions/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    action_path = path.removeprefix("/api/actions/").strip("/")
                    action_id, _, decision = action_path.partition("/")
                    payload = self._json_body(body)
                    session_id = self._valid_browser_id(payload.get("session_id"))
                    if not action_id or decision not in {"approve", "reject"}:
                        raise ValueError("Action route was not found")
                    action = self._action_store().resolve(
                        self._memory_owner(client_id),
                        action_id,
                        self._session_key(client_id, session_id),
                        decision,
                    )
                    self._write_response(
                        writer, 200, json.dumps({"action": action.to_dict()}, ensure_ascii=False).encode()
                    )
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/sessions/") and path.endswith("/profile"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/profile").rstrip("/")
                    profile = self._set_browser_session_profile(
                        client_id, session_id, self._json_body(body).get("profile")
                    )
                    self._write_response(writer, 200, json.dumps({"profile": profile}).encode())
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/sessions/") and path.endswith("/title"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/title").rstrip("/")
                    payload = self._json_body(body)
                    title = self._set_browser_session_title(client_id, session_id, payload.get("title"))
                    self._write_response(writer, 200, json.dumps({"id": session_id, "title": title}).encode())
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "GET" and path.startswith("/api/sessions/") and path.endswith("/context"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/context").rstrip("/")
                    response = json.dumps(
                        self._browser_session_context(client_id, session_id), ensure_ascii=False
                    ).encode()
                    self._write_response(writer, 200, response)
                except ValueError as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/sessions/") and path.endswith("/skill-proposals"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/skill-proposals").rstrip("/")
                    session_key = self._session_key(client_id, session_id)
                    self._require_browser_session(client_id, session_id)
                    payload = self._json_body(body)
                    proposal = self._skill_proposal_store().create(
                        owner_id=self._memory_owner(client_id),
                        session_key=session_key,
                        name=payload.get("name"),
                        description=payload.get("description"),
                        guidance=payload.get("guidance", ""),
                    )
                    self._write_response(
                        writer, 201, json.dumps({"proposal": asdict(proposal)}, ensure_ascii=False).encode()
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/sessions/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/")
                    response = json.dumps(
                        self._browser_transcript(client_id, session_id), ensure_ascii=False
                    ).encode()
                    self._write_response(writer, 200, response)
                except ValueError as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/skill-proposals":
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = self._single_query_value(query, "session_id")
                    session_key = self._session_key(client_id, session_id) if session_id else None
                    proposals = self._skill_proposal_store().list(
                        self._memory_owner(client_id), session_key=session_key
                    )
                    self._write_response(
                        writer,
                        200,
                        json.dumps({"proposals": [asdict(item) for item in proposals]}, ensure_ascii=False).encode(),
                    )
                except ValueError as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/skill-proposals/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    owner_id = self._memory_owner(client_id)
                    proposal_path = path.removeprefix("/api/skill-proposals/").strip("/")
                    proposal_id, _, operation = proposal_path.partition("/")
                    if not proposal_id or operation not in {"revise", "approve", "reject"}:
                        raise ValueError("Skill proposal route was not found")
                    store = self._skill_proposal_store()
                    if operation == "revise":
                        proposal = store.revise(owner_id, proposal_id, self._json_body(body).get("content"))
                    elif operation == "approve":
                        proposal = store.approve(owner_id, proposal_id)
                    else:
                        proposal = store.reject(owner_id, proposal_id)
                    self._write_response(
                        writer, 200, json.dumps({"proposal": asdict(proposal)}, ensure_ascii=False).encode()
                    )
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/memory":
                try:
                    client_id = self._browser_id_from_query(query)
                    owner_id = self._memory_owner(client_id)
                    store = self._memory_store()
                    if method == "POST":
                        payload = self._json_body(body)
                        value = payload.get("value")
                        if not isinstance(value, str):
                            raise ValueError("Memory value is required")
                        status = payload.get("status", "confirmed")
                        if status == "confirmed":
                            item = store.remember(owner_id, value)
                        elif status == "proposed":
                            item = store.propose(
                                owner_id,
                                value,
                                source_type="explicit_user_proposal",
                            )
                        else:
                            raise ValueError("New memory must be confirmed or proposed")
                        response = json.dumps({"memory": asdict(item)}, ensure_ascii=False).encode()
                        self._write_response(writer, 201, response)
                    else:
                        status = self._single_query_value(query, "status") or None
                        search = self._single_query_value(query, "search") or ""
                        limit_value = self._single_query_value(query, "limit")
                        limit = int(limit_value) if limit_value is not None else 20
                        items = (
                            store.search(owner_id, search, status=status, limit=limit)
                            if search
                            else store.list(owner_id, status=status, limit=limit)
                        )
                        response = json.dumps(
                            {"memory": [asdict(item) for item in items]}, ensure_ascii=False
                        ).encode()
                        self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/memory/") and path.endswith("/transition"):
                try:
                    client_id = self._browser_id_from_query(query)
                    memory_id = path.removeprefix("/api/memory/").removesuffix("/transition")
                    status = self._json_body(body).get("status")
                    if not isinstance(status, str):
                        raise ValueError("Memory status is required")
                    store = self._memory_store()
                    item = store.transition(self._memory_owner(client_id), memory_id, status)
                    response = json.dumps({"memory": asdict(item)}, ensure_ascii=False).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/memory/") and path.endswith("/forget"):
                try:
                    client_id = self._browser_id_from_query(query)
                    memory_id = path.removeprefix("/api/memory/").removesuffix("/forget")
                    store = self._memory_store()
                    item = store.transition(self._memory_owner(client_id), memory_id, "forgotten")
                    response = json.dumps({"memory": asdict(item)}, ensure_ascii=False).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, KeyError) as exc:
                    self._write_response(writer, 404, self._json_error(str(exc)))
            elif path == "/api/settings":
                try:
                    config = self._runtime_config()
                    workspace = config.workspace_path
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
            elif path == "/api/dax/status":
                import httpx

                try:
                    config = self._runtime_config()
                    if not config.dax.enabled or not config.dax.url:
                        response = json.dumps({"error": "DAX not enabled"}).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                            + str(len(response)).encode()
                            + b"\r\n\r\n"
                            + response
                        )
                        return

                    async def fetch_dax_status():
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            r = await client.get(f"{config.dax.url}/soothsayer/overview")
                            if r.status_code == 200:
                                return r.json()
                            return {"error": f"DAX returned {r.status_code}"}

                    status = asyncio.create_task(fetch_dax_status())
                    data = await asyncio.wait_for(status, timeout=15.0)
                    response = json.dumps(data).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(response)).encode()
                        + b"\r\n\r\n"
                        + response
                    )
                except Exception as e:
                    logger.error(f"DAX status API error: {e}")
                    err = json.dumps({"error": str(e)}).encode()
                    writer.write(
                        b"HTTP/1.1 500 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(err)).encode()
                        + b"\r\n\r\n"
                        + err
                    )
            elif path == "/api/dax/stream":
                import httpx

                try:
                    config = self._runtime_config()
                    if not config.dax.enabled or not config.dax.url:
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
                        return

                    async def stream_dax_events():
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            r = await client.get(
                                f"{config.dax.url}/soothsayer/overview",
                                headers={"Accept": "text/event-stream"},
                            )
                            async for line in r.aiter_lines():
                                if line.strip():
                                    yield f"data: {line}\n\n".encode()

                    writer.write(
                        b"HTTP/1.1 200 OK\r\n"
                        + b"Content-Type: text/event-stream\r\n"
                        + b"Cache-Control: no-cache\r\n"
                        + b"Connection: keep-alive\r\n"
                        + b"Access-Control-Allow-Origin: *\r\n\r\n"
                    )
                    async for chunk in stream_dax_events():
                        writer.write(chunk)
                        await writer.drain()
                except Exception as e:
                    logger.error(f"DAX stream API error: {e}")
                except Exception as e:
                    logger.error(f"HTTP error: {e}")
                finally:
                    writer.close()
            elif path == "/api/skills":
                from picobot.agent.skills import SkillsLoader
                try:
                    workspace = self._runtime_config().workspace_path
                    loader = SkillsLoader(workspace)
                    skills = []
                    for skill in loader.list_skills(filter_unavailable=False):
                        metadata = loader.get_skill_metadata(skill["name"]) or {}
                        skill_meta = loader._get_skill_meta(skill["name"])
                        skills.append(
                            {
                                **skill,
                                "description": metadata.get("description", skill["name"]),
                                "available": loader._check_requirements(skill_meta),
                                "requires": loader._get_missing_requirements(skill_meta),
                            }
                        )
                    response = json.dumps({"skills": skills}).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(response)).encode()
                        + b"\r\n\r\n"
                        + response
                    )
                except Exception as e:
                    logger.error(f"Skills API error: {e}")
                    err = json.dumps({"error": str(e)}).encode()
                    writer.write(
                        b"HTTP/1.1 500 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(err)).encode()
                        + b"\r\n\r\n"
                        + err
                    )
            elif path.startswith("/api/webhook"):
                from picobot.bus.webhooks import create_webhook_manager
                try:
                    config = self._runtime_config()
                    webhook_cfg = config.webhook
                    if not webhook_cfg.inbound_enabled:
                        writer.write(b"HTTP/1.1 404 Not Enabled\r\nContent-Length: 0\r\n\r\n")
                        writer.close()
                        return

                    if body:
                        headers = {"x-webhook-signature": ""}
                        for line in lines:
                            if line.startswith("x-webhook-signature:"):
                                headers["x-webhook-signature"] = line.split(":", 1)[1].strip()

                        manager = create_webhook_manager(self.bus)
                        msg = await manager.handle_inbound(path, body, headers)
                        if msg:
                            await self.bus.publish_inbound(msg)
                            writer.write(
                                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
                            )
                        else:
                            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                    else:
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                except Exception as e:
                    logger.error(f"Webhook error: {e}")
                    writer.write(b"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n")
                writer.close()
                return
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        except Exception as e:
            logger.error(f"HTTP error: {e}")
        finally:
            writer.close()

    @classmethod
    def _valid_browser_id(cls, value: object) -> str:
        if not isinstance(value, str) or not cls._BROWSER_ID_RE.fullmatch(value):
            raise ValueError("Browser identity is invalid. Reload Pico and try again.")
        return value

    @classmethod
    def _browser_id_from_query(cls, query: dict[str, list[str]]) -> str:
        values = query.get("client_id", [])
        if len(values) != 1:
            raise ValueError("Browser identity is required")
        return cls._valid_browser_id(values[0])

    @classmethod
    def _session_key(cls, client_id: str, session_id: str) -> str:
        return f"web:web:{cls._valid_browser_id(client_id)}:{cls._valid_browser_id(session_id)}"

    @classmethod
    def _chat_id(cls, client_id: str, session_id: str) -> str:
        return f"web:{cls._valid_browser_id(client_id)}:{cls._valid_browser_id(session_id)}"

    @classmethod
    def _memory_owner(cls, client_id: str) -> str:
        return f"web:browser:{cls._valid_browser_id(client_id)}"

    @staticmethod
    def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key, [])
        if not values:
            return None
        if len(values) != 1:
            raise ValueError(f"Use one {key} value")
        return values[0]

    @staticmethod
    def _request_headers(lines: list[str]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in lines:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        return headers

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any]:
        if not body:
            raise ValueError("JSON request body is required")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    @staticmethod
    def _json_error(message: str) -> bytes:
        return json.dumps({"error": message}).encode()

    @staticmethod
    def _write_response(writer, status: int, response: bytes) -> None:
        status_text = {200: "OK", 201: "Created", 400: "Bad Request", 403: "Forbidden", 404: "Not Found"}.get(
            status, "Error"
        )
        writer.write(
            f"HTTP/1.1 {status} {status_text}\r\n".encode()
            + b"Content-Type: application/json\r\nContent-Length: "
            + str(len(response)).encode()
            + b"\r\n\r\n"
            + response
        )

    @staticmethod
    def _write_raw_response(
        writer,
        status: int,
        response: bytes,
        content_type: str,
        content_disposition: str | None = None,
    ) -> None:
        status_text = {200: "OK", 201: "Created"}.get(status, "Error")
        headers = [
            f"HTTP/1.1 {status} {status_text}",
            f"Content-Type: {content_type}; charset=utf-8",
            f"Content-Length: {len(response)}",
        ]
        if content_disposition:
            headers.append(f"Content-Disposition: {content_disposition}")
        writer.write("\r\n".join(headers).encode() + b"\r\n\r\n" + response)

    @staticmethod
    def _is_local_http_peer(writer) -> bool:
        peer = writer.get_extra_info("peername")
        return peer is None or peer[0] in {"127.0.0.1", "::1"}

    def _session_manager(self):
        from picobot.session.manager import SessionManager

        return SessionManager(self._runtime_config().workspace_path)

    def _memory_store(self):
        from picobot.memory.store import PersonalMemoryStore

        return PersonalMemoryStore(self._runtime_config().workspace_path)

    def _compaction_store(self):
        from picobot.context.store import CompactionStore

        return CompactionStore(self._runtime_config().workspace_path)

    def _artifact_store(self):
        from picobot.artifacts.store import ArtifactStore

        return ArtifactStore(self._runtime_config().workspace_path)

    @staticmethod
    def _provider_setup():
        from picobot.providers.setup import ProviderSetupService

        return ProviderSetupService()

    @staticmethod
    def _policy_service():
        from picobot.policy.runtime import RuntimePolicyService

        return RuntimePolicyService()

    def _mission_store(self):
        from picobot.missions import MissionStore

        return MissionStore(self._runtime_config().workspace_path)

    def _run_store(self):
        from picobot.runs import RunStore

        return RunStore(self._runtime_config().workspace_path)

    def _browser_runs(self, client_id: str, session_id: str | None) -> dict[str, Any]:
        """Return safe turn receipts; never transcripts, args, or reasoning."""
        owner_id = self._memory_owner(client_id)
        store = self._run_store()
        session_key = None
        if session_id is not None:
            session_key = self._session_key(client_id, self._valid_browser_id(session_id))
        runs = store.list(owner_id, session_key=session_key, limit=20)
        active = None
        if session_key:
            active_run = store.get_active(owner_id, session_key)
            active = active_run.turn_receipt() if active_run else None
        return {
            "runs": [run.turn_receipt() for run in runs],
            "active": active,
        }

    def _mission_evidence(self, owner_id: str, session_key: str, mission_id: str) -> dict[str, int]:
        """Return counts only; linked records remain in their own stores."""
        return {
            "artifact_count": len(self._artifact_store().list(owner_id, session_key=session_key)),
            "activity_count": len(self._tool_activity_store().list(owner_id, session_key, limit=100)),
            "checkpoint_count": len(self._mission_store().checkpoints(owner_id, mission_id)),
        }

    def _create_browser_mission(
        self,
        client_id: str,
        session_id: object,
        title: object,
        objective: object,
        current_step: object,
    ) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        mission = self._mission_store().create(
            owner_id=self._memory_owner(client_id),
            session_key=self._session_key(client_id, session_id),
            title=title,
            objective=objective,
            current_step=current_step,
        )
        return mission.to_dict()

    def _list_browser_missions(
        self, client_id: str, *, session_id: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        owner_id = self._memory_owner(client_id)
        session_key = None
        if session_id is not None:
            session_id = self._valid_browser_id(session_id)
            self._require_browser_session(client_id, session_id)
            session_key = self._session_key(client_id, session_id)
        return [
            mission.to_dict()
            for mission in self._mission_store().list(
                owner_id, state=state, session_key=session_key
            )
        ]

    def _browser_mission_detail(self, client_id: str, mission_id: str) -> dict[str, Any]:
        owner_id = self._memory_owner(client_id)
        mission = self._mission_store().get(owner_id, mission_id)
        return {
            "mission": mission.to_dict(),
            "checkpoints": [
                item.to_dict() for item in self._mission_store().checkpoints(owner_id, mission.id)
            ],
            "events": [item.to_dict() for item in self._mission_store().events(owner_id, mission.id)],
            "evidence": self._mission_evidence(owner_id, mission.session_key, mission.id),
        }

    def _transition_browser_mission(
        self, client_id: str, mission_id: str, state: object, blocked_reason: object
    ) -> dict[str, Any]:
        if not isinstance(state, str):
            raise ValueError("Mission state is required")
        if blocked_reason is not None and not isinstance(blocked_reason, str):
            raise ValueError("Mission blocked reason must be text")
        return self._mission_store().transition(
            self._memory_owner(client_id), mission_id, state, blocked_reason
        ).to_dict()

    def _set_browser_mission_step(
        self, client_id: str, mission_id: str, current_step: object
    ) -> dict[str, Any]:
        if current_step is not None and not isinstance(current_step, str):
            raise ValueError("Mission current step must be text")
        return self._mission_store().set_current_step(
            self._memory_owner(client_id), mission_id, current_step
        ).to_dict()

    def _add_browser_mission_checkpoint(
        self, client_id: str, mission_id: str, kind: object, summary: object
    ) -> dict[str, Any]:
        if not isinstance(kind, str) or not isinstance(summary, str):
            raise ValueError("Mission checkpoint kind and summary are required")
        return self._mission_store().add_checkpoint(
            self._memory_owner(client_id), mission_id, kind, summary
        ).to_dict()

    def _skill_proposal_store(self):
        from picobot.learning.store import SkillProposalStore

        return SkillProposalStore(self._runtime_config().workspace_path)

    def _tool_activity_store(self):
        from picobot.operations.activity import ToolActivityStore

        return ToolActivityStore(self._runtime_config().workspace_path)

    def _action_store(self):
        from picobot.operations.actions import ProposedActionStore

        return ProposedActionStore(self._runtime_config().workspace_path)

    def _browser_bridge_store(self):
        from picobot.operations.browser_bridge import BrowserBridgeStore

        return BrowserBridgeStore(self._runtime_config().workspace_path)

    @staticmethod
    def _web_search_is_configured(config) -> bool:
        """Return a boolean only; browser APIs never receive configuration values.

        Pico's existing search tool has a safe DuckDuckGo HTML fallback when a
        configured provider lacks its optional credential, so the local tool
        remains usable without disclosing any setup state or secret.
        """
        return bool(getattr(config.tools.web.search, "provider", ""))

    def _browser_operations(self, client_id: str, session_id: str | None) -> dict[str, Any]:
        from picobot.operations.registry import CapabilityRegistry

        registry = CapabilityRegistry()
        session_key = self._session_key(client_id, session_id) if session_id else None
        profile = registry.resolve(None)
        if session_key:
            manager = self._session_manager()
            if any(item["key"] == session_key for item in manager.list_sessions()):
                profile = registry.resolve(manager.get_or_create(session_key).metadata.get("pico_operation_profile"))
        config = self._runtime_config()
        activities = (
            [item.to_dict() for item in self._tool_activity_store().list(self._memory_owner(client_id), session_key)]
            if session_key
            else []
        )
        actions = (
            [item.to_dict() for item in self._action_store().list(self._memory_owner(client_id), session_key)]
            if session_key
            else []
        )
        shared_tab = None
        if session_key:
            try:
                shared_tab = self._browser_bridge_store().get(self._memory_owner(client_id), session_key).to_dict()
            except KeyError:
                pass
        return {
            "profile": {"id": profile.id, "label": profile.label, "description": profile.description},
            "profiles": registry.profiles(),
            "capabilities": [
                item.to_dict()
                for item in registry.statuses(
                    profile.id,
                    self._OPERATIONS_TOOL_NAMES,
                    web_search_configured=self._web_search_is_configured(config),
                    browser_shared=shared_tab is not None,
                )
            ],
            "activity": activities,
            "actions": actions,
            "shared_browser_tab": shared_tab,
        }

    def _set_browser_session_profile(self, client_id: str, session_id: str, profile_id: object) -> dict[str, str]:
        from picobot.operations.registry import CapabilityRegistry

        session_id = self._valid_browser_id(session_id)
        registry = CapabilityRegistry()
        profile = registry.profile(profile_id)
        session = self._session_manager().get_or_create(self._session_key(client_id, session_id))
        session.metadata["pico_operation_profile"] = profile.id
        session.metadata.pop("pico_system_prompt", None)
        session.updated_at = datetime.now()
        self._session_manager().save(session)
        return {"id": profile.id, "label": profile.label, "description": profile.description}

    def _require_browser_session(self, client_id: str, session_id: str) -> None:
        key = self._session_key(client_id, session_id)
        if not any(item["key"] == key for item in self._session_manager().list_sessions()):
            raise ValueError("Session was not found for this browser identity")

    def _list_browser_sessions(self, client_id: str) -> list[dict[str, Any]]:
        prefix = f"web:web:{self._valid_browser_id(client_id)}:"
        manager = self._session_manager()
        result: list[dict[str, Any]] = []
        for item in manager.list_sessions():
            key = item["key"]
            if not key.startswith(prefix):
                continue
            session_id = key.removeprefix(prefix)
            if not self._BROWSER_ID_RE.fullmatch(session_id):
                continue
            session = manager.get_or_create(key)
            result.append(
                {
                    "id": session_id,
                    "title": self._browser_session_title(session),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "message_count": len(
                        [m for m in session.messages if m.get("role") in {"user", "assistant"}]
                    ),
                }
            )
        return result

    @classmethod
    def _clean_session_title(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Session title is required")
        title = " ".join(value.split())
        if not title:
            raise ValueError("Session title is required")
        if len(title) > cls._MAX_SESSION_TITLE_LENGTH:
            raise ValueError(
                f"Session titles are limited to {cls._MAX_SESSION_TITLE_LENGTH} characters"
            )
        return title

    @classmethod
    def _browser_session_title(cls, session) -> str:
        explicit_title = session.metadata.get(cls._SESSION_TITLE_KEY)
        if isinstance(explicit_title, str) and explicit_title.strip():
            return explicit_title
        messages = [message for message in session.messages if message.get("role") == "user"]
        first_message = str(messages[0].get("content", "")) if messages else "New session"
        return " ".join(first_message.split())[: cls._MAX_SESSION_TITLE_LENGTH] or "New session"

    def _set_browser_session_title(self, client_id: str, session_id: str, value: object) -> str:
        title = self._clean_session_title(value)
        key = self._session_key(client_id, session_id)
        manager = self._session_manager()
        session = manager.get_or_create(key)
        session.metadata[self._SESSION_TITLE_KEY] = title
        session.updated_at = datetime.now()
        manager.save(session)
        return title

    def _browser_transcript(self, client_id: str, session_id: str) -> dict[str, Any]:
        key = self._session_key(client_id, session_id)
        manager = self._session_manager()
        if not any(item["key"] == key for item in manager.list_sessions()):
            raise ValueError("Session was not found for this browser identity")
        session = manager.get_or_create(key)
        messages = [
            {
                "role": message["role"],
                "content": message.get("content", ""),
                "timestamp": message.get("timestamp"),
            }
            for message in session.messages
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]
        return {"id": session_id, "messages": messages}

    def _browser_session_context(self, client_id: str, session_id: str) -> dict[str, Any]:
        """Return the latest context record for one browser-owned session."""
        key = self._session_key(client_id, session_id)
        manager = self._session_manager()
        if not any(item["key"] == key for item in manager.list_sessions()):
            raise ValueError("Session was not found for this browser identity")
        session = manager.get_or_create(key)
        trace = session.metadata.get("pico_last_context")
        if not isinstance(trace, dict):
            trace = {}
        memory_ids = trace.get("memory_ids", [])
        if not isinstance(memory_ids, list):
            memory_ids = []
        store = self._memory_store()
        owner_id = self._memory_owner(client_id)
        memories = []
        for memory_id in memory_ids[:12]:
            if not isinstance(memory_id, str):
                continue
            try:
                memories.append(asdict(store.get(owner_id, memory_id)))
            except KeyError:
                continue
        history_count = trace.get("history_message_count", 0)
        owner_id = self._memory_owner(client_id)
        try:
            compaction_records = self._compaction_store().list(owner_id, key, limit=5)
        except Exception:
            compaction_records = []
        compaction_timeline = [record.public_view() for record in compaction_records]
        return {
            "session_id": session_id,
            "recorded_at": trace.get("recorded_at"),
            "history_message_count": history_count if isinstance(history_count, int) else 0,
            "memory": memories,
            "compaction": compaction_timeline[0] if compaction_timeline else None,
            "compaction_timeline": compaction_timeline,
        }

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
            self._chat_clients.clear()

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

        client = self._chat_clients.get(msg.chat_id)
        recipients = [client] if client else []
        # Only system-wide messages without a chat destination are broadcast.
        # Replies to a browser conversation remain private to that connection.
        if not recipients and not msg.chat_id:
            recipients = list(self._clients)

        disconnected = set()
        for client in recipients:
            try:
                await client.send(payload)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)

        for client in disconnected:
            self._forget_client(client)

    async def _handle_connection(self, websocket: websockets.WebSocketServerProtocol):
        """Handle a new WebSocket client connection."""
        chat_id: str | None = None
        sender_id: str | None = None
        logger.info("New web client connected. Total clients: {}", len(self._clients) + 1)

        try:
            async for message in websocket:
                try:
                    payload = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    await self._send_error(websocket, "Message must be valid JSON.")
                    continue

                if not isinstance(payload, dict):
                    await self._send_error(websocket, "Message must be a JSON object.")
                    continue

                if payload.get("type") == "hello":
                    try:
                        # The browser supplies opaque, random local IDs so it
                        # can resume its own work after a refresh. Pico owns
                        # channel, sender, and session key construction; raw
                        # sender/chat values are never accepted.
                        client_id = self._valid_browser_id(payload.get("client_id") or uuid4().hex)
                        session_id = self._valid_browser_id(payload.get("session_id") or uuid4().hex)
                    except ValueError as exc:
                        await self._send_error(websocket, str(exc))
                        continue

                    if chat_id:
                        self._forget_client(websocket)
                    chat_id = self._chat_id(client_id, session_id)
                    sender_id = f"browser:{client_id}"
                    self._clients[websocket] = chat_id
                    self._chat_clients[chat_id] = websocket
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "ready",
                                "chat_id": chat_id,
                                "sender_id": sender_id,
                                "client_id": client_id,
                                "session_id": session_id,
                            }
                        )
                    )
                    continue

                if payload.get("type") != "message":
                    await self._send_error(websocket, "Unsupported message type.")
                    continue

                if not chat_id or not sender_id:
                    await self._send_error(websocket, "Connect handshake first.")
                    continue

                content = payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    await self._send_error(websocket, "Message content cannot be empty.")
                    continue
                if len(content) > 20_000:
                    await self._send_error(websocket, "Message content is too long.")
                    continue

                await self._handle_message(
                    sender_id=sender_id,
                    chat_id=chat_id,
                    content=content.strip(),
                    metadata={"source": "browser"},
                )
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._forget_client(websocket)
            logger.info("Web client disconnected. Total clients: {}", len(self._clients))

    @staticmethod
    async def _send_error(websocket: websockets.WebSocketServerProtocol, message: str) -> None:
        await websocket.send(json.dumps({"type": "error", "content": message}))

    def _forget_client(self, websocket: websockets.WebSocketServerProtocol) -> None:
        chat_id = self._clients.pop(websocket, None)
        if chat_id:
            self._chat_clients.pop(chat_id, None)

    @staticmethod
    def _runtime_config():
        """Load the active profile selected when Pico was started."""
        from picobot.config.loader import load_config

        return load_config()
