"""Turn a rough message into a prompt worth sending.

A half-formed ask gets a half-formed answer, and the usual fix is to rewrite it
by hand every time, remembering what the model needs to know. Most of what it
needs is already recorded: the project in play, the decisions taken about it,
the constraints learned the hard way.

This assembles that into a draft the owner reviews before sending. It never
sends anything, and it never invents anything. Every line it adds comes from a
stored record, and it reports which records it used, so a compile that reads
oddly can be traced to the memory that caused it rather than guessed at.

Deliberately deterministic. Given the same message and the same workspace it
produces the same draft, which means it can be tested, costs nothing to run,
and cannot quietly reinterpret an ask. Rewriting the owner's own words is left
to the owner.

Standing instructions are not repeated here. AGENTS.md, SOUL.md, USER.md and
TOOLS.md already reach the model through the system prompt, so restating them
in the message would pay for the same tokens twice. They are reported as active
so the owner can see they applied, not pasted in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Memory kinds that belong in a compiled prompt, and the heading each takes.
#: Facts are excluded on purpose: recall already surfaces those, and repeating
#: them here would crowd out the reasoning, which is the part a model cannot
#: re-derive.
_KIND_HEADINGS: dict[str, str] = {
    "decision": "Decisions that apply",
    "constraint": "Constraints",
    "open_question": "Still unresolved",
}

#: Order the headings appear in. Constraints first because they bound the
#: answer; decisions next because they explain the shape; open questions last
#: because they invite rather than restrict.
_KIND_ORDER: tuple[str, ...] = ("constraint", "decision", "open_question")

#: A compiled prompt is a briefing, not a dossier. Past a few entries per kind
#: it stops being read and starts being skipped.
_MAX_PER_KIND = 4


@dataclass(frozen=True)
class CompiledPrompt:
    """A draft for the owner to review, plus what it was built from."""

    text: str
    used_project: str | None = None
    used_memories: list[str] = field(default_factory=list)
    standing_instructions_active: bool = False
    changed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "used_project": self.used_project,
            "used_memories": self.used_memories,
            "standing_instructions_active": self.standing_instructions_active,
            "changed": self.changed,
        }


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _bullet(item: Any) -> str:
    """Render one memory as a line, with its reasoning when it has any.

    ``why`` is included because a constraint without its reason invites the
    model to argue with it. "Never add a retrieval package" reads as a
    preference; adding "the boundary test enforces it" makes it a fact.
    """
    head = _clean(getattr(item, "hook", "") or getattr(item, "value", ""))
    why = _clean(getattr(item, "why", ""))
    return f"- {head} ({why})" if why else f"- {head}"


def compile_prompt(
    raw: str,
    *,
    project: Any = None,
    memories: list[Any] | tuple[Any, ...] = (),
    standing_instructions_active: bool = False,
) -> CompiledPrompt:
    """Assemble a reviewable draft from a rough message.

    Returns the message unchanged, with ``changed=False``, when there is
    nothing to add. Wrapping an ask in empty headings would make the compile
    button feel like it did something while making the prompt worse.
    """
    intent = _clean(raw)
    if not intent:
        raise ValueError("Nothing to compile. Write the ask first.")

    grouped: dict[str, list[Any]] = {}
    used: list[str] = []
    for item in memories:
        kind = getattr(item, "kind", None)
        if kind not in _KIND_HEADINGS:
            continue
        bucket = grouped.setdefault(kind, [])
        if len(bucket) >= _MAX_PER_KIND:
            continue
        bucket.append(item)
        identifier = getattr(item, "id", None)
        if identifier:
            used.append(str(identifier))

    title = _clean(getattr(project, "title", "")) if project is not None else ""
    purpose = _clean(getattr(project, "purpose", "")) if project is not None else ""

    if not grouped and not title:
        return CompiledPrompt(
            text=intent,
            standing_instructions_active=standing_instructions_active,
            changed=False,
        )

    sections: list[str] = [intent]
    if title:
        sections.append(f"Working on {title}." + (f" {purpose}" if purpose else ""))
    for kind in _KIND_ORDER:
        items = grouped.get(kind)
        if not items:
            continue
        lines = "\n".join(_bullet(item) for item in items)
        sections.append(f"{_KIND_HEADINGS[kind]}:\n{lines}")

    return CompiledPrompt(
        text="\n\n".join(sections),
        used_project=title or None,
        used_memories=used,
        standing_instructions_active=standing_instructions_active,
        changed=True,
    )
