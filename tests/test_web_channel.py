"""Contract tests for the local browser chat channel."""

import asyncio
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from picobot.bus.events import OutboundMessage
from picobot.bus.queue import MessageBus
from picobot.artifacts.store import ArtifactStore
from picobot.channels.web import WebChannel
from picobot.cron.service import CronService
from picobot.cron.types import CronSchedule
from picobot.memory.store import PersonalMemoryStore
from picobot.missions import MissionStore
from picobot.runs import RunStore
from picobot.session.manager import SessionManager
from picobot.tasks import TaskStore
from picobot.operations import ProposedActionStore, ToolActivityStore


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


def test_project_folder_chooser_returns_only_user_selected_directory(tmp_path: Path, monkeypatch):
    """The native picker is user initiated and never scans the selected tree."""
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._require_browser_session = lambda client_id, session_id: None  # type: ignore[method-assign]
    monkeypatch.setattr("picobot.channels.web.shutil.which", lambda command: "/usr/bin/osascript")
    captured = {}

    def selected_folder(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout=f"{tmp_path}/\n")

    monkeypatch.setattr("picobot.channels.web.subprocess.run", selected_folder)
    selected = channel._choose_project_local_folder(CLIENT_A, SESSION_A)
    assert selected == str(tmp_path.resolve())
    assert captured["command"][:2] == ["osascript", "-e"]
    assert "choose folder" in captured["command"][2]


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
    own.add_message("assistant", "I will prepare the first slice.", run_id="run-123")
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
            "archived": False,
            "active_mission": None,
            "active_task": None,
            "latest_run": None,
        }
    ]
    transcript = channel._browser_transcript(CLIENT_A, SESSION_A)
    assert [item["content"] for item in transcript["messages"]] == [
        "Plan Pico's personal workbench",
        "I will prepare the first slice.",
    ]
    assert transcript["messages"][1]["run_id"] == "run-123"

    store = PersonalMemoryStore(workspace)
    own_memory = store.remember(channel._memory_owner(CLIENT_A), "I prefer short updates")
    store.remember(channel._memory_owner(CLIENT_B), "Do not expose this")
    assert [item.id for item in store.list(channel._memory_owner(CLIENT_A))] == [own_memory.id]


def test_browser_schedules_are_durable_and_owner_scoped(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    cron = CronService(workspace / "cron" / "jobs.json")
    channel.set_cron_service(cron)
    sessions = SessionManager(workspace)
    sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    sessions.get_or_create(channel._session_key(CLIENT_B, SESSION_B))

    own = cron.add_job(
        name="Own review",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Review my open work",
        deliver=True,
        channel="web",
        to=channel._chat_id(CLIENT_A, SESSION_A),
    )
    cron.add_job(
        name="Other review",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Private other work",
        deliver=True,
        channel="web",
        to=channel._chat_id(CLIENT_B, SESSION_B),
    )

    assert [item["id"] for item in map(channel._browser_schedule_dict, channel._browser_schedule_jobs(CLIENT_A))] == [own.id]
    with pytest.raises(ValueError, match="Schedule was not found"):
        channel._browser_schedule_job(CLIENT_A, "missing")


def test_browser_schedule_payload_requires_one_bounded_schedule_type():
    schedule, delete_after = WebChannel._browser_schedule_payload({"every_seconds": 60})
    assert schedule.kind == "every" and schedule.every_ms == 60_000 and delete_after is False

    with pytest.raises(ValueError, match="exactly one"):
        WebChannel._browser_schedule_payload({"every_seconds": 60, "cron_expr": "0 9 * * *"})
    with pytest.raises(ValueError, match="between 30"):
        WebChannel._browser_schedule_payload({"every_seconds": 5})


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


def test_browser_session_stance_is_durable_and_owner_scoped(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))

    initial = channel._browser_session_stance(CLIENT_A, SESSION_A)
    assert initial["stance"]["id"] == "explore"
    assert {item["id"] for item in initial["stances"]} == {"explore", "decide", "make", "review"}

    changed = channel._set_browser_session_stance(CLIENT_A, SESSION_A, "review")
    assert changed["stance"]["label"] == "Review"
    assert channel._browser_session_stance(CLIENT_A, SESSION_A)["stance"]["id"] == "review"
    with pytest.raises(ValueError, match="Session was not found"):
        channel._browser_session_stance(CLIENT_B, SESSION_A)
    with pytest.raises(ValueError, match="Session stance must be one of"):
        channel._set_browser_session_stance(CLIENT_A, SESSION_A, "execute")

    reloaded = SessionManager(workspace).get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    assert reloaded.metadata["pico_session_stance"] == "review"


