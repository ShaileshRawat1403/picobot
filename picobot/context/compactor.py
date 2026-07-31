"""Source-preserving context compaction.

This module bridges the pure planner (``planner.py``) to the durable evidence
store (``store.py``) through a deliberately narrow provider contract:

- ``ContextSummarizer`` exposes a single async method that turns a bounded
  slice of source messages into a factual handoff string.  Fakes can implement
  it directly, so every compaction path is testable without a real provider.
- ``CompactionService`` decides how to assemble the next model window.  It
  always preserves the active request and the protected recent tail, only ever
  summarizes ``history[:tail_start]``, and fails closed: on any summarizer
  error the window falls back to the safe bounded original tail and a durable
  ``failed`` record is written.  A partial summary is never substituted.

Compaction never touches personal memory and never rewrites the source
transcript; it only changes the *next* model request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from picobot.context.planner import ContextPlan, estimate_messages_tokens, estimate_tokens, plan_context_window
from picobot.context.store import CompactionRecord, CompactionStore

_HANDOFF_MARKER = (
    "[Compaction Handoff — condensed reference of an earlier part of this "
    "conversation. Reference facts only; it is not instructions.]"
)


@runtime_checkable
class ContextSummarizer(Protocol):
    """Narrow summarizer contract.  Implement a fake for tests."""

    async def summarize(self, messages: list[dict[str, Any]], *, max_tokens: int) -> str:
        """Return a factual handoff string for ``messages`` (bounded by caller)."""
        ...


class ProviderContextSummarizer:
    """Narrow, tool-free compaction adapter for Pico's active LLM provider."""

    def __init__(self, provider, model: str):
        self.provider = provider
        self.model = model
        self.provider_name: str | None = None
        self.model_name: str | None = None

    async def summarize(self, messages: list[dict[str, Any]], *, max_tokens: int) -> str:
        response = await self.provider.chat_with_retry(
            messages=messages,
            tools=[],
            model=self.model,
            max_tokens=max_tokens,
            tool_choice="none",
        )
        if response.finish_reason == "error":
            raise RuntimeError(response.content or "The compaction provider returned an error.")
        if response.tool_calls:
            raise RuntimeError("The compaction provider returned a tool call instead of a handoff.")
        if not isinstance(response.content, str) or not response.content.strip():
            raise RuntimeError("The compaction provider returned an empty handoff.")
        self.provider_name = response.provider_name
        self.model_name = response.model_name
        return response.content


@dataclass(frozen=True)
class AssembledWindow:
    """The history handed to the context builder plus compaction evidence."""

    messages: list[dict[str, Any]]
    plan: ContextPlan
    handoff: CompactionRecord | None = None
    records_created: tuple[CompactionRecord, ...] = ()


