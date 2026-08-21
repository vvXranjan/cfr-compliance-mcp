"""P6.2 review lifecycle state-machine tests (pure, deterministic).

Cover the explicit allowed-transition rules that both the PostgreSQL and
in-memory backends share. No database or external services required.
"""

from __future__ import annotations

from agent.persistence.review_lifecycle import (
    ALLOWED_TRANSITIONS,
    REVIEW_STATES,
    is_valid_state,
    validate_transition,
)


class TestValidTransitions:
    def test_claim_moves_needs_review_to_under_review(self) -> None:
        assert validate_transition("needs_review", "under_review") is True

    def test_under_review_can_approve(self) -> None:
        assert validate_transition("under_review", "approved") is True

    def test_under_review_can_reject(self) -> None:
        assert validate_transition("under_review", "rejected") is True

    def test_under_review_can_escalate(self) -> None:
        assert validate_transition("under_review", "escalated") is True


class TestInvalidTransitions:
    def test_cannot_skip_claim(self) -> None:
        assert validate_transition("needs_review", "approved") is False
        assert validate_transition("needs_review", "rejected") is False
        assert validate_transition("needs_review", "escalated") is False

    def test_cannot_transition_into_needs_review(self) -> None:
        assert validate_transition("under_review", "needs_review") is False

    def test_cannot_self_transition(self) -> None:
        assert validate_transition("under_review", "under_review") is False
        assert validate_transition("approved", "approved") is False

    def test_terminal_states_have_no_outgoing(self) -> None:
        assert validate_transition("approved", "under_review") is False
        assert validate_transition("rejected", "under_review") is False
        assert validate_transition("escalated", "under_review") is False

    def test_unknown_state_rejected(self) -> None:
        assert validate_transition("nonsense", "approved") is False
        assert validate_transition("needs_review", "nonsense") is False


class TestStates:
    def test_all_states_known(self) -> None:
        assert REVIEW_STATES == {
            "needs_review",
            "under_review",
            "approved",
            "rejected",
            "escalated",
        }

    def test_is_valid_state(self) -> None:
        assert is_valid_state("approved") is True
        assert is_valid_state("bogus") is False

    def test_every_source_state_has_explicit_targets(self) -> None:
        # Every transition target must itself be a known state.
        for targets in ALLOWED_TRANSITIONS.values():
            for target in targets:
                assert target in REVIEW_STATES