def test_browser_session_orientation_is_project_scoped_and_durable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    project = channel._project_store().create(
        channel._memory_owner(CLIENT_A),
        title="Pico",
        kind="software",
        purpose="A local work partner",
    )

    changed = channel._set_browser_session_orientation(
        CLIENT_A,
        SESSION_A,
        {
            "project_id": project.id,
            "objective": "Make the working context legible.",
            "role_lens_id": "systems_designer",
            "challenge_policy_id": "active",
            "temporary_constraints": ["No unbounded integrations"],
            "expected_result": "A safe orientation receipt",
        },
    )
    assert changed["project"]["title"] == "Pico"
    assert changed["orientation"]["role_lens"]["id"] == "systems_designer"
    assert {role["id"] for role in changed["roles"]} == {
        "founder", "systems_designer", "builder", "researcher", "writer"
    }
    with pytest.raises(KeyError):
        channel._set_browser_session_orientation(
            CLIENT_A, SESSION_A, {"project_id": "not-this-owner"}
        )
    with pytest.raises(ValueError, match="Session was not found"):
        channel._browser_session_orientation(CLIENT_B, SESSION_A)

    reloaded = SessionManager(workspace).get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    assert reloaded.metadata["pico_session_orientation"]["project_id"] == project.id


def test_browser_project_brief_is_explicit_owner_scoped_and_saved_as_an_artifact(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source_root = tmp_path / "project-source"
    workspace.mkdir()
    source_root.mkdir()
    (source_root / "README.md").write_text("# Safe project\n\nTOKEN=do-not-save\n", encoding="utf-8")
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    owner_id = channel._memory_owner(CLIENT_A)
    project = channel._project_store().create(
        owner_id, title="Pico", kind="software", purpose="A safe local workbench"
    )
    source = channel._project_store().add_source(
        owner_id, project.id, kind="local_folder", label="Checkout", locator=str(source_root)
    )
    channel._set_browser_session_orientation(CLIENT_A, SESSION_A, {"project_id": project.id})

    result = asyncio.run(
        channel._browser_project_brief(CLIENT_A, owner_id, project.id, SESSION_A, source.id)
    )

    artifacts = ArtifactStore(workspace).list(owner_id, session_key=channel._session_key(CLIENT_A, SESSION_A))
    assert result["artifact"]["id"] == artifacts[0].id
    assert artifacts[0].kind == "brief"
    content = ArtifactStore(workspace).read_content(owner_id, artifacts[0].id)
    assert "Safe project" in content
    assert "do-not-save" not in content
    assert "[sensitive value redacted]" in content
    assert channel._project_store().get(owner_id, project.id).inspected_at is not None
    activity = channel._project_store().activity(owner_id, project.id)
    assert [(item.kind, item.resource_id) for item in activity] == [("artifact", artifacts[0].id)]
    assert "do-not-save" not in str(activity[0].to_dict())

    with pytest.raises(ValueError, match="Session was not found"):
        asyncio.run(channel._browser_project_brief(CLIENT_A, owner_id, project.id, SESSION_B, source.id))


def test_browser_session_project_brief_is_explicit_revision_pinned_and_owner_scoped(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    owner_id = channel._memory_owner(CLIENT_A)
    brief = ArtifactStore(workspace).create(
        owner_id=owner_id,
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        title="Pico brief",
        kind="brief",
        content_type="text/markdown",
        content="# Summary\nPrivate project text stays in the artifact.",
    )
    note = ArtifactStore(workspace).create(
        owner_id=owner_id,
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        title="Not a brief",
        content="No",
    )
    other_owner_brief = ArtifactStore(workspace).create(
        owner_id=channel._memory_owner(CLIENT_B),
        session_key=channel._session_key(CLIENT_B, SESSION_B),
        title="Other owner brief",
        kind="brief",
        content_type="text/markdown",
        content="No access",
    )

    selected = channel._set_browser_session_project_brief(CLIENT_A, SESSION_A, brief.id)
    assert selected["brief"] == {
        "artifact_id": brief.id,
        "revision": 1,
        "title": "Pico brief",
        "kind": "brief",
        "status": "draft",
    }
    assert "Private project text" not in str(selected)
    with pytest.raises(ValueError, match="Only Brief"):
        channel._set_browser_session_project_brief(CLIENT_A, SESSION_A, note.id)
    with pytest.raises(KeyError):
        channel._set_browser_session_project_brief(CLIENT_A, SESSION_A, other_owner_brief.id)
    assert channel._set_browser_session_project_brief(CLIENT_A, SESSION_A, None) == {"brief": None}
    with pytest.raises(ValueError, match="Session was not found"):
        channel._browser_session_project_brief(CLIENT_B, SESSION_A)


def test_browser_workflow_compiler_is_owner_scoped_and_draft_only(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))

    result = channel._compile_browser_workflow(
        CLIENT_A,
        SESSION_A,
        {"brief": "Research the shared browser tab and create a source brief."},
    )

    workflow = result["workflow"]
    assert workflow.state == "draft"
    assert result["compiler"]["profile_id"] == "personal-work"
    assert any(node.kind == "browser_read" for node in workflow.nodes)
    assert all(node.kind != "browser_action" for node in workflow.nodes)
    assert all(
        node.config.get("harness", {}).get("profile_id") == "personal-work"
        for node in workflow.nodes
        if node.kind in {"agent", "approval", "browser_read"}
    )
    with pytest.raises(ValueError, match="Session was not found"):
        channel._compile_browser_workflow(CLIENT_B, SESSION_A, {"brief": "A private workflow"})


def test_browser_maintenance_is_owner_scoped_and_redacts_action_payloads(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))

    project = channel._project_store().create(
        channel._memory_owner(CLIENT_A),
        title="Pico workbench",
        kind="software",
        purpose="Keep a bounded review queue for Pico.",
    )
    channel._set_browser_session_orientation(CLIENT_A, SESSION_A, {"project_id": project.id})
    actions = ProposedActionStore(workspace)
    actions.stage(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        profile_id="mission-work",
        capability_id="governed_action",
        tool_name="save_artifact",
        target="Local Pico workspace",
        summary="Review the proposed Pico brief",
        payload_data={"private_token": "must-not-appear"},
    )
    actions.stage(
        owner_id=channel._memory_owner(CLIENT_B),
        session_key=channel._session_key(CLIENT_B, SESSION_B),
        profile_id="mission-work",
        capability_id="governed_action",
        tool_name="save_artifact",
        target="Another workspace",
        summary="Another owner's pending action",
        payload_data={"private_token": "also-hidden"},
    )
    ArtifactStore(workspace).create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        title="Review note",
        content="Private artifact contents stay out of the cockpit.",
    )

    result = channel._browser_maintenance(CLIENT_A, SESSION_A)

    assert result["scope"]["project"] == {
        "id": project.id,
        "title": "Pico workbench",
        "kind": "software",
    }
    assert result["counts"]["approvals"] == 1
    assert result["counts"]["artifacts_to_review"] == 1
    assert result["next_action"] == {
        "label": "Review pending approval",
        "detail": "Review the proposed Pico brief",
        "target_view": "operations",
    }
    rendered = json.dumps(result)
    assert "must-not-appear" not in rendered
    assert "also-hidden" not in rendered
    assert "Another owner's pending action" not in rendered
    assert "Private artifact contents" not in rendered
    assert [column["id"] for column in result["board"]] == [
        "prepare",
        "active",
        "waiting",
        "review",
    ]
    assert result["board"][2]["cards"][0]["title"] == "Review the proposed Pico brief"
    assert "must-not-appear" not in json.dumps(result["board"])