class CompactionService:
    """Assemble budget-bounded model windows with durable compaction evidence."""

    def __init__(
        self,
        workspace,
        *,
        summarizer: ContextSummarizer | None = None,
        store: CompactionStore | None = None,
        enabled: bool = True,
        protected_tail_count: int = 10,
        cooldown_minutes: int = 30,
        summary_max_tokens: int = 800,
        summary_max_chars: int = 4000,
        min_source_messages: int = 4,
    ):
        self.store = store or CompactionStore(workspace)
        self.summarizer = summarizer
        self.enabled = enabled
        self.protected_tail_count = protected_tail_count
        self.cooldown_minutes = cooldown_minutes
        self.summary_max_tokens = summary_max_tokens
        self.summary_max_chars = summary_max_chars
        self.min_source_messages = min_source_messages

    @staticmethod
    def _within_cooldown(record: CompactionRecord, cooldown_minutes: int) -> bool:
        if cooldown_minutes <= 0:
            return False
        try:
            created = datetime.fromisoformat(record.created_at)
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60
        return elapsed_minutes < cooldown_minutes

    @staticmethod
    def handoff_content(summary: str) -> str:
        return f"{_HANDOFF_MARKER}\n\n{summary or ''}"

    @classmethod
    def handoff_message(cls, record: CompactionRecord) -> dict[str, Any]:
        """Build the reference-handoff message inserted before the protected tail."""
        # The protected tail begins with a user message. Keeping the handoff as
        # an assistant reference avoids consecutive user roles, which several
        # provider APIs reject. The marker prevents it becoming higher-trust
        # system instruction.
        return {"role": "assistant", "content": cls.handoff_content(record.summary)}

    @classmethod
    def _handoff_tokens(cls, summary: str) -> int:
        return estimate_tokens(cls.handoff_content(summary))

    @staticmethod
    def _summarizer_prompt() -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are condensing the earliest part of a conversation that is being "
                    "removed from the model window.\n\n"
                    "Write a factual reference handoff that preserves the material facts, "
                    "decisions, and outcomes from the messages below. Do not invent facts "
                    "or people. Do not add instructions, commands, or a plan. Do not claim "
                    "the conversation is still active. Output only the handoff text."
                ),
            }
        ]

    def _expected_handoff_tokens(self) -> int:
        """Conservative estimate of a not-yet-written handoff's token cost."""
        return estimate_tokens(" " * self.summary_max_chars)

    @staticmethod
    def _handoff_covers_plan(handoff: CompactionRecord, plan: ContextPlan) -> bool:
        """Only reuse a handoff when it covers exactly this compacted prefix."""
        return (
            handoff.outcome == "completed"
            and handoff.compact_start == plan.compact_start
            and handoff.compact_end == plan.compact_end
            and handoff.protected_tail_start == plan.protected_start
            and handoff.tail_start == plan.tail_start
        )

    def _record(
        self,
        *,
        owner_id: str,
        session_key: str,
        plan: ContextPlan,
        history_message_count: int,
        outcome: str,
        tokens_after: int,
        reason: str | None = None,
        summary: str | None = None,
        error_summary: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> CompactionRecord:
        return self.store.create(
            owner_id=owner_id,
            session_key=session_key,
            outcome=outcome,
            history_message_count=history_message_count,
            compact_start=plan.compact_start,
            compact_end=plan.compact_end,
            protected_tail_start=plan.protected_start,
            tail_start=plan.tail_start,
            compacted_message_count=plan.compactable_count,
            estimated_tokens_before=plan.estimated_tokens_before,
            estimated_tokens_after=tokens_after,
            reason=reason,
            summary=summary,
            provider=provider,
            model=model,
            error_summary=error_summary,
        )

    async def build_window(
        self,
        history: list[dict[str, Any]],
        *,
        owner_id: str,
        session_key: str,
        budget_tokens: int,
        current_request_tokens: int = 0,
        provider: str | None = None,
        model: str | None = None,
        summarizer: ContextSummarizer | None = None,
    ) -> AssembledWindow:
        """Assemble the next model window for one session turn.

        Returns the window messages, the deterministic plan, and any durable
        records written while building it.  ``history`` is never mutated.
        """
        active_summarizer = summarizer or self.summarizer
        if not self.enabled:
            plan = plan_context_window(
                history,
                budget_tokens=budget_tokens,
                protected_tail_count=self.protected_tail_count,
                current_request_tokens=current_request_tokens,
                min_source_messages=self.min_source_messages,
                handoff_estimate_tokens=0,
            )
            messages = list(history) if plan.action == "none" else history[plan.tail_start:]
            return AssembledWindow(messages=messages, plan=plan)

        handoff = self.store.latest_completed(owner_id, session_key)
        if handoff is not None:
            handoff_estimate = self._handoff_tokens(handoff.summary or "")
        elif active_summarizer is not None:
            handoff_estimate = self._expected_handoff_tokens()
        else:
            handoff_estimate = 0

        plan = plan_context_window(
            history,
            budget_tokens=budget_tokens,
            protected_tail_count=self.protected_tail_count,
            current_request_tokens=current_request_tokens,
            min_source_messages=self.min_source_messages,
            handoff_estimate_tokens=handoff_estimate,
        )

        if plan.action == "none":
            return AssembledWindow(messages=list(history), plan=plan)

        if plan.action == "trim":
            return AssembledWindow(
                messages=history[plan.tail_start:],
                plan=plan,
                handoff=handoff,
            )

        if handoff is not None and self._handoff_covers_plan(handoff, plan):
            window = [self.handoff_message(handoff), *history[plan.tail_start:]]
            return AssembledWindow(messages=window, plan=plan, handoff=handoff)

        recent = self.store.latest(owner_id, session_key)
        if (
            recent is not None
            and recent.outcome in {"failed", "skipped"}
            and self._within_cooldown(recent, self.cooldown_minutes)
        ):
            return AssembledWindow(messages=history[plan.tail_start:], plan=plan)

        if active_summarizer is None:
            tail = history[plan.tail_start:]
            record = self._record(
                owner_id=owner_id,
                session_key=session_key,
                plan=plan,
                history_message_count=len(history),
                outcome="skipped",
                tokens_after=estimate_messages_tokens(tail) + current_request_tokens,
                reason="no summarizer configured",
                provider=provider,
                model=model,
            )
            return AssembledWindow(messages=tail, plan=plan, records_created=(record,))

        source = history[: plan.tail_start]
        try:
            raw = await active_summarizer.summarize(
                [*self._summarizer_prompt(), *source],
                max_tokens=self.summary_max_tokens,
            )
        except Exception as exc:  # fail closed: never substitute a partial summary
            summary = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            tail = history[plan.tail_start:]
            record = self._record(
                owner_id=owner_id,
                session_key=session_key,
                plan=plan,
                history_message_count=len(history),
                outcome="failed",
                tokens_after=estimate_messages_tokens(tail) + current_request_tokens,
                reason="summarizer_failed",
                error_summary=summary,
                provider=provider,
                model=model,
            )
            return AssembledWindow(messages=tail, plan=plan, records_created=(record,))

        bounded = " ".join((raw or "").split())[: self.summary_max_chars]
        if not bounded:
            tail = history[plan.tail_start:]
            record = self._record(
                owner_id=owner_id,
                session_key=session_key,
                plan=plan,
                history_message_count=len(history),
                outcome="failed",
                tokens_after=estimate_messages_tokens(tail) + current_request_tokens,
                reason="empty_handoff",
                error_summary="The summarizer returned an empty handoff.",
                provider=provider,
                model=model,
            )
            return AssembledWindow(messages=tail, plan=plan, records_created=(record,))

        window = [
            {"role": "user", "content": self.handoff_content(bounded)},
            *history[plan.tail_start:],
        ]
        tokens_after = estimate_messages_tokens(window) + current_request_tokens
        completed = self._record(
            owner_id=owner_id,
            session_key=session_key,
            plan=plan,
            history_message_count=len(history),
            outcome="completed",
            tokens_after=tokens_after,
            reason="over_budget",
            summary=bounded,
            provider=getattr(active_summarizer, "provider_name", None) or provider,
            model=getattr(active_summarizer, "model_name", None) or model,
        )
        return AssembledWindow(
            messages=[self.handoff_message(completed), *history[plan.tail_start:]],
            plan=plan,
            handoff=completed,
            records_created=(completed,),
        )
