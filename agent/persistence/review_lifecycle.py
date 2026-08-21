"""Human review lifecycle state machine (pure, deterministic).

The review workflow is an additional human decision layer on top of the
immutable automated results. A reviewer transition never overwrites the
original `ComplianceResult`, evidence, or `ReviewAudit` -- it only moves
the review record through an explicit, validated state machine and
appends an immutable audit event.

Allowed transitions are explicit and minimal:

    needs_review -> under_review
    under_review  -> approved | rejected | escalated

Anything else is an invalid transition and must be rejected (never
silently allowed).
"""

from __future__ import annotations

#: All valid review states.
REVIEW_STATES = frozenset(
    {"needs_review", "under_review", "approved", "rejected", "escalated"}
)

#: Explicit allowed transitions: current_state -> set(target states).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "needs_review": frozenset({"under_review"}),
    "under_review": frozenset({"approved", "rejected", "escalated"}),
}


def is_valid_state(state: str) -> bool:
    """True when ``state`` is a known review state."""
    return state in REVIEW_STATES


def validate_transition(current: str, target: str) -> bool:
    """True only when the current -> target transition is explicitly allowed.

    A state is not a valid transition target unless listed for its source.
    A transition to an identical state is rejected (e.g. a second ``approve``
    cannot be recorded).
    """
    if current not in ALLOWED_TRANSITIONS:
        return False
    return target in ALLOWED_TRANSITIONS[current]