def test_project_workspace_folder_picker_is_bounded_to_visible_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "Pico").mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / "node_modules").mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    folders = channel._project_workspace_folders(CLIENT_A)
    assert folders == [
        {"locator": ".", "label": "Pico workspace"},
        {"locator": "Pico", "label": "Pico"},
    ]


def test_browser_session_search_matches_title_and_messages_without_crossing_identity(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config

    sessions = SessionManager(workspace)
    first = sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    first.add_message("user", "Prepare the Pico launch brief")
    sessions.save(first)
    second = sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_B))
    second.metadata["pico_web_title"] = "Research notes"
    second.add_message("assistant", "The launch brief is ready for review")
    sessions.save(second)
    other = sessions.get_or_create(channel._session_key(CLIENT_B, SESSION_A))
    other.add_message("user", "Pico launch brief for another identity")
    sessions.save(other)

    assert [item["id"] for item in channel._list_browser_sessions(CLIENT_A, search="launch brief")] == [
        SESSION_B,
        SESSION_A,
    ]
    assert channel._list_browser_sessions(CLIENT_A, search="another identity") == []


def test_browser_session_archive_is_reversible_and_owner_scoped(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    session = sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A))
    session.add_message("user", "Archive this finished thread")
    sessions.save(session)

    assert channel._set_browser_session_archive(CLIENT_A, SESSION_A, True) == {
        "id": SESSION_A,
        "archived": True,
    }
    assert channel._list_browser_sessions(CLIENT_A) == []
    assert channel._list_browser_sessions(CLIENT_A, include_archived=True)[0]["archived"] is True
    with pytest.raises(ValueError, match="Session was not found"):
        channel._set_browser_session_archive(CLIENT_B, SESSION_A, False)

    channel._set_browser_session_archive(CLIENT_A, SESSION_A, False)
    assert channel._list_browser_sessions(CLIENT_A)[0]["archived"] is False


