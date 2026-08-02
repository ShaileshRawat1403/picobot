from pathlib import Path

import pytest

from picobot.agent.tools.workspace_change import ProposeWorkspaceChangeTool
from picobot.operations.actions import ProposedActionStore
from picobot.operations.workspace_executor import WorkspaceExecutor


OWNER = "web:browser:owner-a"
SESSION = "web:web:owner-a:session-a"


@pytest.mark.asyncio
async def test_workspace_change_proposal_is_durable_bounded_and_non_mutating(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("before\n", encoding="utf-8")

    tool = ProposeWorkspaceChangeTool(workspace)
    tool.set_turn_context(owner_id=OWNER, session_key=SESSION, profile_id="workspace-build")
    result = await tool.execute(
        operation="edit_file",
        path="notes.txt",
        old_text="before",
        new_text="after",
        summary="Update the local note",
        expected_outcome="The note contains the new text.",
    )

    assert "proposed for review" in result
    assert target.read_text(encoding="utf-8") == "before\n"
    action = ProposedActionStore(workspace).list(OWNER, SESSION)[0]
    assert action.status == "proposed"
    assert action.capability_id == "workspace.propose_change"
    assert action.payload_fingerprint
    assert action.payload is not None
    assert action.to_dict().get("payload") is None

    rejected = await tool.execute(
        operation="write_file",
        path="../outside.txt",
        content="no",
        summary="Escape the workspace",
    )
    assert "inside Pico's configured workspace" in rejected
    secret = await tool.execute(
        operation="write_file",
        path=".env",
        content="API_KEY=do-not-store",
        summary="Write a secret",
    )
    assert "credential or secret" in secret


@pytest.mark.asyncio
async def test_approved_workspace_edit_executes_once_with_fingerprint_and_profile_guard(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("before\n", encoding="utf-8")

    proposal_tool = ProposeWorkspaceChangeTool(workspace)
    proposal_tool.set_turn_context(owner_id=OWNER, session_key=SESSION, profile_id="workspace-build")
    await proposal_tool.execute(
        operation="edit_file",
        path="notes.txt",
        old_text="before",
        new_text="after",
        summary="Update the local note",
    )
    store = ProposedActionStore(workspace)
    action = store.list(OWNER, SESSION)[0]
    store.resolve(OWNER, action.id, SESSION, "approve", payload_fingerprint=action.payload_fingerprint)

    executor = WorkspaceExecutor(workspace)
    refused = await executor.execute_action(
        OWNER,
        SESSION,
        action.id,
        payload_fingerprint=action.payload_fingerprint,
        profile_id="personal-work",
    )
    assert refused["failure_category"] == "profile_mismatch"
    assert target.read_text(encoding="utf-8") == "before\n"

    executed = await executor.execute_action(
        OWNER,
        SESSION,
        action.id,
        payload_fingerprint=action.payload_fingerprint,
        profile_id="workspace-build",
    )
    assert executed["status"] == "executed"
    assert target.read_text(encoding="utf-8") == "after\n"
    assert store.get(OWNER, action.id, session_key=SESSION).status == "executed"

    replay = await executor.execute_action(
        OWNER,
        SESSION,
        action.id,
        payload_fingerprint=action.payload_fingerprint,
        profile_id="workspace-build",
    )
    assert replay["status"] == "failed"
    assert "executed" in replay["detail"]


@pytest.mark.asyncio
async def test_workspace_executor_rejects_wrong_owner_and_fingerprint(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ProposeWorkspaceChangeTool(workspace)
    tool.set_turn_context(owner_id=OWNER, session_key=SESSION, profile_id="workspace-build")
    await tool.execute(
        operation="write_file",
        path="new.txt",
        content="hello",
        summary="Create a local file",
    )
    store = ProposedActionStore(workspace)
    action = store.list(OWNER, SESSION)[0]
    store.resolve(OWNER, action.id, SESSION, "approve", payload_fingerprint=action.payload_fingerprint)
    executor = WorkspaceExecutor(workspace)

    wrong_owner = await executor.execute_action(
        "web:browser:owner-b",
        SESSION,
        action.id,
        payload_fingerprint=action.payload_fingerprint,
        profile_id="workspace-build",
    )
    assert wrong_owner["failure_category"] == "not_found"

    mismatch = await executor.execute_action(
        OWNER,
        SESSION,
        action.id,
        payload_fingerprint="wrong",
        profile_id="workspace-build",
    )
    assert mismatch["failure_category"] == "payload_mismatch"
    assert not (workspace / "new.txt").exists()
