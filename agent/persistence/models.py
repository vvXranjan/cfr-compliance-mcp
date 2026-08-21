"""Shared persistence/review data models.

These are the *boundary* models returned by persistence repositories for
analysis-history and human-review features. They intentionally reuse the
existing domain models (`EvidencePassage`, `ReviewAudit`) for the
immutable automated record, and add only the small, explicit review
surface that the history/review APIs genuinely need. No parallel
regulatory domain model is introduced.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from agent.models import EvidencePassage, ReviewAudit


class ReviewState(StrEnum):
    """Explicit review lifecycle states (see ``review_lifecycle``)."""

    NEEDS_REVIEW = "needs_review"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class AnalysisSummary(BaseModel):
    """A row in the analysis-history listing.

    Mirrors the summary fields of ``ReportRecord`` (no clause/contract
    body). Never carries secrets, prompts, or contract text.
    """

    analysis_id: str
    generated_at: str = ""
    contract_id: str | None = None
    total_clauses: int = 0
    compliant: int = 0
    non_compliant: int = 0
    needs_review: int = 0


class MetricsSummary(BaseModel):
    """Aggregate compliance metrics across all persisted analyses.

    Used by the dashboard overview. Every field is derived from real
    persisted data -- nothing is fabricated. ``review_pending`` is the
    number of review-queue items in the actionable ``needs_review``
    state (0 for backends without the review workflow).
    """

    total_analyses: int = 0
    total_clauses: int = 0
    compliant: int = 0
    non_compliant: int = 0
    needs_review: int = 0
    review_pending: int = 0


class ReviewEvent(BaseModel):
    """One immutable transition in a review record's audit history."""

    state: str
    reason: str = ""
    reviewer_identity: str = ""
    created_at: str = ""


class ReviewItem(BaseModel):
    """A row in the review queue listing (actionable items)."""

    analysis_id: str
    clause_id: str
    clause_title: str = ""
    original_status: str = ""
    verification_status: str = ""
    review_state: str
    version: int = 1
    reviewer_identity: str = ""
    decided_at: str = ""


class ReviewDetail(BaseModel):
    """Full review view for one clause.

    ``review_state`` is None when the clause is not part of the review
    workflow (e.g. an already-verified clause). The immutable automated
    record (original status, evidence, provenance, review audit) is
    preserved verbatim and is never altered by reviewer decisions.
    """

    analysis_id: str
    clause_id: str
    clause_title: str = ""
    original_status: str = ""
    verification_status: str = ""
    reason: str = ""
    confidence: float = 0.0
    memory_participated: bool = False
    evidence: list[EvidencePassage] = Field(default_factory=list)
    review_audit: ReviewAudit | None = None

    review_state: str | None = None
    version: int = 0
    reviewer_identity: str = ""
    decision_reason: str = ""
    decided_at: str = ""
    events: list[ReviewEvent] = Field(default_factory=list)


class ReviewDecisionRequest(BaseModel):
    """Minimal request body for a review decision.

    ``reviewer_identity`` is an UNAUTHENTICATED placeholder -- there is
    no authentication system in this milestone. Callers may pass a label
    for attribution, but it must never be treated as proof of identity.
    ``expected_version`` enables optimistic concurrency: the transition
    only succeeds when it matches the current record version, so two
    concurrent reviewers cannot silently overwrite each other.
    """

    target_state: ReviewState
    reason: str = Field(default="", max_length=2000)
    reviewer_identity: str = Field(default="", max_length=200)
    expected_version: int = Field(default=0, ge=0)