def test_browser_session_summaries_expose_only_its_active_mission_and_task(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(
        workspace_path=workspace,
        tools=SimpleNamespace(web=SimpleNamespace(search=SimpleNamespace(provider=""))),
    )
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    session_key = channel._session_key(CLIENT_A, SESSION_A)
    sessions = SessionManager(workspace)
    session = sessions.get_or_create(session_key)
    session.metadata["pico_operation_profile"] = "mission-work"
    sessions.save(session)

    owner_id = channel._memory_owner(CLIENT_A)
    mission_store = MissionStore(workspace)
    mission = mission_store.create(
        owner_id=owner_id,
        session_key=session_key,
        title="Ship the session workbench",
        objective="Make active work visible without opening every mission.",
    )
    mission = mission_store.transition(owner_id, mission.id, "active")
    session.metadata["pico_active_mission_id"] = mission.id
    sessions.save(session)

    task_store = TaskStore(workspace)
    task = task_store.create(
        owner_id=owner_id,
        session_key=session_key,
        mission_id=mission.id,
        title="Review session navigation",
        objective="Surface active work in the session rail.",
        capability_profile="mission-work",
    )
    task_store.claim_for_run(owner_id, task.id, session_key, "mission-work", mission)

    listed = channel._list_browser_sessions(CLIENT_A)

    assert listed[0]["active_mission"] == {
        "id": mission.id,
        "title": "Ship the session workbench",
        "state": "active",
    }
    assert listed[0]["active_task"] == {
        "id": task.id,
        "title": "Review session navigation",
        "state": "queued",
        "mission_id": mission.id,
    }
    operations = channel._browser_operations(CLIENT_A, SESSION_A)
    assert operations["active_task"]["id"] == task.id
    assert channel._list_browser_sessions(CLIENT_B) == []


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


def test_browser_feedback_is_bound_to_the_owned_session_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_B, SESSION_B)))

    runs = RunStore(workspace)
    own_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        capability_profile="personal-work",
    )
    other_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_B),
        capability_profile="personal-work",
    )
    feedback = channel._record_browser_feedback(
        CLIENT_A,
        {
            "session_id": SESSION_A,
            "run_id": own_run.id,
            "kind": "correction",
            "note": "Lead with the decision.",
        },
    )
    assert feedback.run_id == own_run.id
    assert channel._list_browser_feedback(CLIENT_A, SESSION_A)[0]["kind"] == "correction"

    with pytest.raises(ValueError, match="does not belong to this session"):
        channel._record_browser_feedback(
            CLIENT_A,
            {"session_id": SESSION_A, "run_id": other_run.id, "kind": "useful"},
        )


def test_browser_artifact_source_is_bound_to_the_owned_session_run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_B)))

    runs = RunStore(workspace)
    own_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        capability_profile="personal-work",
        mission_id="mission-source",
    )
    other_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_B),
        capability_profile="personal-work",
    )

    assert channel._browser_artifact_source(CLIENT_A, SESSION_A, own_run.id) == (
        own_run.id,
        "mission-source",
    )
    with pytest.raises(ValueError, match="does not belong to this session"):
        channel._browser_artifact_source(CLIENT_A, SESSION_A, other_run.id)
    with pytest.raises(ValueError, match="was not found"):
        channel._browser_artifact_source(CLIENT_A, SESSION_A, "missing-run")


