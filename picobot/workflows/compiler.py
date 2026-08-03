"""Deterministic, draft-only bridge from an explicit brief to a Pico workflow.

The compiler deliberately does *not* call a model or inspect a transcript.  An
owner supplies a short brief, Pico turns it into a small graph that can be
edited, and the normal workflow approval and execution contracts remain in
charge.  This keeps the convenience of "turn this into a workflow" without
silently turning conversation into authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_MAX_BRIEF_LENGTH = 1_600
_SOURCE_MODES = {"brief", "shared_tab"}
_ARTIFACT_KINDS = {
    "note",
    "brief",
    "plan",
    "report",
    "draft",
    "checklist",
    "data",
    "link",
    "code",
    "config",
}
_BROWSER_SIGNALS = (
    "article",
    "browser",
    "browse",
    "compare sources",
    "investigate",
    "research",
    "shared tab",
    "source",
    "website",
    "web ",
)


@dataclass(frozen=True)
class CompiledWorkflow:
    """A bounded workflow proposal, ready for the durable draft store."""

    title: str
    description: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "nodes": self.nodes,
            "edges": self.edges,
            "summary": self.summary,
        }


class WorkflowDraftCompiler:
    """Compile one owner-authored brief into a safe editable graph."""

    @staticmethod
    def _brief(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Workflow brief must be text")
        brief = " ".join(value.split())
        if not brief:
            raise ValueError("Describe the repeatable outcome before compiling a workflow")
        if len(brief) > _MAX_BRIEF_LENGTH:
            raise ValueError(f"Workflow brief is limited to {_MAX_BRIEF_LENGTH} characters")
        return brief

    @staticmethod
    def _title(brief: str, requested_title: object) -> str:
        if isinstance(requested_title, str) and requested_title.strip():
            title = " ".join(requested_title.split())
            if len(title) > 160:
                raise ValueError("Workflow title is limited to 160 characters")
            return title
        sentence = re.split(r"[.!?]", brief, maxsplit=1)[0].strip()
        if len(sentence) > 72:
            sentence = sentence[:69].rstrip() + "..."
        return sentence or "Untitled workflow"

    @staticmethod
    def _needs_browser_read(brief: str) -> bool:
        lowered = f" {brief.casefold()} "
        return any(signal in lowered for signal in _BROWSER_SIGNALS)

    @staticmethod
    def _source_mode(value: object, brief: str) -> str:
        if value is None:
            return "shared_tab" if WorkflowDraftCompiler._needs_browser_read(brief) else "brief"
        if not isinstance(value, str) or value not in _SOURCE_MODES:
            raise ValueError("Workflow source mode must be brief or shared_tab")
        return value

    @staticmethod
    def _artifact_kind(value: object) -> str:
        if value is None:
            return "brief"
        if not isinstance(value, str) or value not in _ARTIFACT_KINDS:
            raise ValueError("Workflow artifact kind is not supported")
        return value

    @staticmethod
    def _artifact_content_type(kind: str) -> str:
        if kind == "data":
            return "application/json"
        if kind == "link":
            return "text/uri-list"
        if kind in {"code", "config"}:
            return "text/plain"
        return "text/markdown"

    def compile(
        self,
        brief: object,
        *,
        profile_id: str,
        title: object = None,
        source_mode: object = None,
        artifact_kind: object = None,
    ) -> CompiledWorkflow:
        """Return a graph proposal without persisting or executing anything."""
        clean_brief = self._brief(brief)
        clean_title = self._title(clean_brief, title)
        source = self._source_mode(source_mode, clean_brief)
        output_kind = self._artifact_kind(artifact_kind)
        includes_browser_read = source == "shared_tab"
        harness = {
            "profile_id": profile_id,
            "authority": "current_session_profile_only",
            "approval_required": True,
        }
        nodes: list[dict[str, Any]] = [
            {
                "id": "start",
                "kind": "manual_trigger",
                "title": "Start",
                "description": "Begin only when the owner chooses Run.",
                "config": {},
                "x": 80,
                "y": 220,
            }
        ]
        edges: list[dict[str, Any]] = []
        previous = "start"
        if includes_browser_read:
            nodes.append(
                {
                    "id": "sources",
                    "kind": "browser_read",
                    "title": "Inspect sources",
                    "description": "Read only an explicitly shared browser tab.",
                    "config": {"harness": harness, "shared_tab_required": True},
                    "x": 330,
                    "y": 220,
                }
            )
            edges.append({"id": "start_to_sources", "source": previous, "target": "sources", "condition": "success"})
            previous = "sources"
        nodes.extend(
            [
                {
                    "id": "think",
                    "kind": "agent",
                    "title": "Work through the brief",
                    "description": "Hand off to a bounded task only after the draft is reviewed.",
                    "config": {"harness": harness, "brief": clean_brief},
                    "x": 580 if includes_browser_read else 330,
                    "y": 220,
                },
                {
                    "id": "review",
                    "kind": "approval",
                    "title": "Review outcome",
                    "description": "Pause for the owner before preserving a result.",
                    "config": {"harness": harness},
                    "x": 830 if includes_browser_read else 580,
                    "y": 220,
                },
                {
                    "id": "preserve",
                    "kind": "artifact",
                    "title": "Preserve useful result",
                    "description": "Create a durable artifact only with explicit content.",
                    "config": {
                        "kind": output_kind,
                        "title": clean_title,
                        "content_type": self._artifact_content_type(output_kind),
                        "requires_owner_content": True,
                    },
                    "x": 1080 if includes_browser_read else 830,
                    "y": 220,
                },
                {
                    "id": "finish",
                    "kind": "end",
                    "title": "Finish",
                    "description": "Close the inspected workflow run.",
                    "config": {},
                    "x": 1330 if includes_browser_read else 1080,
                    "y": 220,
                },
            ]
        )
        edges.extend(
            [
                {"id": f"{previous}_to_think", "source": previous, "target": "think", "condition": "success"},
                {"id": "think_to_review", "source": "think", "target": "review", "condition": "success"},
                {"id": "review_to_preserve", "source": "review", "target": "preserve", "condition": "approved", "note": "The owner must approve before Pico preserves the result."},
                {"id": "preserve_to_finish", "source": "preserve", "target": "finish", "condition": "success"},
            ]
        )
        browser_note = " It includes a read-only shared-tab checkpoint." if includes_browser_read else ""
        return CompiledWorkflow(
            title=clean_title,
            description=clean_brief,
            nodes=nodes,
            edges=edges,
            summary=(
                f"Created a draft-only workflow with a bounded task, owner review, and an explicit {output_kind} artifact step."
                f"{browser_note} Review the graph, its harnesses, and its profile before approval."
            ),
        )
