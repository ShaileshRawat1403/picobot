"""Tests for the draft-only conversation-to-workflow bridge."""

import pytest

from picobot.workflows import WorkflowDraftCompiler, WorkflowStore


def test_compiler_emits_a_reviewed_draft_without_write_nodes(tmp_path):
    proposal = WorkflowDraftCompiler().compile(
        "Turn our weekly product review into a concise decision record.",
        profile_id="personal-work",
    )

    assert proposal.title == "Turn our weekly product review into a concise decision record"
    assert [node["kind"] for node in proposal.nodes] == [
        "manual_trigger", "agent", "approval", "artifact", "end"
    ]
    assert all(node["kind"] != "browser_action" for node in proposal.nodes)
    assert next(node for node in proposal.nodes if node["id"] == "think")["config"]["harness"]["profile_id"] == "personal-work"
    assert next(edge for edge in proposal.edges if edge["source"] == "review")["condition"] == "approved"

    workflow = WorkflowStore(tmp_path).create_draft(
        owner_id="owner",
        session_key="session",
        title=proposal.title,
        description=proposal.description,
        nodes=proposal.nodes,
        edges=proposal.edges,
    )
    assert workflow.state == "draft"
    assert workflow.title == proposal.title


def test_compiler_includes_read_only_source_checkpoint_when_requested():
    proposal = WorkflowDraftCompiler().compile(
        "Research the shared browser tab and turn its sources into a brief.",
        profile_id="browser-review",
        title="Source brief",
    )

    source = next(node for node in proposal.nodes if node["id"] == "sources")
    assert source["kind"] == "browser_read"
    assert source["config"]["shared_tab_required"] is True
    assert "browser_action" not in {node["kind"] for node in proposal.nodes}


def test_compiler_respects_explicit_configuration_choices():
    proposal = WorkflowDraftCompiler().compile(
        "Turn my notes into a reusable operating plan.",
        profile_id="personal-work",
        source_mode="brief",
        artifact_kind="plan",
    )

    assert "browser_read" not in {node["kind"] for node in proposal.nodes}
    artifact = next(node for node in proposal.nodes if node["kind"] == "artifact")
    assert artifact["config"]["kind"] == "plan"
    assert artifact["config"]["content_type"] == "text/markdown"


@pytest.mark.parametrize("source_mode", ["browser_action", "web", 1])
def test_compiler_rejects_unknown_source_modes(source_mode):
    with pytest.raises(ValueError, match="source mode"):
        WorkflowDraftCompiler().compile(
            "Review a result.", profile_id="personal-work", source_mode=source_mode
        )


@pytest.mark.parametrize("brief", ["", "  ", "x" * 1601, None])
def test_compiler_rejects_missing_or_unbounded_briefs(brief):
    with pytest.raises(ValueError):
        WorkflowDraftCompiler().compile(brief, profile_id="personal-work")
