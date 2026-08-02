"""Web channel implementation using WebSocket."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import zipfile

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
        "browser_action",
        "github_pr",
        "calendar",
        "read_file",
        "list_dir",
        "exec",
        "propose_workspace_change",
        "save_mission_artifact_draft",
    }

    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        self._server = None
        self._clients: dict[websockets.WebSocketServerProtocol, str] = {}
        self._chat_clients: dict[str, websockets.WebSocketServerProtocol] = {}
        self._http_server = None
        self._cron_service = None

    def set_cron_service(self, cron_service: Any) -> None:
        """Attach the gateway's durable scheduler to the local workbench."""
        self._cron_service = cron_service

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
            elif path == "/api/schedules":
                try:
                    client_id = self._browser_id_from_query(query)
                    if self._cron_service is None:
                        self._write_response(writer, 503, self._json_error("Scheduler is not available"))
                        return
                    if method == "GET":
                        schedules = [
                            self._browser_schedule_dict(job)
                            for job in self._browser_schedule_jobs(client_id)
                        ]
                        self._write_response(
                            writer, 200, json.dumps({"schedules": schedules}, ensure_ascii=False).encode()
                        )
                    elif method == "POST":
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        self._require_browser_session(client_id, session_id)
                        message = payload.get("message")
                        if not isinstance(message, str) or not message.strip():
                            raise ValueError("Schedule message is required")
                        if len(message) > 4000:
                            raise ValueError("Schedule message is too long")
                        schedule, delete_after_run = self._browser_schedule_payload(payload)
                        job = self._cron_service.add_job(
                            name=message.strip()[:30],
                            schedule=schedule,
                            message=message.strip(),
                            deliver=True,
                            channel="web",
                            to=self._chat_id(client_id, session_id),
                            delete_after_run=delete_after_run,
                        )
                        self._write_response(
                            writer,
                            201,
                            json.dumps({"schedule": self._browser_schedule_dict(job)}, ensure_ascii=False).encode(),
                        )
                    else:
                        raise ValueError("Schedules route supports GET or POST")
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/schedules/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    if self._cron_service is None:
                        self._write_response(writer, 503, self._json_error("Scheduler is not available"))
                        return
                    schedule_path = path.removeprefix("/api/schedules/").strip("/")
                    schedule_id, _, operation = schedule_path.partition("/")
                    job = self._browser_schedule_job(client_id, schedule_id)
                    if not operation and method == "GET":
                        self._write_response(
                            writer, 200, json.dumps({"schedule": self._browser_schedule_dict(job)}).encode()
                        )
                    elif not operation and method == "DELETE":
                        if not self._cron_service.remove_job(job.id):
                            raise ValueError("Schedule was not found")
                        self._write_response(writer, 200, json.dumps({"removed": True, "id": job.id}).encode())
                    elif operation == "enabled" and method == "POST":
                        payload = self._json_body(body)
                        enabled = payload.get("enabled")
                        if not isinstance(enabled, bool):
                            raise ValueError("Schedule enabled state must be boolean")
                        updated = self._cron_service.enable_job(job.id, enabled)
                        if updated is None:
                            raise ValueError("Schedule was not found")
                        self._write_response(
                            writer, 200, json.dumps({"schedule": self._browser_schedule_dict(updated)}).encode()
                        )
                    elif operation == "run" and method == "POST":
                        ran = await self._cron_service.run_job(job.id)
                        self._write_response(writer, 200, json.dumps({"ran": ran}).encode())
                    else:
                        raise ValueError("Schedule route was not found")
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
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
                        source_run_id, source_mission_id = self._browser_artifact_source(
                            client_id, session_id, payload.get("run_id")
                        )
                        kind = str(payload.get("kind") or "note")
                        content_type = str(
                            payload.get("content_type")
                            or ("text/uri-list" if kind == "link" else "text/markdown")
                        )
                        artifact = store.create(
                            owner_id=owner_id,
                            session_key=self._session_key(client_id, session_id),
                            title=title,
                            content=content,
                            kind=kind,
                            content_type=content_type,
                            source_run_id=source_run_id,
                            source_mission_id=source_mission_id,
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
                        revision_value = self._single_query_value(query, "revision")
                        revision = int(revision_value) if revision_value is not None else artifact.revision
                        content = store.read_content(owner_id, artifact_id, revision).encode("utf-8")
                        self._write_raw_response(
                            writer,
                            200,
                            content,
                            artifact.content_type,
                            f'attachment; filename="{self._artifact_download_name(artifact, revision)}"',
                        )
                    elif artifact_path.endswith("/export/html") and method == "GET":
                        artifact_id = artifact_path.removesuffix("/export/html").rstrip("/")
                        artifact = store.get(owner_id, artifact_id)
                        revision_value = self._single_query_value(query, "revision")
                        revision = int(revision_value) if revision_value is not None else artifact.revision
                        content = store.read_content(owner_id, artifact_id, revision)
                        document = self._artifact_html_export(artifact, content, revision).encode("utf-8")
                        filename = self._artifact_download_name(artifact, revision).rsplit(".", 1)[0] + ".html"
                        self._write_raw_response(
                            writer,
                            200,
                            document,
                            "text/html",
                            f'attachment; filename="{filename}"',
                        )
                    elif artifact_path.endswith("/export/bundle") and method == "GET":
                        artifact_id = artifact_path.removesuffix("/export/bundle").rstrip("/")
                        artifact = store.get(owner_id, artifact_id)
                        revision_value = self._single_query_value(query, "revision")
                        revision = int(revision_value) if revision_value is not None else artifact.revision
                        content = store.read_content(owner_id, artifact_id, revision)
                        bundle = self._artifact_bundle_export(artifact, content, revision)
                        filename = self._artifact_download_name(artifact, revision).rsplit(".", 1)[0] + ".zip"
                        self._write_raw_response(
                            writer,
                            200,
                            bundle,
                            "application/zip",
                            f'attachment; filename="{filename}"',
                        )
                    elif artifact_path.endswith("/verification") and method == "POST":
                        artifact_id = artifact_path.removesuffix("/verification").rstrip("/")
                        status = self._json_body(body).get("verification_status")
                        artifact = store.set_verification(owner_id, artifact_id, status)
                        self._write_response(
                            writer, 200, json.dumps({"artifact": asdict(artifact)}).encode()
                        )
                    elif artifact_path.endswith("/status") and method == "POST":
                        artifact_id = artifact_path.removesuffix("/status").rstrip("/")
                        status = self._json_body(body).get("status")
                        artifact = store.set_status(owner_id, artifact_id, status)
                        self._write_response(
                            writer, 200, json.dumps({"artifact": asdict(artifact)}).encode()
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
                        revision_value = self._single_query_value(query, "revision")
                        revision = int(revision_value) if revision_value is not None else None
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {
                                    "artifact": asdict(artifact),
                                    "content": store.read_content(owner_id, artifact.id, revision),
                                    "selected_revision": revision or artifact.revision,
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
                        session_id = self._single_query_value(query, "session_id")
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                self._browser_mission_detail(client_id, session_id, mission_id),
                                ensure_ascii=False,
                            ).encode(),
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
                    elif method == "GET" and operation == "blueprint":
                        blueprint = self._browser_get_mission_blueprint(
                            client_id, self._single_query_value(query, "session_id"), mission_id
                        )
                        self._write_response(
                            writer, 200, json.dumps({"blueprint": blueprint}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation in {"blueprint/draft", "blueprint-draft"}:
                        payload = self._json_body(body)
                        blueprint = self._browser_save_draft_blueprint(
                            client_id, payload.get("session_id"), mission_id, payload.get("steps")
                        )
                        self._write_response(
                            writer, 200, json.dumps({"blueprint": blueprint}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation in {"blueprint/approve", "blueprint-approve"}:
                        payload = self._json_body(body)
                        blueprint = self._browser_approve_blueprint(
                            client_id, payload.get("session_id"), mission_id, payload.get("blueprint_id")
                        )
                        self._write_response(
                            writer, 200, json.dumps({"blueprint": blueprint}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation in {"blueprint/step-transition", "blueprint-step-transition"}:
                        payload = self._json_body(body)
                        blueprint = self._browser_transition_blueprint_step(
                            client_id,
                            payload.get("session_id"),
                            mission_id,
                            payload.get("step_id"),
                            payload.get("target_state"),
                            blocked_reason=payload.get("blocked_reason"),
                        )
                        self._write_response(
                            writer, 200, json.dumps({"blueprint": blueprint}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation == "tasks":
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        session_key = self._session_key(client_id, session_id)
                        session = self._require_browser_session(client_id, session_id)
                        profile = self._session_profile(session)
                        active_mission = self._mission_store().get(owner_id, mission_id)
                        if active_mission.session_key != session_key or active_mission.state != "active":
                            raise ValueError("Mission is not active or does not belong to this session")
                        title = payload.get("title")
                        objective = payload.get("objective")
                        max_turns = int(payload.get("max_turns") or 10)
                        max_elapsed_sec = int(payload.get("max_elapsed_sec") or 600)
                        max_attempts = int(payload.get("max_attempts") or 3)

                        task = self._task_store().create(
                            owner_id=owner_id,
                            session_key=session_key,
                            mission_id=mission_id,
                            title=title,
                            objective=objective,
                            capability_profile=profile.id,
                            max_turns=max_turns,
                            max_elapsed_sec=max_elapsed_sec,
                            max_attempts=max_attempts,
                            depth=0,
                        )
                        self._write_response(
                            writer, 201, json.dumps({"task": task.to_dict()}, ensure_ascii=False).encode()
                        )
                    elif method == "GET" and operation == "tasks":
                        session_id = self._valid_browser_id(self._single_query_value(query, "session_id"))
                        session_key = self._session_key(client_id, session_id)
                        mission = self._mission_store().get(owner_id, mission_id)
                        if mission.session_key != session_key:
                            raise ValueError("Session mismatch for mission tasks")
                        tasks = self._task_store().list(owner_id, session_key=session_key, mission_id=mission_id)
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {
                                    "tasks": [
                                        {
                                            **t.to_dict(),
                                            "linked_runs_count": len(self._run_store().list_by_task(owner_id, t.id)),
                                        }
                                        for t in tasks
                                    ]
                                },
                                ensure_ascii=False,
                            ).encode(),
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
            elif path == "/api/search":
                try:
                    client_id = self._browser_id_from_query(query)
                    search = self._single_query_value(query, "q") or self._single_query_value(query, "search") or ""
                    limit_value = self._single_query_value(query, "limit")
                    limit = int(limit_value) if limit_value is not None else 20
                    service = self._search_service()
                    prefix = f"web:web:{self._valid_browser_id(client_id)}:"
                    results = service.search(
                        self._memory_owner(client_id),
                        search,
                        limit=limit,
                        session_prefix=prefix,
                    )
                    response = json.dumps(
                        {"query": search, "results": [item.to_dict() for item in results]},
                        ensure_ascii=False,
                    ).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/sessions":
                try:
                    client_id = self._browser_id_from_query(query)
                    search = self._single_query_value(query, "search") or ""
                    include_archived = self._query_bool(query, "include_archived", default=False)
                    response = json.dumps(
                        {
                            "sessions": self._list_browser_sessions(
                                client_id, search=search, include_archived=include_archived
                            )
                        },
                        ensure_ascii=False,
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
            elif path == "/api/governance":
                try:
                    self._browser_id_from_query(query)
                    store = self._governed_registry()
                    response = json.dumps(
                        {"entries": [entry.to_dict() for entry in store.list_entries()]}, ensure_ascii=False
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
            elif method == "GET" and path == "/api/browser-bridge/commands/next":
                try:
                    command = self._browser_bridge_store().claim_next(
                        self._single_query_value(query, "share_id"),
                        headers.get("x-pico-bridge-token"),
                    )
                    self._write_response(writer, 200, json.dumps({"command": command}, ensure_ascii=False).encode())
                except (ValueError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/browser-bridge/commands/") and path.endswith("/result"):
                try:
                    command_id = path.removeprefix("/api/browser-bridge/commands/").removesuffix("/result").strip("/")
                    payload = self._json_body(body)
                    command = self._browser_bridge_store().complete_command(
                        payload.get("share_id"),
                        headers.get("x-pico-bridge-token"),
                        command_id,
                        success=payload.get("success") is True,
                        result_summary=payload.get("result_summary"),
                        failure_category=payload.get("failure_category"),
                    )
                    self._write_response(writer, 200, json.dumps({"command": command.to_dict()}, ensure_ascii=False).encode())
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
            elif method == "GET" and path.startswith("/api/actions/") and path.endswith("/preview"):
                try:
                    client_id = self._browser_id_from_query(query)
                    action_id = path.removeprefix("/api/actions/").removesuffix("/preview").strip("/")
                    session_id = self._valid_browser_id(self._single_query_value(query, "session_id"))
                    self._require_browser_session(client_id, session_id)
                    preview = self._workspace_executor().preview_action(
                        self._memory_owner(client_id),
                        self._session_key(client_id, session_id),
                        action_id,
                    )
                    self._write_response(writer, 200, json.dumps({"preview": preview}, ensure_ascii=False).encode())
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif method == "POST" and path.startswith("/api/actions/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    action_path = path.removeprefix("/api/actions/").strip("/")
                    action_id, _, operation = action_path.partition("/")
                    payload = self._json_body(body)
                    session_id = self._valid_browser_id(payload.get("session_id"))
                    self._require_browser_session(client_id, session_id)
                    owner_id = self._memory_owner(client_id)
                    session_key = self._session_key(client_id, session_id)

                    if not action_id or operation not in {"approve", "reject", "cancel", "execute"}:
                        raise ValueError("Action route was not found")

                    if operation in {"approve", "reject"}:
                        action = self._action_store().resolve(
                            owner_id,
                            action_id,
                            session_key,
                            operation,
                            payload_fingerprint=payload.get("payload_fingerprint"),
                        )
                        self._sync_action_task_outcome(owner_id, session_key, action)
                        self._write_response(
                            writer, 200, json.dumps({"action": action.to_dict()}, ensure_ascii=False).encode()
                        )
                    elif operation == "cancel":
                        action = self._action_store().cancel(owner_id, action_id, session_key)
                        self._sync_action_task_outcome(owner_id, session_key, action)
                        self._write_response(
                            writer, 200, json.dumps({"action": action.to_dict()}, ensure_ascii=False).encode()
                        )
                    elif operation == "execute":
                        action = self._action_store().get(owner_id, action_id, session_key=session_key)
                        if action.profile_id == "workspace-build":
                            from picobot.operations.registry import CapabilityRegistry

                            session = self._session_manager().get_or_create(session_key)
                            profile = CapabilityRegistry().resolve(
                                session.metadata.get("pico_operation_profile")
                            )
                            result = await self._workspace_executor().execute_action(
                                owner_id,
                                session_key,
                                action_id,
                                payload_fingerprint=payload.get("payload_fingerprint"),
                                profile_id=profile.id,
                            )
                        elif action.profile_id == "browser-action":
                            from picobot.operations.browser_executor import BrowserActionExecutor

                            session = self._session_manager().get_or_create(session_key)
                            profile = CapabilityRegistry().resolve(
                                session.metadata.get("pico_operation_profile")
                            )
                            result = await BrowserActionExecutor(self._runtime_config().workspace_path).execute_action(
                                owner_id,
                                session_key,
                                action_id,
                                payload_fingerprint=payload.get("payload_fingerprint"),
                                profile_id=profile.id,
                            )
                        else:
                            result = self._mission_executor().execute_action(
                                owner_id,
                                session_key,
                                action_id,
                                payload_fingerprint=payload.get("payload_fingerprint"),
                            )
                        self._write_response(
                            writer, 200, json.dumps({"result": result}, ensure_ascii=False).encode()
                        )
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))

            elif path.startswith("/api/tasks/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    task_path = path.removeprefix("/api/tasks/").strip("/")
                    task_id, _, operation = task_path.partition("/")
                    owner_id = self._memory_owner(client_id)

                    if method == "GET" and not operation:
                        session_id = self._valid_browser_id(self._single_query_value(query, "session_id"))
                        session_key = self._session_key(client_id, session_id)
                        task = self._task_store().get(owner_id, task_id)
                        if task.session_key != session_key:
                            raise ValueError("Session mismatch for task access")
                        runs = self._run_store().list_by_task(owner_id, task.id, limit=30)
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {
                                    "task": task.to_dict(),
                                    "runs": [r.turn_receipt() for r in runs],
                                },
                                ensure_ascii=False,
                            ).encode(),
                        )
                    elif method == "POST" and operation == "run":
                        if not (hasattr(self, "_agent_loop") and self._agent_loop):
                            self._write_response(
                                writer, 503, self._json_error("Agent loop is not available")
                            )
                            return
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        session_key = self._session_key(client_id, session_id)
                        session = self._require_browser_session(client_id, session_id)
                        active_mission_info = self._get_browser_session_active_mission(client_id, session_id)
                        active_mission = active_mission_info.get("active_mission")
                        profile = self._session_profile(session)

                        task = self._task_store().claim_for_run(
                            owner_id, task_id, session_key, profile.id, active_mission
                        )
                        session.metadata["pico_active_task_id"] = task.id
                        self._session_manager().save(session)

                        task, run = await self._agent_loop.run_direct_task(
                            owner_id, session_key, task.id
                        )

                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {
                                    "task": task.to_dict(),
                                    "run": run.turn_receipt() if run else None,
                                },
                                ensure_ascii=False,
                            ).encode(),
                        )
                    elif method == "POST" and operation == "cancel":
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        session_key = self._session_key(client_id, session_id)
                        session = self._require_browser_session(client_id, session_id)
                        task = self._task_store().cancel(
                            owner_id, task_id, session_key, run_store=self._run_store()
                        )
                        if hasattr(self, "_agent_loop") and self._agent_loop:
                            self._agent_loop.cancel_task_run(task_id)
                        if session.metadata.get("pico_active_task_id") == task.id:
                            session.metadata.pop("pico_active_task_id", None)
                            self._session_manager().save(session)
                        self._write_response(
                            writer, 200, json.dumps({"task": task.to_dict()}, ensure_ascii=False).encode()
                        )
                    elif method == "POST" and operation == "retry":
                        payload = self._json_body(body)
                        session_id = self._valid_browser_id(payload.get("session_id"))
                        session_key = self._session_key(client_id, session_id)
                        session = self._require_browser_session(client_id, session_id)
                        active_mission_info = self._get_browser_session_active_mission(client_id, session_id)
                        active_mission = active_mission_info.get("active_mission")
                        profile = self._session_profile(session)

                        new_task = self._task_store().retry_task(
                            owner_id, task_id, session_key, profile.id, active_mission
                        )
                        self._write_response(
                            writer, 201, json.dumps({"task": new_task.to_dict()}, ensure_ascii=False).encode()
                        )
                    else:
                        raise ValueError("Task route was not found")
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))

            elif path.startswith("/api/sessions/") and path.endswith("/active-mission"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/active-mission").rstrip("/")
                    if method == "POST":
                        payload = self._json_body(body)
                        res = self._set_browser_session_active_mission(
                            client_id, session_id, payload.get("mission_id")
                        )
                    elif method == "GET":
                        res = self._get_browser_session_active_mission(client_id, session_id)
                    else:
                        raise ValueError("Active mission route supports GET or POST only")
                    self._write_response(writer, 200, json.dumps(res, ensure_ascii=False).encode())
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path.startswith("/api/sessions/") and path.endswith("/active-task"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/active-task").rstrip("/")
                    if method == "POST":
                        payload = self._json_body(body)
                        res = self._set_browser_session_active_task(
                            client_id, session_id, payload.get("task_id")
                        )
                    elif method == "GET":
                        res = self._get_browser_session_active_task(client_id, session_id)
                    else:
                        raise ValueError("Active task route supports GET or POST only")
                    self._write_response(writer, 200, json.dumps(res, ensure_ascii=False).encode())
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
            elif method == "POST" and path.startswith("/api/sessions/") and path.endswith("/archive"):
                try:
                    client_id = self._browser_id_from_query(query)
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/archive").strip("/")
                    payload = self._json_body(body) if body else {}
                    archived = payload.get("archived", True)
                    if not isinstance(archived, bool):
                        raise ValueError("Session archive state must be boolean")
                    result = self._set_browser_session_archive(client_id, session_id, archived)
                    self._write_response(writer, 200, json.dumps(result).encode())
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
            elif method == "POST" and path.startswith("/api/feedback/") and path.endswith("/candidate"):
                try:
                    client_id = self._browser_id_from_query(query)
                    feedback_id = path.removeprefix("/api/feedback/").removesuffix("/candidate").strip("/")
                    result = self._create_browser_learning_candidate(
                        client_id, feedback_id, self._json_body(body)
                    )
                    self._write_response(writer, 201, json.dumps(result, ensure_ascii=False).encode())
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 400, self._json_error(str(exc)))
            elif path == "/api/feedback":
                try:
                    client_id = self._browser_id_from_query(query)
                    if method == "GET":
                        session_id = self._single_query_value(query, "session_id")
                        self._write_response(
                            writer,
                            200,
                            json.dumps(
                                {"feedback": self._list_browser_feedback(client_id, session_id)},
                                ensure_ascii=False,
                            ).encode(),
                        )
                    elif method == "POST":
                        feedback = self._record_browser_feedback(client_id, self._json_body(body))
                        self._write_response(
                            writer,
                            200,
                            json.dumps({"feedback": asdict(feedback)}, ensure_ascii=False).encode(),
                        )
                    else:
                        raise ValueError("Feedback route supports GET or POST only")
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
            elif method == "GET" and path.startswith("/api/memory/"):
                try:
                    client_id = self._browser_id_from_query(query)
                    memory_id = path.removeprefix("/api/memory/").strip("/")
                    if not memory_id or "/" in memory_id:
                        raise ValueError("Memory route was not found")
                    store = self._memory_store()
                    owner_id = self._memory_owner(client_id)
                    item = store.get(owner_id, memory_id)
                    response = json.dumps(
                        {"memory": asdict(item), "history": store.history(owner_id, memory_id)},
                        ensure_ascii=False,
                    ).encode()
                    self._write_response(writer, 200, response)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._write_response(writer, 404, self._json_error(str(exc)))
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

    @classmethod
    def _query_bool(cls, query: dict[str, list[str]], key: str, *, default: bool) -> bool:
        value = cls._single_query_value(query, key)
        if value is None:
            return default
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Use a boolean {key} value")

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
            f"Content-Type: {content_type}{'; charset=utf-8' if content_type.startswith(('text/', 'application/json')) else ''}",
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
    def _artifact_download_name(artifact, revision: int | None = None) -> str:
        """Return a safe, useful filename without trusting artifact titles."""
        title = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact.title).strip("-._")[:80]
        stem = title or "pico-artifact"
        suffix = Path(artifact.relative_path).suffix or ".txt"
        return f"{stem}-v{revision or artifact.revision}{suffix}"

    @staticmethod
    def _artifact_html_export(artifact, content: str, revision: int) -> str:
        """Render a share-safe HTML wrapper without executing artifact content."""
        title = html.escape(artifact.title, quote=True)
        kind = html.escape(artifact.kind, quote=True)
        content_type = html.escape(artifact.content_type, quote=True)
        status = html.escape(artifact.status, quote=True)
        verification = html.escape(artifact.verification_status, quote=True)
        body = html.escape(content, quote=False)
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#20242a}"
            "h1{font-size:28px;margin-bottom:4px}.meta{color:#66717c;font-size:13px;margin-bottom:24px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7f9;border:1px solid #dfe3e8;"
            "border-radius:8px;padding:18px}</style></head><body>"
            f"<h1>{title}</h1><div class=\"meta\">{kind} · {content_type} · revision {revision} · {status} · {verification}</div>"
            f"<pre>{body}</pre></body></html>\n"
        )

    @classmethod
    def _artifact_bundle_export(cls, artifact, content: str, revision: int) -> bytes:
        """Build a deterministic, share-safe bundle for one artifact revision."""
        canonical_name = cls._artifact_download_name(artifact, revision)
        html_name = canonical_name.rsplit(".", 1)[0] + ".html"
        canonical_bytes = content.encode("utf-8")
        html_bytes = cls._artifact_html_export(artifact, content, revision).encode("utf-8")
        manifest = {
            "schema_version": 1,
            "title": artifact.title,
            "kind": artifact.kind,
            "content_type": artifact.content_type,
            "revision": revision,
            "status": artifact.status,
            "verification_status": artifact.verification_status,
            "files": [
                {
                    "name": canonical_name,
                    "role": "canonical",
                    "content_type": artifact.content_type,
                    "bytes": len(canonical_bytes),
                    "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
                },
                {
                    "name": html_name,
                    "role": "html-preview",
                    "content_type": "text/html",
                    "bytes": len(html_bytes),
                    "sha256": hashlib.sha256(html_bytes).hexdigest(),
                },
            ],
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, payload in (
                ("manifest.json", manifest_bytes),
                (canonical_name, canonical_bytes),
                (html_name, html_bytes),
            ):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                bundle.writestr(info, payload)
        return output.getvalue()

    def _browser_artifact_source(
        self, client_id: str, session_id: str, source_run_id: object
    ) -> tuple[str | None, str | None]:
        """Resolve a source run only when it belongs to this browser session."""
        if source_run_id is None:
            return None, None
        if not isinstance(source_run_id, str) or not source_run_id.strip():
            raise ValueError("Artifact source run ID must be a non-empty string")
        source_run_id = source_run_id.strip()
        try:
            source_run = self._run_store().get(self._memory_owner(client_id), source_run_id)
        except KeyError as exc:
            raise ValueError("Artifact source run was not found") from exc
        if source_run.session_key != self._session_key(client_id, session_id):
            raise ValueError("Artifact source run does not belong to this session")
        return source_run.id, source_run.mission_id

    def _browser_schedule_jobs(self, client_id: str):
        """Return only schedules addressed to this browser identity."""
        self._valid_browser_id(client_id)
        if self._cron_service is None:
            return []
        prefix = f"web:{client_id}:"
        return [
            job
            for job in self._cron_service.list_jobs(include_disabled=True)
            if job.payload.channel == "web"
            and isinstance(job.payload.to, str)
            and job.payload.to.startswith(prefix)
        ]

    def _browser_schedule_job(self, client_id: str, schedule_id: str):
        if not schedule_id or "/" in schedule_id:
            raise ValueError("Schedule route was not found")
        for job in self._browser_schedule_jobs(client_id):
            if job.id == schedule_id:
                return job
        raise ValueError("Schedule was not found")

    @staticmethod
    def _browser_schedule_dict(job) -> dict[str, Any]:
        schedule = job.schedule
        return {
            "id": job.id,
            "name": job.name,
            "enabled": job.enabled,
            "message": job.payload.message,
            "schedule": {
                "kind": schedule.kind,
                "at_ms": schedule.at_ms,
                "every_ms": schedule.every_ms,
                "expr": schedule.expr,
                "tz": schedule.tz,
            },
            "next_run_at_ms": job.state.next_run_at_ms,
            "last_run_at_ms": job.state.last_run_at_ms,
            "last_status": job.state.last_status,
            "last_error": job.state.last_error,
            "created_at_ms": job.created_at_ms,
            "updated_at_ms": job.updated_at_ms,
            "delete_after_run": job.delete_after_run,
        }

    @staticmethod
    def _browser_schedule_payload(payload: dict[str, Any]):
        """Build a validated local schedule from an explicit web form."""
        from picobot.cron.types import CronSchedule

        every_seconds = payload.get("every_seconds")
        cron_expr = payload.get("cron_expr")
        tz = payload.get("tz")
        at = payload.get("at")
        selected = sum(value not in (None, "") for value in (every_seconds, cron_expr, at))
        if selected != 1:
            raise ValueError("Choose exactly one of every_seconds, cron_expr, or at")
        if tz and not cron_expr:
            raise ValueError("tz can only be used with cron_expr")
        if cron_expr is not None and (not isinstance(cron_expr, str) or len(cron_expr) > 100):
            raise ValueError("cron_expr must be a short cron expression")

        if every_seconds is not None:
            try:
                interval = int(every_seconds)
            except (TypeError, ValueError):
                raise ValueError("every_seconds must be an integer") from None
            if interval < 30 or interval > 31_536_000:
                raise ValueError("every_seconds must be between 30 and 31536000")
            return CronSchedule(kind="every", every_ms=interval * 1000), False

        if cron_expr:
            if tz:
                from zoneinfo import ZoneInfo

                try:
                    ZoneInfo(str(tz))
                except Exception:
                    raise ValueError("Unknown timezone") from None
            return CronSchedule(kind="cron", expr=cron_expr, tz=tz), False

        if not isinstance(at, str):
            raise ValueError("at must be an ISO datetime")
        try:
            at_ms = int(datetime.fromisoformat(at).timestamp() * 1000)
        except ValueError:
            raise ValueError("at must be an ISO datetime") from None
        return CronSchedule(kind="at", at_ms=at_ms), True

    def _search_service(self):
        from picobot.search import PersonalSearch

        return PersonalSearch(self._runtime_config().workspace_path)

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

    def _task_store(self):
        from picobot.tasks import TaskStore

        return TaskStore(self._runtime_config().workspace_path)

    def _sync_action_task_outcome(self, owner_id: str, session_key: str, action: Any) -> None:
        if not getattr(action, "initiating_run_id", None):
            return
        try:
            run = self._run_store().get(owner_id, action.initiating_run_id)
            if run and getattr(run, "task_id", None):
                task_store = self._task_store()
                if action.status == "rejected":
                    task_store.fail(owner_id, run.task_id, failure_category="rejected", result_summary="Proposed action was rejected by human.", session_key=session_key)
                elif action.status == "cancelled":
                    task_store.cancel(owner_id, run.task_id, session_key=session_key)
                elif action.status == "expired":
                    task_store.fail(owner_id, run.task_id, failure_category="expired", result_summary="Proposed action expired before review.", session_key=session_key)
        except Exception:
            pass

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
        linked_runs = self._run_store().list_by_mission(owner_id, mission_id, limit=100)
        return {
            "artifact_count": len(self._artifact_store().list(owner_id, session_key=session_key)),
            "activity_count": len(self._tool_activity_store().list(owner_id, session_key, limit=100)),
            "checkpoint_count": len(self._mission_store().checkpoints(owner_id, mission_id)),
            "run_count": len(linked_runs),
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

    def _browser_mission_for_session(
        self, client_id: str, session_id: object, mission_id: str
    ):
        """Load a mission only when it belongs to the caller's saved session."""
        valid_session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, valid_session_id)
        owner_id = self._memory_owner(client_id)
        mission = self._mission_store().get(owner_id, mission_id)
        if mission.session_key != self._session_key(client_id, valid_session_id):
            raise ValueError("Mission does not belong to this session")
        return mission

    def _browser_mission_detail(
        self, client_id: str, session_id: object, mission_id: str
    ) -> dict[str, Any]:
        owner_id = self._memory_owner(client_id)
        mission = self._browser_mission_for_session(client_id, session_id, mission_id)
        linked_runs = self._run_store().list_by_mission(owner_id, mission.id, limit=20)
        safe_runs = [
            {
                "id": r.id,
                "state": r.state,
                "provider": r.provider,
                "model": r.model,
                "created_at": r.created_at,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "elapsed_ms": r.elapsed_ms,
                "error_summary": r.error_summary,
                "usage": r.usage or None,
                "blueprint_step_id": getattr(r, "blueprint_step_id", None),
            }
            for r in linked_runs
        ]
        approved_bp = self._mission_store().get_approved_blueprint(owner_id, mission.id)
        latest_bp = self._mission_store().get_latest_blueprint(owner_id, mission.id)
        bp_dict = approved_bp.to_dict() if approved_bp else (latest_bp.to_dict() if latest_bp else None)
        tasks_list = self._task_store().list(owner_id, mission_id=mission.id, limit=30)
        tasks_data = [
            {
                **t.to_dict(),
                "linked_runs_count": len(self._run_store().list_by_task(owner_id, t.id, limit=100)),
            }
            for t in tasks_list
        ]
        checkpoints = [
            item.to_dict() for item in self._mission_store().checkpoints(owner_id, mission.id)
        ]
        events = [item.to_dict() for item in self._mission_store().events(owner_id, mission.id)]
        mission_actions = [
            item.to_dict()
            for item in self._action_store().list_by_mission(owner_id, mission.id, limit=30)
        ]
        active_task = next(
            (task for task in tasks_data if task["state"] in {"queued", "running", "waiting_for_approval"}),
            None,
        )
        return {
            "mission": mission.to_dict(),
            "blueprint": bp_dict,
            "approved_blueprint": approved_bp.to_dict() if approved_bp else None,
            "latest_blueprint": latest_bp.to_dict() if latest_bp else None,
            "checkpoints": checkpoints,
            "events": events,
            "runs": safe_runs,
            "evidence": self._mission_evidence(owner_id, mission.session_key, mission.id),
            "mission_actions": mission_actions,
            "tasks": tasks_data,
            "resume_brief": {
                "outcome": mission.objective,
                "current_step": mission.current_step,
                "state": mission.state,
                "blocker": mission.blocked_reason,
                "last_checkpoint": checkpoints[-1]["summary"] if checkpoints else None,
                "active_task": active_task["title"] if active_task else None,
                "pending_approvals": sum(
                    action["status"] in {"proposed", "approved"} for action in mission_actions
                ),
            },
        }

    def _browser_get_mission_blueprint(
        self, client_id: str, session_id: object, mission_id: str
    ) -> dict[str, Any] | None:
        owner_id = self._memory_owner(client_id)
        self._browser_mission_for_session(client_id, session_id, mission_id)
        approved = self._mission_store().get_approved_blueprint(owner_id, mission_id)
        if approved:
            return approved.to_dict()
        latest = self._mission_store().get_latest_blueprint(owner_id, mission_id)
        return latest.to_dict() if latest else None

    def _browser_save_draft_blueprint(
        self, client_id: str, session_id: object, mission_id: str, steps: object
    ) -> dict[str, Any]:
        owner_id = self._memory_owner(client_id)
        self._browser_mission_for_session(client_id, session_id, mission_id)
        if not isinstance(steps, list):
            raise ValueError("Blueprint steps must be a list")
        blueprint = self._mission_store().save_draft_blueprint(owner_id, mission_id, steps)
        return blueprint.to_dict()

    def _browser_approve_blueprint(
        self, client_id: str, session_id: object, mission_id: str, blueprint_id: object = None
    ) -> dict[str, Any]:
        owner_id = self._memory_owner(client_id)
        self._browser_mission_for_session(client_id, session_id, mission_id)
        bp_id_str = str(blueprint_id) if isinstance(blueprint_id, str) and blueprint_id.strip() else None
        blueprint = self._mission_store().approve_blueprint(owner_id, mission_id, blueprint_id=bp_id_str)
        return blueprint.to_dict()

    def _browser_transition_blueprint_step(
        self,
        client_id: str,
        session_id: object,
        mission_id: str,
        step_id: object,
        target_state: object,
        blocked_reason: object = None,
    ) -> dict[str, Any]:
        owner_id = self._memory_owner(client_id)
        self._browser_mission_for_session(client_id, session_id, mission_id)
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError("Step ID is required")
        if not isinstance(target_state, str) or not target_state.strip():
            raise ValueError("Target state is required")
        reason_str = str(blocked_reason) if isinstance(blocked_reason, str) and blocked_reason.strip() else None
        blueprint = self._mission_store().transition_blueprint_step(
            owner_id, mission_id, step_id.strip(), target_state.strip(), blocked_reason=reason_str
        )
        return blueprint.to_dict()

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

    def _feedback_store(self):
        from picobot.learning.feedback import ResponseFeedbackStore

        return ResponseFeedbackStore(self._runtime_config().workspace_path)

    def _list_browser_feedback(self, client_id: str, session_id: object = None) -> list[dict[str, Any]]:
        session_key = None
        if session_id:
            valid_session_id = self._valid_browser_id(session_id)
            self._require_browser_session(client_id, valid_session_id)
            session_key = self._session_key(client_id, valid_session_id)
        return [
            asdict(item)
            for item in self._feedback_store().list(
                self._memory_owner(client_id), session_key=session_key
            )
        ]

    def _record_browser_feedback(self, client_id: str, payload: dict[str, Any]):
        session_id = self._valid_browser_id(payload.get("session_id"))
        self._require_browser_session(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session_key = self._session_key(client_id, session_id)
        run = self._run_store().get(owner_id, payload.get("run_id"))
        if run.session_key != session_key:
            raise ValueError("Feedback run does not belong to this session")
        return self._feedback_store().record(
            owner_id=owner_id,
            session_key=session_key,
            run_id=run.id,
            kind=payload.get("kind"),
            note=payload.get("note"),
        )

    def _create_browser_learning_candidate(
        self, client_id: str, feedback_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        session_id = self._valid_browser_id(payload.get("session_id"))
        self._require_browser_session(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session_key = self._session_key(client_id, session_id)
        feedback_store = self._feedback_store()
        feedback = feedback_store.get(owner_id, feedback_id)
        if feedback.session_key != session_key:
            raise ValueError("Feedback does not belong to this session")
        if feedback.kind != "correction" or not feedback.note:
            raise ValueError("Only correction feedback can become a learning candidate")

        candidate_type = payload.get("candidate_type")
        if candidate_type not in {"memory", "skill"}:
            raise ValueError("Candidate type must be memory or skill")
        if feedback.candidate_type and feedback.candidate_ref:
            return self._existing_learning_candidate(owner_id, feedback)

        source_ref = f"feedback:{feedback.id}"
        if candidate_type == "memory":
            candidate = self._memory_store().propose(
                owner_id,
                feedback.note,
                kind="correction",
                source_type="response_correction",
                source_ref=source_ref,
            )
            feedback = feedback_store.attach_candidate(
                owner_id, feedback.id, "memory", candidate.id
            )
            return {
                "feedback": asdict(feedback),
                "candidate_type": "memory",
                "candidate": asdict(candidate),
            }

        name = payload.get("name") or f"response-correction-{feedback.id[:8]}"
        description = payload.get("description") or "Review a reusable response correction before activation."
        guidance = payload.get("guidance") or feedback.note
        candidate = self._skill_proposal_store().create(
            owner_id=owner_id,
            session_key=session_key,
            name=name,
            description=description,
            guidance=guidance,
            source_type="response_correction",
            source_ref=source_ref,
        )
        feedback = feedback_store.attach_candidate(owner_id, feedback.id, "skill", candidate.id)
        return {
            "feedback": asdict(feedback),
            "candidate_type": "skill",
            "candidate": asdict(candidate),
        }

    def _existing_learning_candidate(self, owner_id: str, feedback) -> dict[str, Any]:
        if feedback.candidate_type == "memory":
            candidate = self._memory_store().get(owner_id, feedback.candidate_ref)
        elif feedback.candidate_type == "skill":
            candidate = self._skill_proposal_store().get(owner_id, feedback.candidate_ref)
        else:
            raise ValueError("Feedback candidate type is invalid")
        return {
            "feedback": asdict(feedback),
            "candidate_type": feedback.candidate_type,
            "candidate": asdict(candidate),
        }

    def _tool_activity_store(self):
        from picobot.operations.activity import ToolActivityStore

        return ToolActivityStore(self._runtime_config().workspace_path)

    def _action_store(self):
        from picobot.operations.actions import ProposedActionStore

        return ProposedActionStore(self._runtime_config().workspace_path)

    def _mission_executor(self):
        from picobot.operations.executor import MissionExecutor

        return MissionExecutor(self._runtime_config().workspace_path)

    def _workspace_executor(self):
        from picobot.operations.workspace_executor import WorkspaceExecutor

        return WorkspaceExecutor(self._runtime_config().workspace_path)

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

    @staticmethod
    def _github_cli_is_configured() -> bool:
        """Expose only local CLI presence, never auth output or credentials."""
        return shutil.which("gh") is not None

    @staticmethod
    def _calendar_is_configured() -> bool:
        """Expose only token-file presence, never token contents or path details."""
        return os.path.isfile(os.path.expanduser("~/.picobot/runtime/google-calendar-token.json"))

    def _governed_registry(self):
        from picobot.agent.skills import SkillsLoader
        from picobot.operations.governed_registry import GovernedRegistryStore

        cfg = self._runtime_config()
        workspace = getattr(cfg, "workspace_path", Path("."))
        store = GovernedRegistryStore(workspace)
        tools_cfg = getattr(cfg, "tools", None)
        mcp_servers = getattr(tools_cfg, "mcp_servers", {}) if tools_cfg else {}
        store.sync_inventory(
            SkillsLoader(workspace),
            mcp_servers,
        )
        return store

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
        governed_entries = [entry.to_dict() for entry in self._governed_registry().list_entries()]
        active_mission_info = {"active_mission_id": None, "active_mission": None}
        active_task_info = {"active_task_id": None, "active_task": None}
        if session_key:
            try:
                active_mission_info = self._get_browser_session_active_mission(client_id, session_id)
                active_task_info = self._get_browser_session_active_task(client_id, session_id)
            except ValueError:
                pass
        mission_context_only = (
            active_mission_info.get("active_mission") is not None
            and profile.id != "mission-work"
        )
        profile_description = profile.description
        if mission_context_only:
            profile_description += (
                " This mission is attached as context. Switch to Mission work to allow "
                "governed mission action proposals."
            )
        return {
            "profile": {
                "id": profile.id,
                "label": profile.label,
                "description": profile_description,
            },
            "profiles": registry.profiles(),
            # The registry remains the server-side source of truth, but the
            # workbench should show only capabilities active for this profile.
            # Rendering every other profile as "not in profile" turns the
            # focused operations view into a capability catalogue and makes
            # ordinary sessions look more privileged than they are.
            "capabilities": [
                item.to_dict()
                for item in registry.statuses(
                    profile.id,
                    self._OPERATIONS_TOOL_NAMES,
                    web_search_configured=self._web_search_is_configured(config),
                    browser_shared=shared_tab is not None,
                    github_configured=self._github_cli_is_configured(),
                    calendar_configured=self._calendar_is_configured(),
                )
                if item.permitted
            ],
            "governance": governed_entries,
            "activity": activities,
            "actions": actions,
            "shared_browser_tab": shared_tab,
            "active_mission": active_mission_info.get("active_mission"),
            "active_task": active_task_info.get("active_task"),
        }

    def _get_browser_session_active_mission(self, client_id: str, session_id: str) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        session_key = self._session_key(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session = self._session_manager().get_or_create(session_key)
        mission_id = session.metadata.get("pico_active_mission_id")
        mission_dict = None
        if isinstance(mission_id, str) and mission_id:
            try:
                mission = self._mission_store().get(owner_id, mission_id)
                if mission.session_key == session_key and mission.state == "active":
                    mission_dict = mission.to_dict()
                else:
                    session.metadata.pop("pico_active_mission_id", None)
                    self._session_manager().save(session)
            except KeyError:
                session.metadata.pop("pico_active_mission_id", None)
                self._session_manager().save(session)
        return {
            "active_mission_id": mission_dict["id"] if mission_dict else None,
            "active_mission": mission_dict,
        }

    def _set_browser_session_active_mission(
        self, client_id: str, session_id: str, mission_id: object
    ) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        session_key = self._session_key(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session = self._session_manager().get_or_create(session_key)

        if mission_id is None or mission_id == "":
            session.metadata.pop("pico_active_mission_id", None)
            session.updated_at = datetime.now()
            self._session_manager().save(session)
            return {"active_mission_id": None, "active_mission": None}

        if not isinstance(mission_id, str):
            raise ValueError("Active mission ID must be a string or null")

        mission = self._mission_store().get(owner_id, mission_id)
        if mission.session_key != session_key:
            raise ValueError("Mission belongs to a different session")
        if mission.state != "active":
            raise ValueError(f"Only active missions can be selected (mission is '{mission.state}')")

        session.metadata["pico_active_mission_id"] = mission.id
        session.updated_at = datetime.now()
        self._session_manager().save(session)
        return {"active_mission_id": mission.id, "active_mission": mission.to_dict()}

    def _get_browser_session_active_task(self, client_id: str, session_id: str) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        session_key = self._session_key(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session = self._session_manager().get_or_create(session_key)
        task_id = session.metadata.get("pico_active_task_id")
        task_dict = None
        if isinstance(task_id, str) and task_id:
            try:
                task = self._task_store().get(owner_id, task_id)
                if task.session_key == session_key and task.state in {"queued", "running", "waiting_for_approval"}:
                    task_dict = task.to_dict()
                else:
                    session.metadata.pop("pico_active_task_id", None)
                    self._session_manager().save(session)
            except KeyError:
                session.metadata.pop("pico_active_task_id", None)
                self._session_manager().save(session)
        if not task_dict:
            active = self._task_store().get_active(owner_id, session_key)
            if active:
                task_dict = active.to_dict()
        return {
            "active_task_id": task_dict["id"] if task_dict else None,
            "active_task": task_dict,
        }

    def _set_browser_session_active_task(
        self, client_id: str, session_id: str, task_id: object
    ) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        session_key = self._session_key(client_id, session_id)
        owner_id = self._memory_owner(client_id)
        session = self._session_manager().get_or_create(session_key)

        if task_id is None or task_id == "":
            session.metadata.pop("pico_active_task_id", None)
            session.updated_at = datetime.now()
            self._session_manager().save(session)
            return {"active_task_id": None, "active_task": None}

        if not isinstance(task_id, str):
            raise ValueError("Task ID must be a string or null")

        task = self._task_store().get(owner_id, task_id)
        if task.session_key != session_key:
            raise ValueError("Task belongs to a different session")

        session.metadata["pico_active_task_id"] = task.id
        session.updated_at = datetime.now()
        self._session_manager().save(session)
        return {"active_task_id": task.id, "active_task": task.to_dict()}

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

    def _list_browser_sessions(
        self,
        client_id: str,
        *,
        search: str = "",
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        prefix = f"web:web:{self._valid_browser_id(client_id)}:"
        manager = self._session_manager()
        result: list[dict[str, Any]] = []
        search_terms = search.casefold().split()
        for item in manager.list_sessions():
            key = item["key"]
            if not key.startswith(prefix):
                continue
            session_id = key.removeprefix(prefix)
            if not self._BROWSER_ID_RE.fullmatch(session_id):
                continue
            session = manager.get_or_create(key)
            archived = session.metadata.get("pico_archived") is True
            if archived and not include_archived:
                continue
            title = self._browser_session_title(session)
            if search_terms:
                searchable = " ".join(
                    [
                        title,
                        key,
                        *(
                            str(message.get("content", ""))
                            for message in session.messages
                            if message.get("role") in {"user", "assistant"}
                        ),
                    ]
                ).casefold()
                if not all(term in searchable for term in search_terms):
                    continue
            active_mission = self._get_browser_session_active_mission(client_id, session_id).get(
                "active_mission"
            )
            active_task = self._get_browser_session_active_task(client_id, session_id).get(
                "active_task"
            )
            result.append(
                {
                    "id": session_id,
                    "title": title,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "archived": archived,
                    "message_count": len(
                        [m for m in session.messages if m.get("role") in {"user", "assistant"}]
                    ),
                    "active_mission": (
                        {
                            "id": active_mission["id"],
                            "title": active_mission["title"],
                            "state": active_mission["state"],
                        }
                        if active_mission
                        else None
                    ),
                    "active_task": (
                        {
                            "id": active_task["id"],
                            "title": active_task["title"],
                            "state": active_task["state"],
                            "mission_id": active_task["mission_id"],
                        }
                        if active_task
                        else None
                    ),
                }
            )
        return result

    def _set_browser_session_archive(self, client_id: str, session_id: str, archived: bool) -> dict[str, Any]:
        session_id = self._valid_browser_id(session_id)
        self._require_browser_session(client_id, session_id)
        manager = self._session_manager()
        session = manager.get_or_create(self._session_key(client_id, session_id))
        if archived:
            session.metadata["pico_archived"] = True
        else:
            session.metadata.pop("pico_archived", None)
        session.updated_at = datetime.now()
        manager.save(session)
        return {"id": session_id, "archived": archived}

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
