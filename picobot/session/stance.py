"""Small, explicit working stances for a Pico thinking session.

Stances shape how Pico helps with a turn; they never grant tools, change a
provider, or widen a capability profile.  Keeping the contract here gives the
prompt builder, web API, and evidence ledger one server-owned vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionStance:
    """Owner-visible communication intent for one session."""

    id: str
    label: str
    description: str
    prompt: str


_STANCES = (
    SessionStance(
        id="explore",
        label="Explore",
        description="Open up the question, clarify it, and surface useful possibilities.",
        prompt=(
            "Help me expand and clarify the question. Surface assumptions, alternatives, "
            "and unknowns without forcing a decision too early."
        ),
    ),
    SessionStance(
        id="decide",
        label="Decide",
        description="Turn ambiguity into a clear choice with criteria and tradeoffs.",
        prompt=(
            "Help me make a clear decision. State the relevant criteria, tradeoffs, "
            "your recommendation, and what evidence would change it."
        ),
    ),
    SessionStance(
        id="make",
        label="Make",
        description="Turn intent into a concrete draft, plan, or other usable work product.",
        prompt=(
            "Help me produce a usable work product. Ask only necessary clarifying questions, "
            "then make a concrete draft, plan, or next artifact."
        ),
    ),
    SessionStance(
        id="review",
        label="Review",
        description="Inspect existing work for strengths, gaps, risks, and precise revisions.",
        prompt=(
            "Help me inspect the existing idea or work product. Identify strengths, gaps, "
            "risks, and precise revisions, prioritizing what matters most."
        ),
    ),
)

DEFAULT_STANCE_ID = "explore"
STANCE_METADATA_KEY = "pico_session_stance"
STANCES = {stance.id: stance for stance in _STANCES}


def get_stance(value: object) -> SessionStance:
    """Resolve a stored or requested stance, rejecting unknown values."""

    if not isinstance(value, str) or value not in STANCES:
        raise ValueError(f"Session stance must be one of: {', '.join(STANCES)}")
    return STANCES[value]


def default_stance() -> SessionStance:
    return STANCES[DEFAULT_STANCE_ID]


def public_stances() -> list[dict[str, str]]:
    """Return the non-sensitive owner-facing stance catalog."""

    return [
        {
            "id": stance.id,
            "label": stance.label,
            "description": stance.description,
        }
        for stance in _STANCES
    ]
