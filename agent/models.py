import hashlib
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _clause_id_from_text(title: str, text: str) -> str:
    """Generate a deterministic clause ID from its title and text."""
    h = hashlib.sha256(f"{title}|{text}".encode()).hexdigest()
    return h[:8]


@dataclass
class Clause:
    """A single contract clause identified by its section heading."""

    title: str
    text: str
    clause_id: str = field(
        default_factory=lambda: _clause_id_from_text(
            "", ""
        ),  # will be overridden when Clause is constructed with title/text
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "clause_id", _clause_id_from_text(self.title, self.text)
        )


class EvidencePassage(BaseModel):
    """A specific CFR text passage that influenced a compliance decision.

    Ensures every compliance conclusion is evidence-grounded: the exact
    regulation text (title, part, section, date, and clean text span)
    that was referenced is always traceable back from the result.

    Provenance fields (``source``, ``retrieved_at``, ``retrieval_method``,
    ``version``, ``confidence``) describe where the material came from,
    when it was retrieved, and which regulation version/effective date
    it represented. They are populated ONLY by the retrieval layer from
    authoritative API metadata -- never by the LLM, which is only ever
    allowed to describe *what* it read, not manufacture where it came
    from.
    """

    title: int
    part: str | None = None
    section: str | None = None
    date: str = ""  # regulation date; unknown until the retrieval layer supplies it
    text_span: str  # the clean text excerpt, truncated if very long
    citation: str  # human-readable citation like "40 CFR 257.3"
    source: str = ""  # where the material came from (e.g. "eCFR")
    retrieved_at: str = ""  # ISO-8601 UTC timestamp of retrieval
    retrieval_method: str = ""  # how it was retrieved (e.g. "ecfr_api")
    version: str = ""  # regulation/version identifier or effective date
    confidence: float | None = None  # provenance confidence, 0-1; None = not rated

    model_config = ConfigDict(frozen=True)


class ReviewAudit(BaseModel):
    """Audit record explaining why a finding was -- or was not --
    finalized automatically.

    This is the human-in-the-loop trail: a reviewer inspecting a
    ``Needs Review`` result can read exactly what the LLM/deterministic
    rules proposed, what the verifier concluded, and why the system
    refused to finalize it. Populated by the compliance pipeline; the
    LLM never writes into it.
    """

    final_status: str = ""
    proposed_status: str = ""
    proposed_confidence: float = 0.0
    review_reason: str = ""
    verifier_recommendation: str = ""  # accept / review / reject / not_run
    verifier_notes: str = ""
    deterministic_status: str = ""
    evidence_citations: list[str] = Field(default_factory=list)
    reviewed_at: str = ""  # ISO-8601 UTC timestamp


class ComplianceResult(BaseModel):
    """
    Structured output returned by the compliance system, evidence-grounded
    and verified.

    Unlike the original Agno-agent-only output, this model always includes
    an `evidence` list so a human (or automated auditor) can trace exactly
    which CFR passages triggered each decision.

    `verification_status` is the explicit human-in-the-loop boundary:
        - "verified"     -- passed verification (or a deterministic rule
                           decision); safe to act on.
        - "needs_review" -- a verifier conflict, insufficient evidence,
                           security concern, or failure routed the finding
                           to human review.
        - "not_verified" -- no verification step ran (informational only).
    `review_audit` records why, when a finding is not auto-finalized.
    """

    clause_title: str
    clause_id: str

    status: Literal["Compliant", "Non-Compliant", "Needs Review"]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1",
    )

    reason: str

    evidence: list[EvidencePassage] = Field(
        default_factory=list,
        description="CFR passages that influenced the decision, with citations and text spans.",
    )

    verification_status: Literal["verified", "needs_review", "not_verified"] = (
        "not_verified"
    )

    review_reason: str = Field(
        default="",
        description="Human-readable explanation of why review is required (empty when verified).",
    )

    review_audit: ReviewAudit | None = Field(
        default=None,
        description="Audit trail of the decision, populated by the pipeline.",
    )

    memory_participated: bool = Field(
        default=False,
        description=(
            "True when historical Compliance Memory influenced this "
            "evaluation (exact-match reuse or supplied historical "
            "context). Memory is advisory context only, never a source "
            "of regulatory truth; memory-assisted results are never "
            "auto-indexed back into memory (feedback-loop prevention)."
        ),
    )