def test_artifact_download_name_is_safe_and_keeps_the_format_extension():
    artifact = SimpleNamespace(
        title='Quarterly / "decision" pack',
        revision=3,
        relative_path="artifact-id/v3.yaml",
    )

    assert WebChannel._artifact_download_name(artifact) == "Quarterly-decision-pack-v3.yaml"
    assert WebChannel._artifact_download_name(artifact, 1) == "Quarterly-decision-pack-v1.yaml"


def test_artifact_html_export_escapes_content_and_omits_internal_provenance():
    artifact = SimpleNamespace(
        title="Brief <share>",
        kind="report",
        content_type="text/markdown",
        status="final",
        verification_status="verified",
        source_run_id="run-secret-internal",
        source_mission_id="mission-secret-internal",
    )

    document = WebChannel._artifact_html_export(
        artifact,
        "<script>alert('x')</script>\n# Safe",
        2,
    )

    assert "&lt;script&gt;alert('x')&lt;/script&gt;" in document
    assert "<script>alert('x')</script>" not in document
    assert "run-secret-internal" not in document
    assert "mission-secret-internal" not in document
    assert "revision 2" in document


def test_artifact_bundle_export_is_deterministic_and_contains_safe_manifest():
    artifact = SimpleNamespace(
        title="Decision / pack",
        kind="report",
        content_type="text/markdown",
        status="final",
        verification_status="verified",
        relative_path="artifact-id/v2.md",
        source_run_id="run-secret-internal",
        source_mission_id="mission-secret-internal",
    )
    content = "<script>alert('x')</script>\n# Decision"

    first = WebChannel._artifact_bundle_export(artifact, content, 2)
    second = WebChannel._artifact_bundle_export(artifact, content, 2)

    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as bundle:
        assert bundle.namelist() == [
            "manifest.json",
            "Decision-pack-v2.md",
            "Decision-pack-v2.html",
        ]
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["title"] == artifact.title
        assert manifest["revision"] == 2
        assert "owner_id" not in manifest
        assert "source_run_id" not in manifest
        assert "source_mission_id" not in manifest
        canonical = bundle.read("Decision-pack-v2.md")
        preview = bundle.read("Decision-pack-v2.html").decode("utf-8")
        canonical_entry = next(item for item in manifest["files"] if item["role"] == "canonical")
        assert canonical_entry["sha256"] == hashlib.sha256(canonical).hexdigest()
        assert "&lt;script&gt;alert('x')&lt;/script&gt;" in preview
        assert "run-secret-internal" not in preview
        assert "mission-secret-internal" not in preview


def test_browser_correction_can_become_one_reviewable_memory_or_skill_candidate(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(channel._session_key(CLIENT_A, SESSION_A)))
    runs = RunStore(workspace)
    memory_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        capability_profile="personal-work",
    )
    skill_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        capability_profile="personal-work",
    )
    useful_run = runs.create(
        owner_id=channel._memory_owner(CLIENT_A),
        session_key=channel._session_key(CLIENT_A, SESSION_A),
        capability_profile="personal-work",
    )
    memory_feedback = channel._record_browser_feedback(
        CLIENT_A,
        {
            "session_id": SESSION_A,
            "run_id": memory_run.id,
            "kind": "correction",
            "note": "Keep answers decision-first.",
        },
    )
    skill_feedback = channel._record_browser_feedback(
        CLIENT_A,
        {
            "session_id": SESSION_A,
            "run_id": skill_run.id,
            "kind": "correction",
            "note": "Use a short decision-first workflow.",
        },
    )

    memory_result = channel._create_browser_learning_candidate(
        CLIENT_A,
        memory_feedback.id,
        {"session_id": SESSION_A, "candidate_type": "memory"},
    )
    assert memory_result["candidate"]["status"] == "proposed"
    assert memory_result["candidate"]["source_ref"] == f"feedback:{memory_feedback.id}"
    assert channel._create_browser_learning_candidate(
        CLIENT_A,
        memory_feedback.id,
        {"session_id": SESSION_A, "candidate_type": "memory"},
    )["candidate"]["id"] == memory_result["candidate"]["id"]

    skill_result = channel._create_browser_learning_candidate(
        CLIENT_A,
        skill_feedback.id,
        {
            "session_id": SESSION_A,
            "candidate_type": "skill",
            "name": "decision-first-workflow",
            "description": "Prefer a decision-first response workflow.",
        },
    )
    assert skill_result["candidate"]["status"] == "proposed"
    assert skill_result["candidate"]["source_type"] == "response_correction"
    assert skill_result["candidate"]["source_ref"] == f"feedback:{skill_feedback.id}"

    useful = channel._record_browser_feedback(
        CLIENT_A,
        {
            "session_id": SESSION_A,
            "run_id": useful_run.id,
            "kind": "useful",
        },
    )
    with pytest.raises(ValueError, match="Only correction feedback"):
        channel._create_browser_learning_candidate(
            CLIENT_A,
            useful.id,
            {"session_id": SESSION_A, "candidate_type": "memory"},
        )

    reviews = channel._list_browser_feedback(CLIENT_A, SESSION_A)
    memory_review = next(item for item in reviews if item["id"] == memory_feedback.id)
    assert memory_review["candidate"]["type"] == "memory"
    assert memory_review["candidate"]["status"] == "proposed"
    assert "value" not in memory_review["candidate"]
    assert "owner_id" not in memory_review["candidate"]


