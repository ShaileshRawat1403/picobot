"""Session summary worker.

Pure function over session transcript events and memory records returning
a typed result with source attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionSummaryResult:
    session_id: str
    total_turns: int
    user_turns: int
    assistant_turns: int
    decisions_count: int
    open_questions_count: int
    key_topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_turns": self.total_turns,
            "user_turns": self.user_turns,
            "assistant_turns": self.assistant_turns,
            "decisions_count": self.decisions_count,
            "open_questions_count": self.open_questions_count,
            "key_topics": self.key_topics,
            "sources": self.sources,
        }


def run_session_summary_worker(
    session_id: str,
    transcript_events: list[dict[str, Any]],
    memory_items: list[dict[str, Any]] | None = None,
) -> SessionSummaryResult:
    """Execute session summary worker over in-memory transcript & memory records."""
    user_count = 0
    assistant_count = 0
    topics: set[str] = set()

    for event in transcript_events:
        role = event.get("role") or event.get("sender")
        if role in {"user", "human"}:
            user_count += 1
        elif role in {"assistant", "bot", "model"}:
            assistant_count += 1

        content = str(event.get("content") or "").lower()
        if "test" in content:
            topics.add("testing")
        if "feature" in content or "roadmap" in content:
            topics.add("product roadmap")
        if "diff" in content or "commit" in content or "git" in content:
            topics.add("git repository")
        if "worker" in content or "workflow" in content:
            topics.add("worker engine")

    memories = memory_items or []
    decisions = [m for m in memories if m.get("kind") == "decision" or m.get("type") == "decision"]
    questions = [m for m in memories if m.get("kind") == "open_question" or m.get("type") == "open_question"]

    return SessionSummaryResult(
        session_id=session_id,
        total_turns=len(transcript_events),
        user_turns=user_count,
        assistant_turns=assistant_count,
        decisions_count=len(decisions),
        open_questions_count=len(questions),
        key_topics=sorted(list(topics)),
        sources=[
            f"session://{session_id}",
            f"transcript://{session_id}?events={len(transcript_events)}",
            f"memory://{session_id}?items={len(memories)}",
        ],
    )
