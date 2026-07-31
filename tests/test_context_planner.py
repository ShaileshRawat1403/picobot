from picobot.context.planner import (
    ContextPlan,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    plan_context_window,
)


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def _history(turns: int = 40, text_length: int = 3000) -> list[dict]:
    history = []
    for i in range(turns):
        history.append(_message("user", f"user-{i} " + "x" * text_length))
        history.append(_message("assistant", f"assistant-{i} " + "y" * text_length))
    return history


def test_below_budget_keeps_everything():
    history = _history(turns=2, text_length=10)
    plan = plan_context_window(
        history,
        budget_tokens=10_000,
        protected_tail_count=4,
        current_request_tokens=20,
    )
    assert plan.action == "none"
    assert plan.reason == "below_budget"
    assert plan.tail_start == 0
    assert plan.tail_count == len(history)
    assert plan.estimated_tokens_after == plan.estimated_tokens_before


def test_over_budget_compacts_older_history_and_keeps_protected_tail():
    history = _history(turns=40, text_length=3000)
    budget = 9_000
    plan = plan_context_window(
        history,
        budget_tokens=budget,
        protected_tail_count=4,
        current_request_tokens=30,
    )
    assert plan.action == "compact"
    assert plan.compact_start == 0
    assert plan.compact_end == plan.tail_start - 1
    assert plan.protected_start > plan.compact_end
    assert plan.compactable_count >= 1
    tail = history[plan.tail_start :]
    assert len(tail) == plan.tail_count
    assert estimate_messages_tokens(tail) + 30 <= budget
    assert tail[0]["role"] == "user"  # never an orphaned tool block


def test_protected_tail_is_never_summarized():
    history = _history(turns=20, text_length=3000)
    plan = plan_context_window(
        history,
        budget_tokens=7_000,
        protected_tail_count=3,
        current_request_tokens=20,
    )
    assert plan.action == "compact"
    compacted = history[plan.compact_start : plan.compact_end + 1]
    protected = history[plan.protected_start :]
    last_users = [m for m in history if m["role"] == "user"][-3:]
    for message in last_users:
        assert message in protected
        assert message not in compacted


def test_trim_fallback_stays_within_budget():
    history = _history(turns=3, text_length=10)
    budget = 40
    plan = plan_context_window(
        history,
        budget_tokens=budget,
        protected_tail_count=4,
        current_request_tokens=5,
        min_source_messages=4,
    )
    assert plan.action == "trim"
    assert plan.reason == "not_compactable"
    assert estimate_messages_tokens(history[plan.tail_start :]) + 5 <= budget


def test_handoff_tokens_reduce_the_tail_budget():
    history = _history(turns=40, text_length=3000)
    budget = 8_200
    kwargs = {
        "budget_tokens": budget,
        "protected_tail_count": 4,
        "current_request_tokens": 30,
        "min_source_messages": 4,
    }
    no_handoff = plan_context_window(history, **kwargs, handoff_estimate_tokens=0)
    with_handoff = plan_context_window(history, **kwargs, handoff_estimate_tokens=2_000)

    assert no_handoff.action == "compact"
    assert no_handoff.tail_start == 72  # the whole protected tail fits alongside nothing
    # Once a handoff must share the budget, the protected tail no longer fits
    # alongside it, so the planner must not compact protected messages: it
    # falls back to a bounded trim.
    assert with_handoff.action == "trim"
    assert with_handoff.tail_start >= no_handoff.tail_start
    assert estimate_messages_tokens(history[no_handoff.tail_start :]) + 30 <= budget
    assert estimate_messages_tokens(history[with_handoff.tail_start :]) + 30 <= budget


def test_over_budget_never_compacts_a_protected_tail_that_overflows():
    # Each message is ~1011 tokens; a 4-user protected tail is ~8088 tokens,
    # larger than this budget. Compaction must degrade to a bounded trim rather
    # than summarize protected messages or overflow the window.
    history = _history(turns=40, text_length=3000)
    plan = plan_context_window(
        history,
        budget_tokens=2_000,
        protected_tail_count=4,
        current_request_tokens=30,
    )
    assert plan.action == "trim"
    assert estimate_messages_tokens(history[plan.tail_start :]) + 30 <= 2_000


def test_token_estimate_is_deterministic_and_counts_blocks():
    text = "hello world " * 10
    assert estimate_tokens(text) == estimate_tokens(text)
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    assert estimate_tokens(text) >= 1

    plain = {"role": "user", "content": text}
    with_image = {
        "role": "user",
        "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
    }
    assert estimate_message_tokens(with_image) >= estimate_message_tokens(plain)
    assert estimate_messages_tokens([plain, with_image]) == (
        estimate_message_tokens(plain) + estimate_message_tokens(with_image)
    )


def test_plan_is_frozen_and_reports_compactable_count():
    history = _history(turns=20, text_length=3000)
    plan = plan_context_window(
        history,
        budget_tokens=7_000,
        protected_tail_count=3,
    )
    assert isinstance(plan, ContextPlan)
    assert plan.action == "compact"
    assert plan.compactable_count == plan.compact_end - plan.compact_start + 1
    try:
        plan.action = "none"
    except (AttributeError, Exception):
        pass
    else:
        raise AssertionError("ContextPlan must be immutable")