def test_browser_run_detail_links_safe_evidence_without_private_payloads(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    owner = channel._memory_owner(CLIENT_A)
    session_key = channel._session_key(CLIENT_A, SESSION_A)
    session_manager = SessionManager(workspace)
    session_manager.save(session_manager.get_or_create(session_key))
    run_store = RunStore(workspace)
    run = run_store.create(
        owner_id=owner,
        session_key=session_key,
        capability_profile="personal-work",
        mission_id="mission-safe",
    )
    run_store.mark_running(owner, run.id)
    run = run_store.complete(owner, run.id, result_ref="message:safe")
    ToolActivityStore(workspace).record(
        owner_id=owner,
        session_key=session_key,
        profile_id="personal-work",
        capability_id="skills.list",
        tool_name="list_skills",
        risk="read",
        outcome="success",
        run_id=run.id,
    )
    action = ProposedActionStore(workspace).stage(
        owner_id=owner,
        session_key=session_key,
        profile_id="workspace-build",
        capability_id="workspace.propose_change",
        tool_name="propose_workspace_change",
        target="notes.txt",
        summary="Draft notes",
        payload_data={"secret": "do-not-return"},
        initiating_run_id=run.id,
    )
    artifact = ArtifactStore(workspace).create(
        owner_id=owner,
        session_key=session_key,
        title="Run brief",
        content="# Safe",
        kind="brief",
        source_run_id=run.id,
    )
    detail = channel._browser_run_detail(CLIENT_A, SESSION_A, run.id)
    assert detail["run"]["run_id"] == run.id
    assert detail["run"]["mission_id"] == "mission-safe"
    assert detail["tools"][0]["run_id"] == run.id
    assert detail["approvals"][0]["id"] == action.id
    assert "payload" not in detail["approvals"][0]
    assert detail["artifacts"][0]["id"] == artifact.id
    assert "do-not-return" not in json.dumps(detail)


def test_browser_session_list_includes_safe_latest_run_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    config = SimpleNamespace(workspace_path=workspace)
    channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._runtime_config = lambda: config
    owner = channel._memory_owner(CLIENT_A)
    session_key = channel._session_key(CLIENT_A, SESSION_A)
    sessions = SessionManager(workspace)
    sessions.save(sessions.get_or_create(session_key))
    run_store = RunStore(workspace)
    run = run_store.create(
        owner_id=owner,
        session_key=session_key,
        capability_profile="personal-work",
        provider="openai",
        model="gpt-test",
    )
    run_store.mark_running(owner, run.id)
    run = run_store.fail(owner, run.id, error_summary="A bounded test failure")

    listed = channel._list_browser_sessions(CLIENT_A)
    assert listed[0]["latest_run"]["run_id"] == run.id
    assert listed[0]["latest_run"]["state"] == "failed"
    assert listed[0]["latest_run"]["error_summary"] == "A bounded test failure"
    assert "owner_id" not in json.dumps(listed)
