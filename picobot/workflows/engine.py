"""Bounded workflow orchestration for Pico's graph builder.

The engine advances only persisted graph state.  Actual provider, browser, and
artifact work remains behind Pico's existing governed contracts; nodes that
need an external result pause instead of guessing or executing arbitrary code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from picobot.artifacts import ArtifactStore

from .store import WorkflowDefinition, WorkflowRun, WorkflowStore


class WorkflowEngineError(ValueError):
    """Raised when a workflow cannot advance safely."""


@dataclass(frozen=True)
class StepResult:
    run: WorkflowRun
    node_id: str | None
    node_state: str | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "node_id": self.node_id,
            "node_state": self.node_state,
            "summary": self.summary,
        }


class WorkflowEngine:
    """Advance a DAG one bounded node at a time."""

    _EXTERNAL_NODES = {
        "approval": "Approval is required before this node can continue.",
        "agent": "Agent nodes hand off to a bounded task adapter before they can continue.",
        "browser_read": "Browser reads require an explicit shared-tab result.",
        "browser_action": "Browser writes require an explicit approval and command result.",
        "wait": "Wait nodes require an explicit resume signal.",
    }

    def __init__(self, store: WorkflowStore, artifact_store: ArtifactStore | None = None):
        self.store = store
        self.artifact_store = artifact_store

    @staticmethod
    def _next_node(workflow: WorkflowDefinition, node_id: str, *, branch: str = "success") -> str | None:
        edges = [edge for edge in workflow.edges if edge.source == node_id]
        if not edges:
            return None
        matching = [edge for edge in edges if edge.condition == branch]
        return (matching or edges)[0].target

    def step(
        self, owner_id: str, run_id: str, *, resume: bool = False, result_ref: str | None = None
    ) -> StepResult:
        run = self.store.get_run(owner_id, run_id)
        workflow = self.store.get(owner_id, run.workflow_id)
        if run.state == "queued":
            run = self.store.transition_run(owner_id, run_id, "running")
        elif run.state in {"completed", "failed", "cancelled"}:
            return StepResult(run, run.current_node_id, None, f"Workflow run is already {run.state}.")
        elif run.state == "paused":
            raise WorkflowEngineError("Resume the workflow run before stepping it")
        elif run.state in {"waiting_for_approval", "waiting_for_input"} and not resume:
            return StepResult(run, run.current_node_id, None, "Workflow is waiting for an explicit resume signal.")
        elif run.state in {"waiting_for_approval", "waiting_for_input"}:
            run = self.store.transition_run(owner_id, run_id, "running")

        node = next((item for item in workflow.nodes if item.id == run.current_node_id), None)
        if node is None:
            run = self.store.transition_run(owner_id, run_id, "failed", failure_category="missing_cursor")
            return StepResult(run, None, "failed", "Workflow cursor did not reference a node.")

        self.store.record_node_run(owner_id, run_id, node.id, "running")
        if node.kind in self._EXTERNAL_NODES and not resume:
            waiting_state = "waiting_for_approval" if node.kind in {"approval", "browser_action"} else "waiting_for_input"
            self.store.record_node_run(
                owner_id,
                run_id,
                node.id,
                waiting_state,
                result_ref=self._EXTERNAL_NODES[node.kind],
            )
            run = self.store.transition_run(owner_id, run_id, waiting_state)
            return StepResult(run, node.id, waiting_state, self._EXTERNAL_NODES[node.kind])

        result_ref = result_ref or (node.config.get("result_ref") if isinstance(node.config.get("result_ref"), str) else None)
        if node.kind == "artifact":
            if self.artifact_store is None:
                self.store.record_node_run(owner_id, run_id, node.id, "waiting_for_input", result_ref="Artifact adapter is unavailable.")
                run = self.store.transition_run(owner_id, run_id, "waiting_for_input")
                return StepResult(run, node.id, "waiting_for_input", "Artifact adapter is unavailable.")
            content = node.config.get("content")
            if not isinstance(content, str) or not content.strip():
                self.store.record_node_run(owner_id, run_id, node.id, "waiting_for_input", result_ref="Artifact content is required.")
                run = self.store.transition_run(owner_id, run_id, "waiting_for_input")
                return StepResult(run, node.id, "waiting_for_input", "Artifact content is required in the node inspector.")
            artifact = self.artifact_store.create(
                owner_id=owner_id,
                session_key=run.session_key,
                title=str(node.config.get("title") or node.title),
                content=content,
                kind=str(node.config.get("kind") or "note"),
                content_type=str(node.config.get("content_type") or "text/markdown"),
                source_run_id=run.id,
            )
            result_ref = artifact.id

        self.store.record_node_run(
            owner_id,
            run_id,
            node.id,
            "succeeded",
            result_ref=result_ref,
        )
        branch = str(node.config.get("branch", "success")) if node.kind == "condition" else "success"
        next_id = self._next_node(workflow, node.id, branch=branch)
        if node.kind == "end" or next_id is None:
            run = self.store.advance_run(owner_id, run_id, None)
            run = self.store.transition_run(owner_id, run_id, "completed")
            return StepResult(run, node.id, "succeeded", "Workflow completed.")
        run = self.store.advance_run(owner_id, run_id, next_id)
        return StepResult(run, node.id, "succeeded", f"Advanced to {next_id}.")

    def run_until_wait(
        self,
        owner_id: str,
        run_id: str,
        *,
        max_steps: int = 40,
        resume: bool = False,
        result_ref: str | None = None,
    ) -> list[StepResult]:
        if isinstance(max_steps, bool) or not 1 <= max_steps <= 40:
            raise WorkflowEngineError("Workflow step limit must be between 1 and 40")
        results: list[StepResult] = []
        for _ in range(max_steps):
            result = self.step(owner_id, run_id, resume=resume, result_ref=result_ref)
            resume = False
            result_ref = None
            results.append(result)
            if result.run.state in {"waiting_for_approval", "waiting_for_input", "completed", "failed", "cancelled"}:
                break
        return results
