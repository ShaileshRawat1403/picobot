"""Deterministic context-budget planning.

The old context path trimmed history by a fixed message count.  This module
replaces that with a conservative token estimate and an explicit budget.  The
planner is pure: it takes a message list and returns a decision, so it is
trivially testable and never touches storage or the provider.

Decisions:

- ``none``      the whole history fits the budget; keep everything.
- ``compact``   older eligible history is summarized into a durable handoff
                while the protected recent tail and the current request stay
                in the model input.
- ``trim``      nothing can be compacted (too little eligible history, no
                summarizer, or the budget cannot even hold the tail with a
                handoff); bound the window to the largest trailing slice that
                fits the budget.  This is the safe fail-closed fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_BLOCK_OVERHEAD_TOKENS = 3
_MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str | None) -> int:
    """Conservative deterministic token estimate for one text block.

    ``len / 3`` over-counts English (~4 chars/token) and under-counts nothing
    meaningful for a budget guard, so the planner errs toward compaction.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3)) + _BLOCK_OVERHEAD_TOKENS


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "image_url":
                    parts.append("[image]")
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return " ".join(parts)
    return str(content)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the tokens of one message, including role/attachment overhead."""
    tokens = estimate_tokens(_content_to_text(message.get("content")))
    for key in ("tool_calls", "name", "tool_call_id"):
        if message.get(key):
            tokens += 2
    return tokens + _MESSAGE_OVERHEAD_TOKENS


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _aligned_tail_start(messages: list[dict[str, Any]], protected_tail_count: int) -> int:
    """Index where the protected tail begins, aligned to a user message.

    The tail includes the last ``protected_tail_count`` user messages and
    everything after them.
    """
    if protected_tail_count <= 0:
        return len(messages)
    count = 0
    start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            count += 1
            if count > protected_tail_count:
                break
            start = i
    return start


def _max_tail_start_within_budget(
    messages: list[dict[str, Any]], budget_tokens: int
) -> int:
    """Largest start index whose trailing slice fits the token budget.

    The returned slice always begins on a user message so the model never sees
    orphaned tool-result blocks.
    """
    if budget_tokens <= 0:
        return len(messages)
    tokens = 0
    start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        token_count = estimate_message_tokens(messages[i])
        if tokens + token_count > budget_tokens:
            break
        tokens += token_count
        start = i
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    return start


@dataclass(frozen=True)
class ContextPlan:
    """One deterministic planning decision for a context window."""

    action: str  # "none" | "compact" | "trim"
    reason: str
    compact_start: int = 0
    compact_end: int = -1  # inclusive index of the last compactable message
    protected_start: int = 0  # intended protected-tail boundary index
    tail_start: int = 0  # actual window slice start (history[tail_start:])
    tail_count: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0

    @property
    def compactable_count(self) -> int:
        if self.compact_end < self.compact_start:
            return 0
        return self.compact_end - self.compact_start + 1


def plan_context_window(
    history: list[dict[str, Any]],
    *,
    budget_tokens: int,
    protected_tail_count: int,
    current_request_tokens: int = 0,
    min_source_messages: int = 4,
    handoff_estimate_tokens: int = 0,
) -> ContextPlan:
    """Decide how to build the next model window from ``history``.

    ``history`` is the eligible session transcript (user-aligned).  The
    protected recent tail is never summarized: compaction only ever replaces
    ``history[:tail_start]``, and the plan's ``protected_start`` marks where
    the tail begins.
    """
    total = estimate_messages_tokens(history) + current_request_tokens
    if total <= budget_tokens:
        return ContextPlan(
            action="none",
            reason="below_budget",
            tail_start=0,
            tail_count=len(history),
            protected_start=len(history),
            estimated_tokens_before=total,
            estimated_tokens_after=total,
        )

    protected_start = _aligned_tail_start(history, protected_tail_count)

    if protected_start >= min_source_messages:
        tail_budget = max(0, budget_tokens - handoff_estimate_tokens - current_request_tokens)
        fit_start = _max_tail_start_within_budget(history, tail_budget)
        # Compaction is only safe when the protected tail itself fits the
        # budget alongside the handoff and the active request.  When it does
        # not, keep the protected tail intact by trimming instead: compaction
        # must never summarize protected messages.
        if fit_start <= protected_start:
            tail_start = protected_start
            tail = history[tail_start:]
            after = estimate_messages_tokens(tail) + handoff_estimate_tokens + current_request_tokens
            return ContextPlan(
                action="compact",
                reason="over_budget",
                compact_start=0,
                compact_end=tail_start - 1,
                protected_start=protected_start,
                tail_start=tail_start,
                tail_count=len(tail),
                estimated_tokens_before=total,
                estimated_tokens_after=after,
            )

    tail_start = _max_tail_start_within_budget(
        history, max(0, budget_tokens - current_request_tokens)
    )
    tail = history[tail_start:]
    return ContextPlan(
        action="trim",
        reason="not_compactable",
        protected_start=protected_start,
        tail_start=tail_start,
        tail_count=len(tail),
        estimated_tokens_before=total,
        estimated_tokens_after=estimate_messages_tokens(tail) + current_request_tokens,
    )
