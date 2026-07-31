"""Contract tests for the local browser chat channel."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.bus.events import OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.memory.store import PersonalMemoryStore
from picobot.session.manager import SessionManager


CLIENT_A = "browser_identity_0001"
CLIENT_B = "browser_identity_0002"
SESSION_A = "session_identity_0001"
SESSION_B = "session_identity_0002"


class FakeWebSocket:
    """Small in-memory websocket that exercises the channel protocol."""

    def __init__(self):
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def send(self, payload: str) -> None:
        await self.outgoing.put(payload)

    async def close(self) -> None:
        return None


def test_browser_messages_use_server_bound_identity_and_private_replies():
    async def scenario():
        bus = MessageBus()
        channel = WebChannel(SimpleNamespace(allow_from=["*"]), bus)
        first, second = FakeWebSocket(), FakeWebSocket()
        first_task = asyncio.create_task(channel._handle_connection(first))
        second_task = asyncio.create_task(channel._handle_connection(second))

        await first.incoming.put(
            json.dumps({"type": "hello", "client_id": CLIENT_A, "session_id": SESSION_A})
        )
        await second.incoming.put(
            json.dumps({"type": "hello", "client_id": CLIENT_B, "session_id": SESSION_B})
        )
        first_ready = json.loads(await asyncio.wait_for(first.outgoing.get(), timeout=1))
        second_ready = json.loads(await asyncio.wait_for(second.outgoing.get(), timeout=1))

        assert first_ready["type"] == "ready"
        assert first_ready["chat_id"] != second_ready["chat_id"]
        assert first_ready["sender_id"].startswith("browser:")
        assert first_ready["client_id"] == CLIENT_A
        assert first_ready["session_id"] == SESSION_A

        # Supplied routing data is ignored. Pico constructs the channel route
        # from the browser's opaque identity and selected session.
        await first.incoming.put(
            json.dumps(
                {
                    "type": "message",
                    "content": "remember this",
                    "chat_id": "other-person",
                    "sender_id": "other-person",
                }
            )
        )
        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
        assert inbound.channel == "web"
        assert inbound.chat_id == first_ready["chat_id"]
        assert inbound.sender_id == first_ready["sender_id"]
        assert inbound.content == "remember this"
        assert inbound.metadata == {"source": "browser"}

        await channel.send(
            OutboundMessage(
                channel="web",
                chat_id=inbound.chat_id,
                content="private reply",
            )
        )
        reply = json.loads(await asyncio.wait_for(first.outgoing.get(), timeout=1))
        assert reply["content"] == "private reply"
        assert second.outgoing.empty()

        await first.incoming.put(None)
        await second.incoming.put(None)
        await asyncio.wait_for(first_task, timeout=1)
        await asyncio.wait_for(second_task, timeout=1)

    asyncio.run(scenario())


def test_browser_identity_resumes_its_session_after_reconnect():
    async def scenario():
        bus = MessageBus()
        channel = WebChannel(SimpleNamespace(allow_from=["*"]), bus)
        first, replacement = FakeWebSocket(), FakeWebSocket()
        first_task = asyncio.create_task(channel._handle_connection(first))

        hello = {"type": "hello", "client_id": CLIENT_A, "session_id": SESSION_A}
        await first.incoming.put(json.dumps(hello))
        first_ready = json.loads(await asyncio.wait_for(first.outgoing.get(), timeout=1))
        await first.incoming.put(None)
        await asyncio.wait_for(first_task, timeout=1)

        replacement_task = asyncio.create_task(channel._handle_connection(replacement))
        await replacement.incoming.put(json.dumps(hello))
        replacement_ready = json.loads(await asyncio.wait_for(replacement.outgoing.get(), timeout=1))
        assert replacement_ready["chat_id"] == first_ready["chat_id"]
        assert replacement_ready["sender_id"] == first_ready["sender_id"]

        await replacement.incoming.put(None)
        await asyncio.wait_for(replacement_task, timeout=1)

    asyncio.run(scenario())


def test_browser_rejects_invalid_payload_without_publishing_it():
    async def scenario():
        bus = MessageBus()
        channel = WebChannel(SimpleNamespace(allow_from=["*"]), bus)
        socket = FakeWebSocket()
        task = asyncio.create_task(channel._handle_connection(socket))

        await socket.incoming.put("not-json")
        error = json.loads(await asyncio.wait_for(socket.outgoing.get(), timeout=1))
        assert error == {"type": "error", "content": "Message must be valid JSON."}
        assert bus.inbound.empty()

        await socket.incoming.put(None)
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(scenario())


def test_browser_data_helpers_scope_sessions_and_memory_to_one_identity(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    sessions = SessionManager(workspace)
    own_key = channel._session_key(CLIENT_A, SESSION_A)
    other_key = channel._session_key(CLIENT_B, SESSION_B)
    own = sessions.get_or_create(own_key)
    own.add_message("user", "Plan Pico's personal workbench")
    own.add_message("assistant", "I will prepare the first slice.")
    sessions.save(own)
    other = sessions.get_or_create(other_key)
    other.add_message("user", "Private other identity conversation")
    sessions.save(other)

    listed = channel._list_browser_sessions(CLIENT_A)
    assert listed == [
        {
            "id": SESSION_A,
            "title": "Plan Pico's personal workbench",
            "created_at": listed[0]["created_at"],
            "updated_at": listed[0]["updated_at"],
            "message_count": 2,
        }
    ]
    transcript = channel._browser_transcript(CLIENT_A, SESSION_A)
    assert [item["content"] for item in transcript["messages"]] == [
        "Plan Pico's personal workbench",
        "I will prepare the first slice.",
    ]

    store = PersonalMemoryStore(workspace)
    own_memory = store.remember(channel._memory_owner(CLIENT_A), "I prefer short updates")
    store.remember(channel._memory_owner(CLIENT_B), "Do not expose this")
    assert [item.id for item in store.list(channel._memory_owner(CLIENT_A))] == [own_memory.id]


def test_browser_session_title_is_explicit_durable_and_scoped_to_its_owner(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    title = channel._set_browser_session_title(CLIENT_A, SESSION_A, "  Website Ops review  ")

    assert title == "Website Ops review"
    assert channel._list_browser_sessions(CLIENT_A)[0]["title"] == "Website Ops review"
    assert channel._list_browser_sessions(CLIENT_B) == []

    reloaded = SessionManager(workspace).get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    assert reloaded.metadata["pico_web_title"] == "Website Ops review"


def test_browser_context_exposes_only_recalled_memories_owned_by_the_browser(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    store = PersonalMemoryStore(workspace)
    own_memory = store.remember(channel._memory_owner(CLIENT_A), "Use clear, short updates.")
    other_memory = store.remember(channel._memory_owner(CLIENT_B), "Private preference")

    sessions = SessionManager(workspace)
    session = sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    session.metadata["pico_last_context"] = {
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "history_message_count": 4,
        "memory_ids": [own_memory.id, other_memory.id],
    }
    sessions.save(session)

    context = channel._browser_session_context(CLIENT_A, SESSION_A)

    assert context["history_message_count"] == 4
    assert [item["id"] for item in context["memory"]] == [own_memory.id]


def test_browser_learning_requires_a_session_owned_by_the_browser(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))

    channel._require_browser_session(CLIENT_A, SESSION_A)
    with pytest.raises(ValueError, match="Session was not found"):
        channel._require_browser_session(CLIENT_B, SESSION_A)
