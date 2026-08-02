"""Tests for Pico's bounded workflow graph and execution ledger."""

from pathlib import Path

import pytest

from picobot.workflows import WorkflowEngine, WorkflowStore
from picobot.artifacts import ArtifactStore


OWNER = "web:browser:test-owner"
SESSION = "web:web:test-owner:session-1"


def graph(*, include_agent: bool = False):
    nodes = [
        {"id": "start", "kind": "manual_trigger", "title": "Start", "x": 40, "y": 80},
        {"id": "approval", "kind": "approval", "title": "Review", "x": 280, "y": 80},
        {"id": "finish", "kind": "end", "title": "Finish", "x": 520, "y": 80},
    ]
    if include_agent:
        nodes.insert(1, {"id": "agent", "kind": "agent", "title": "Think", "x": 160, "y": 80})
        edges = [
            {"id": "e1", "source": "start", "target": "agent"},
            {"id": "e2", "source": "agent", "target": "approval"},
            {"id": "e3", "source": "approval", "target": "finish"},
        ]
    else:
        edges = [
            {"id": "e1", "source": "start", "target": "approval"},
            {"id": "e2", "source": "approval", "target": "finish"},
        ]
    return nodes, edges


def make_store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(tmp_path)


def test_graph_validation_is_strict(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, edges = graph()
    workflow = store.create_draft(
        owner_id=OWNER, session_key=SESSION, title="Weekly review", description=None, nodes=nodes, edges=edges
    )
    assert workflow.version == 1
    with pytest.raises(ValueError, match="exactly one trigger"):
        store.create_draft(
            owner_id=OWNER,
            session_key=SESSION,
            title="Bad",
            description=None,
            nodes=nodes + [{"id": "other", "kind": "manual_trigger", "title": "Other"}],
            edges=edges,
        )
    with pytest.raises(ValueError, match="cycles"):
        store.create_draft(
            owner_id=OWNER,
            session_key=SESSION,
            title="Cycle",
            description=None,
            nodes=nodes,
            edges=edges + [{"id": "e3", "source": "finish", "target": "start"}],
        )
    with pytest.raises(ValueError, match="secret-like"):
        store.create_draft(
            owner_id=OWNER,
            session_key=SESSION,
            title="Secret",
            description=None,
            nodes=[{**nodes[0], "config": {"api_key": "never"}}, *nodes[1:]],
            edges=edges,
        )
    with pytest.raises(ValueError, match="edge condition"):
        store.create_draft(
            owner_id=OWNER,
            session_key=SESSION,
            title="Unknown transition",
            description=None,
            nodes=nodes,
            edges=[{**edges[0], "condition": "arbitrary_code"}, edges[1]],
        )


def test_lifecycle_version_and_owner_session_isolation(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, edges = graph()
    workflow = store.create_draft(
        owner_id=OWNER, session_key=SESSION, title="Draft", description=None, nodes=nodes, edges=edges
    )
    updated = store.save_draft(
        OWNER,
        workflow.id,
        title="Updated",
        description="A small durable flow",
        nodes=nodes,
        edges=edges,
    )
    assert updated.version == 2
    assert store.transition(OWNER, workflow.id, "approved").state == "approved"
    with pytest.raises(KeyError):
        store.get("web:browser:other", workflow.id)
    with pytest.raises(ValueError, match="session"):
        store.start_run(OWNER, workflow.id, "web:web:test-owner:other")


def test_transition_note_and_node_harness_round_trip(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, edges = graph()
    nodes[1]["config"] = {
        "harness": {
            "profile_id": "research",
            "toolset": "research",
            "approval_required": True,
            "retry_limit": 1,
            "timeout_seconds": 180,
        }
    }
    edges[0]["condition"] = "approved"
    edges[0]["note"] = "Only continue after the owner approves the findings."
    workflow = store.create_draft(
        owner_id=OWNER,
        session_key=SESSION,
        title="Governed transition",
        description=None,
        nodes=nodes,
        edges=edges,
    )
    assert workflow.edges[0].condition == "approved"
    assert workflow.edges[0].note == "Only continue after the owner approves the findings."
    assert workflow.nodes[1].config["harness"]["profile_id"] == "research"


def test_engine_pauses_for_approval_then_completes(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, edges = graph()
    workflow = store.create_draft(
        owner_id=OWNER, session_key=SESSION, title="Review flow", description=None, nodes=nodes, edges=edges
    )
    store.transition(OWNER, workflow.id, "approved")
    run = store.start_run(OWNER, workflow.id, SESSION)
    engine = WorkflowEngine(store)
    first = engine.run_until_wait(OWNER, run.id)[-1]
    assert first.run.state == "waiting_for_approval"
    assert first.node_id == "approval"
    resumed = engine.run_until_wait(OWNER, run.id, resume=True)[-1]
    assert resumed.run.state == "completed"
    detail = store.detail(OWNER, run.id)
    assert detail["run"]["state"] == "completed"
    assert any(event["event_type"] == "run_waiting_for_approval" for event in detail["events"])


def test_engine_uses_approval_transition_condition(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, _ = graph()
    edges = [
        {"id": "e1", "source": "start", "target": "approval", "condition": "success"},
        {"id": "e2", "source": "approval", "target": "finish", "condition": "approved"},
    ]
    workflow = store.create_draft(
        owner_id=OWNER, session_key=SESSION, title="Approval path", description=None, nodes=nodes, edges=edges
    )
    store.transition(OWNER, workflow.id, "approved")
    run = store.start_run(OWNER, workflow.id, SESSION)
    first = WorkflowEngine(store).run_until_wait(OWNER, run.id)[-1]
    assert first.run.state == "waiting_for_approval"
    resumed = WorkflowEngine(store).run_until_wait(OWNER, run.id, resume=True)[-1]
    assert resumed.run.state == "completed"


def test_external_agent_node_waits_without_provider_execution(tmp_path: Path):
    store = make_store(tmp_path)
    nodes, edges = graph(include_agent=True)
    workflow = store.create_draft(
        owner_id=OWNER, session_key=SESSION, title="Agent flow", description=None, nodes=nodes, edges=edges
    )
    store.transition(OWNER, workflow.id, "approved")
    run = store.start_run(OWNER, workflow.id, SESSION)
    result = WorkflowEngine(store).run_until_wait(OWNER, run.id)[-1]
    assert result.run.state == "waiting_for_input"
    assert "task adapter" in result.summary


def test_artifact_node_creates_durable_output(tmp_path: Path):
    store = make_store(tmp_path)
    nodes = [
        {"id": "start", "kind": "manual_trigger", "title": "Start"},
        {"id": "artifact", "kind": "artifact", "title": "Capture", "config": {"content": "A durable note", "kind": "note"}},
        {"id": "finish", "kind": "end", "title": "Finish"},
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "artifact"},
        {"id": "e2", "source": "artifact", "target": "finish"},
    ]
    workflow = store.create_draft(owner_id=OWNER, session_key=SESSION, title="Capture", description=None, nodes=nodes, edges=edges)
    store.transition(OWNER, workflow.id, "approved")
    run = store.start_run(OWNER, workflow.id, SESSION)
    results = WorkflowEngine(store, ArtifactStore(tmp_path)).run_until_wait(OWNER, run.id)
    assert results[-1].run.state == "completed"
    artifacts = ArtifactStore(tmp_path).list(OWNER, session_key=SESSION)
    assert len(artifacts) == 1
    assert ArtifactStore(tmp_path).read_content(OWNER, artifacts[0].id) == "A durable note"


def test_node_result_is_idempotent_when_a_run_is_retried(tmp_path: Path):
    store = make_store(tmp_path)
    nodes = [
        {"id": "start", "kind": "manual_trigger", "title": "Start"},
        {"id": "finish", "kind": "end", "title": "Finish"},
    ]
    workflow = store.create_draft(owner_id=OWNER, session_key=SESSION, title="Retry safe", description=None, nodes=nodes, edges=[{"id": "e1", "source": "start", "target": "finish"}])
    store.transition(OWNER, workflow.id, "approved")
    run = store.start_run(OWNER, workflow.id, SESSION)
    store.transition_run(OWNER, run.id, "running")
    store.record_node_run(OWNER, run.id, "start", "succeeded", result_ref="triggered")
    result = WorkflowEngine(store).run_until_wait(OWNER, run.id)[-1]
    assert result.run.state == "completed"
    node_runs = store.detail(OWNER, run.id)["nodes"]
    assert sum(node["node_id"] == "start" for node in node_runs) == 1
