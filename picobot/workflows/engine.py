"""Bounded workflow orchestration for Pico's graph builder.

The engine advances only persisted graph state.  Actual provider, browser, and
artifact work remains behind Pico's existing governed contracts; nodes that
need an external result pause instead of guessing or executing arbitrary code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from picobot.artifacts import ArtifactStore

from .store import WorkflowDefinition, WorkflowNodeRun, WorkflowRun, WorkflowStore


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

    def __init__(
        self,
        store: WorkflowStore,
        artifact_store: ArtifactStore | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.artifact_store = artifact_store
        self._now_fn = now if callable(now) else (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _next_node(workflow: WorkflowDefinition, node_id: str, *, branch: str = "success") -> str | None:
        edges = [edge for edge in workflow.edges if edge.source == node_id]
        if not edges:
            return None
        matching = [edge for edge in edges if edge.condition == branch]
        return matching[0].target if matching else None

    @staticmethod
    def _node_branch(node: Any, *, resume: bool) -> str:
        if node.kind == "condition":
            branch = node.config.get("branch", "success")
            return str(branch) if isinstance(branch, str) and branch.strip() else "success"
        if resume and node.kind == "approval":
            return "approved"
        harness = node.config.get("harness") if isinstance(node.config, dict) else None
        if isinstance(harness, dict):
            branch = harness.get("resume_condition")
            if isinstance(branch, str) and branch.strip():
                return branch.strip()
        return "success"

    @staticmethod
    def _harness(node: Any) -> dict[str, Any]:
        config = node.config if isinstance(node.config, dict) else {}
        harness = config.get("harness")
        return harness if isinstance(harness, dict) else {}

    def _node_timed_out(self, node_run: WorkflowNodeRun, node: Any) -> bool:
        timeout_seconds = self._harness(node).get("timeout_seconds")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            return False
        if not node_run.started_at:
            return False
        try:
            started = datetime.fromisoformat(node_run.started_at)
        except ValueError:
            return False
        return (self._now_fn() - started).total_seconds() > timeout_seconds

    def _route_failure(
        self,
        owner_id: str,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        node: Any,
        failed_attempt: WorkflowNodeRun,
        failure_category: str,
    ) -> StepResult:
        retry_limit = self._harness(node).get("retry_limit")
        if isinstance(retry_limit, bool) or not isinstance(retry_limit, int) or retry_limit < 0:
            retry_limit = 0
        retry_limit = min(retry_limit, 9)
        if failed_attempt.attempt <= retry_limit:
            next_attempt = failed_attempt.attempt + 1
            if node.kind in self._EXTERNAL_NODES:
                waiting_state = "waiting_for_approval" if node.kind in {"approval", "browser_action"} else "waiting_for_input"
                self.store.transition_run(owner_id, run.id, "running")
                self.store.record_node_run(
                    owner_id,
                    run.id,
                    node.id,
                    waiting_state,
                    attempt=next_attempt,
                    result_ref=self._EXTERNAL_NODES[node.kind],
                )
                run = self.store.transition_run(owner_id, run.id, waiting_state)
                return StepResult(
                    run,
                    node.id,
                    waiting_state,
                    f"Retrying node after {failure_category} (attempt {next_attempt} of {retry_limit + 1}).",
                )
            self.store.record_node_run(owner_id, run.id, node.id, "running", attempt=next_attempt)
            run = self.store.transition_run(owner_id, run.id, "running")
            return StepResult(
                run,
                node.id,
                "running",
                f"Retrying node after {failure_category} (attempt {next_attempt} of {retry_limit + 1}).",
            )
        branch = failure_category if failure_category in WorkflowStore._EDGE_CONDITIONS else "error"
        next_id = self._next_node(workflow, node.id, branch=branch)
        if next_id is None and branch != "error":
            next_id = self._next_node(workflow, node.id, branch="error")
        if next_id is not None:
            run = self.store.advance_run(owner_id, run.id, next_id)
            run = self.store.transition_run(owner_id, run.id, "running")
            return StepResult(
                run,
                node.id,
                "failed",
                f"Node failed after {failed_attempt.attempt} attempts; advanced to {next_id}.",
            )
        run = self.store.transition_run(owner_id, run.id, "failed", failure_category=failure_category)
        return StepResult(run, node.id, "failed", f"Node failed; workflow run ended ({failure_category}).")

    def step(
        self,
        owner_id: str,
        run_id: str,
        *,
        resume: bool = False,
        result_ref: str | None = None,
        output_summary: str | None = None,
        failure_category: str | None = None,
    ) -> StepResult:
        run = self.store.get_run(owner_id, run_id)
        workflow = self.store.get(owner_id, run.workflow_id)
        if run.state == "queued":
            run = self.store.transition_run(owner_id, run_id, "running")
        elif run.state in {"completed", "failed", "cancelled"}:
            return StepResult(run, run.current_node_id, None, f"Workflow run is already {run.state}.")
        elif run.state == "paused":
            raise WorkflowEngineError("Resume the workflow run before stepping it")

        node = next((item for item in workflow.nodes if item.id == run.current_node_id), None)
        if node is None:
            run = self.store.transition_run(owner_id, run_id, "failed", failure_category="missing_cursor")
            return StepResult(run, None, "failed", "Workflow cursor did not reference a node.")

        branch = self._node_branch(node, resume=resume)
        prior = self.store.latest_node_run(owner_id, run_id, node.id)
        attempt = prior.attempt if prior else 1

        if (
            prior
            and prior.state in {"waiting_for_approval", "waiting_for_input"}
            and self._node_timed_out(prior, node)
        ):
            self.store.record_node_run(
                owner_id,
                run_id,
                node.id,
                "failed",
                attempt=attempt,
                failure_category="timeout",
            )
            return self._route_failure(owner_id, run, workflow, node, prior, "timeout")

        if run.state in {"waiting_for_approval", "waiting_for_input"}:
            if not resume:
                return StepResult(run, node.id, None, "Workflow is waiting for an explicit resume signal.")
            run = self.store.transition_run(owner_id, run_id, "running")

        if prior and prior.state == "succeeded":
            next_id = self._next_node(workflow, node.id, branch=branch)
            if node.kind == "end" or next_id is None:
                self.store.advance_run(owner_id, run_id, None)
                run = self.store.transition_run(owner_id, run_id, "completed")
                return StepResult(run, node.id, "succeeded", "Workflow completed from a durable node result.")
            run = self.store.advance_run(owner_id, run_id, next_id)
            return StepResult(run, node.id, "succeeded", f"Resumed from the durable result; advanced to {next_id}.")

        self.store.record_node_run(owner_id, run_id, node.id, "running", attempt=attempt)
        if node.kind in self._EXTERNAL_NODES and not resume:
            waiting_state = "waiting_for_approval" if node.kind in {"approval", "browser_action"} else "waiting_for_input"
            self.store.record_node_run(
                owner_id,
                run_id,
                node.id,
                waiting_state,
                attempt=attempt,
                result_ref=self._EXTERNAL_NODES[node.kind],
            )
            run = self.store.transition_run(owner_id, run_id, waiting_state)
            return StepResult(run, node.id, waiting_state, self._EXTERNAL_NODES[node.kind])

        result_ref = result_ref or (node.config.get("result_ref") if isinstance(node.config.get("result_ref"), str) else None)
        if node.kind in self._EXTERNAL_NODES and resume:
            # A resume is an owner-authorized handoff completion.  Persist only
            # a bounded summary, not provider output, browser payloads, or
            # reasoning.  That summary can be deliberately preserved by a
            # later artifact node after any intervening approval.
            if failure_category:
                failed_run = self.store.record_node_run(
                    owner_id,
                    run_id,
                    node.id,
                    "failed",
                    attempt=attempt,
                    failure_category=failure_category,
                )
                return self._route_failure(owner_id, run, workflow, node, failed_run, failure_category)
            self.store.record_node_run(
                owner_id,
                run_id,
                node.id,
                "succeeded",
                attempt=attempt,
                result_ref=result_ref,
                output_summary=output_summary,
            )
            next_id = self._next_node(workflow, node.id, branch=branch)
            if node.kind == "end" or next_id is None:
                run = self.store.advance_run(owner_id, run_id, None)
                run = self.store.transition_run(owner_id, run_id, "completed")
                return StepResult(run, node.id, "succeeded", "Workflow completed from an owner-confirmed outcome.")
            run = self.store.advance_run(owner_id, run_id, next_id)
            return StepResult(run, node.id, "succeeded", f"Recorded a bounded outcome; advanced to {next_id}.")
        if node.kind == "artifact":
            if self.artifact_store is None:
                self.store.record_node_run(owner_id, run_id, node.id, "waiting_for_input", attempt=attempt, result_ref="Artifact adapter is unavailable.")
                run = self.store.transition_run(owner_id, run_id, "waiting_for_input")
                return StepResult(run, node.id, "waiting_for_input", "Artifact adapter is unavailable.")
            content = node.config.get("content")
            if not isinstance(content, str) or not content.strip():
                if node.config.get("content_from") == "previous_output":
                    content = self.store.latest_output_summary(owner_id, run_id, exclude_node_id=node.id)
            if not isinstance(content, str) or not content.strip():
                self.store.record_node_run(owner_id, run_id, node.id, "waiting_for_input", attempt=attempt, result_ref="Artifact content is required.")
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
            output_summary = f"Created durable artifact: {artifact.title}"
            self.store.set_run_result_ref(owner_id, run_id, artifact.id)

        self.store.record_node_run(
            owner_id,
            run_id,
            node.id,
            "succeeded",
            attempt=attempt,
            result_ref=result_ref,
            output_summary=output_summary,
        )
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
        output_summary: str | None = None,
        failure_category: str | None = None,
    ) -> list[StepResult]:
        if isinstance(max_steps, bool) or not 1 <= max_steps <= 40:
            raise WorkflowEngineError("Workflow step limit must be between 1 and 40")
        results: list[StepResult] = []
        for _ in range(max_steps):
            result = self.step(
                owner_id,
                run_id,
                resume=resume,
                result_ref=result_ref,
                output_summary=output_summary,
                failure_category=failure_category,
            )
            resume = False
            result_ref = None
            output_summary = None
            failure_category = None
            results.append(result)
            if result.run.state in {"waiting_for_approval", "waiting_for_input", "completed", "failed", "cancelled"}:
                break
        return results
