"""Explicit, non-authoritative orientation for a Pico work session.

Orientation makes the owner's current project and working intent inspectable.
It deliberately has no bearing on providers, tools, approval, or execution
authority; those remain governed by the capability profile and runtime policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ORIENTATION_METADATA_KEY = "pico_session_orientation"
_MAX_PROJECT_ID = 160
_MAX_OBJECTIVE = 2_000
_MAX_EXPECTED_RESULT = 1_000
_MAX_CONSTRAINTS = 8
_MAX_CONSTRAINT = 500


@dataclass(frozen=True)
class RoleLens:
    id: str
    label: str
    description: str
    prompt: str


@dataclass(frozen=True)
class ChallengePolicy:
    id: str
    label: str
    description: str
    prompt: str


_ROLES = (
    RoleLens("founder", "Founder / strategist", "Frame choices, leverage, and opportunity cost.",
             "Frame the work around intent, leverage, opportunity cost, and the smallest credible next move."),
    RoleLens("systems_designer", "Systems designer / architect", "Make boundaries, dependencies, and invariants legible.",
             "Make boundaries, dependencies, failure modes, and durable invariants explicit before adding complexity."),
    RoleLens("builder", "Builder / maintainer", "Prefer small, testable increments and healthy maintenance.",
             "Prefer small, testable increments. Call out maintenance cost, verification, and the safest path to a useful result."),
    RoleLens("researcher", "Researcher / analyst", "Separate evidence, inference, uncertainty, and decisions.",
             "Separate source-backed evidence, inference, uncertainty, and decision criteria. Do not overstate confidence."),
    RoleLens("writer", "Writer / communicator", "Shape a clear argument for the stated audience and outcome.",
             "Shape a clear argument for the intended audience and outcome. Prefer precise language over decorative prose."),
)
_CHALLENGE_POLICIES = (
    ChallengePolicy("active", "Actively challenge", "Surface material tension with evidence and a smaller path.",
                    "Challenge material assumptions constructively. When there is real tension, use: Concern, Evidence, Implication, Smaller path. Do not manufacture disagreement or turn challenge into execution authority."),
    ChallengePolicy("balanced", "Balanced", "Challenge consequential assumptions without interrupting ordinary progress.",
                    "Surface consequential assumptions and tradeoffs when useful. Keep the challenge proportional and evidence-based."),
    ChallengePolicy("supportive", "Supportive", "Prioritize forward movement; challenge only clear risks or contradictions.",
                    "Prioritize forward movement. Raise only clear risks or contradictions, with concise evidence and an alternative."),
)
ROLES = {role.id: role for role in _ROLES}
CHALLENGE_POLICIES = {policy.id: policy for policy in _CHALLENGE_POLICIES}
DEFAULT_CHALLENGE_POLICY_ID = "active"


def _bounded_text(value: object, label: str, limit: int, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"Session orientation {label} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"Session orientation {label} must be text")
    clean = " ".join(value.split())
    if not clean:
        if required:
            raise ValueError(f"Session orientation {label} is required")
        return None
    if len(clean) > limit:
        raise ValueError(f"Session orientation {label} is limited to {limit} characters")
    return clean


@dataclass(frozen=True)
class SessionOrientation:
    """Owner-selected context that guides a single session's reasoning."""

    project_id: str | None = None
    objective: str | None = None
    role_lens_id: str | None = None
    challenge_policy_id: str = DEFAULT_CHALLENGE_POLICY_ID
    temporary_constraints: tuple[str, ...] = ()
    expected_result: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "objective": self.objective,
            "role_lens_id": self.role_lens_id,
            "challenge_policy_id": self.challenge_policy_id,
            "temporary_constraints": list(self.temporary_constraints),
            "expected_result": self.expected_result,
        }

    def public_view(self) -> dict[str, Any]:
        role = ROLES.get(self.role_lens_id) if self.role_lens_id else None
        policy = CHALLENGE_POLICIES[self.challenge_policy_id]
        return {
            "project_id": self.project_id,
            "objective": self.objective,
            "role_lens": asdict(role) if role else None,
            "challenge_policy": asdict(policy),
            "temporary_constraints": list(self.temporary_constraints),
            "expected_result": self.expected_result,
        }

    def evidence_view(self) -> dict[str, Any]:
        """Safe receipt projection: describe supplied orientation, never copy it."""
        return {
            "project_id": self.project_id,
            "role_lens_id": self.role_lens_id,
            "challenge_policy_id": self.challenge_policy_id,
            "has_objective": self.objective is not None,
            "temporary_constraint_count": len(self.temporary_constraints),
            "has_expected_result": self.expected_result is not None,
        }


def _validate_orientation(value: dict[str, object]) -> SessionOrientation:
    project_id = _bounded_text(value.get("project_id"), "project ID", _MAX_PROJECT_ID)
    role_lens_id = value.get("role_lens_id")
    if role_lens_id is not None and role_lens_id not in ROLES:
        raise ValueError("Session orientation role lens is not supported")
    challenge_policy_id = value.get("challenge_policy_id", DEFAULT_CHALLENGE_POLICY_ID)
    if challenge_policy_id not in CHALLENGE_POLICIES:
        raise ValueError("Session orientation challenge policy is not supported")
    raw_constraints = value.get("temporary_constraints", [])
    if not isinstance(raw_constraints, list):
        raise ValueError("Session orientation constraints must be a list")
    if len(raw_constraints) > _MAX_CONSTRAINTS:
        raise ValueError(f"Session orientation allows at most {_MAX_CONSTRAINTS} constraints")
    constraints: list[str] = []
    for raw_constraint in raw_constraints:
        clean = _bounded_text(raw_constraint, "constraint", _MAX_CONSTRAINT)
        if clean and clean not in constraints:
            constraints.append(clean)
    return SessionOrientation(
        project_id=project_id,
        objective=_bounded_text(value.get("objective"), "objective", _MAX_OBJECTIVE),
        role_lens_id=role_lens_id,
        challenge_policy_id=challenge_policy_id,
        temporary_constraints=tuple(constraints),
        expected_result=_bounded_text(value.get("expected_result"), "expected result", _MAX_EXPECTED_RESULT),
    )


def orientation_from_mapping(value: object) -> SessionOrientation:
    """Validate persisted orientation, failing closed to a safe empty one."""
    if not isinstance(value, dict):
        return SessionOrientation()
    try:
        return _validate_orientation(value)
    except ValueError:
        return SessionOrientation()


def get_orientation(metadata: dict[str, Any]) -> SessionOrientation:
    return orientation_from_mapping(metadata.get(ORIENTATION_METADATA_KEY))


def set_orientation(value: object) -> SessionOrientation:
    """Validate an API replacement payload; malformed input is rejected."""
    if not isinstance(value, dict):
        raise ValueError("Session orientation must be an object")
    return _validate_orientation(value)


def public_roles() -> list[dict[str, str]]:
    return [{"id": role.id, "label": role.label, "description": role.description} for role in _ROLES]


def public_challenge_policies() -> list[dict[str, str]]:
    return [{"id": policy.id, "label": policy.label, "description": policy.description} for policy in _CHALLENGE_POLICIES]
