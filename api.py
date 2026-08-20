"""FastAPI interface for the cfr-compliance-mcp compliance engineering system.

Exposes REST endpoints for clause compliance evaluation with the full
pipeline: security checks + deterministic rules + LLM agent + verification
+ evidence tracking.

The system is designed to be production-oriented with clear human-review
boundaries and auditable outputs.

Endpoints:
    GET  /health           health/readiness check
    POST /evaluate-clause  evaluate a single clause
    POST /evaluate-bulk    evaluate a batch of clauses

Request/response models reuse the domain models from ``agent.models``
where practical -- ``Clause``, ``ComplianceResult`` and
``EvidencePassage`` are the single source of truth; this module's own
Pydantic models are thin HTTP views over them.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace as trace_api
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

try:
    from opentelemetry.exporter.jaeger.thrift import JaegerSpanExporter
    _JAEGER_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on installed exporter version
    _JAEGER_AVAILABLE = False

from agent.deterministic_rules import evaluate_deterministic
from agent.models import Clause, ComplianceResult, EvidencePassage, ReviewAudit
from agent.reporting import build_report, save_report
from agent.security import (
    check_clause_security,
    sanitize_cfr_text,
    sanitize_clause_text,
    validate_cfr_title,
)
from agent.verification_agent import verify_compliance
from cfr_compliance_mcp.logging_config import configure_logging

# Honor LOG_LEVEL / LOG_FORMAT from Settings (shared with the MCP server).
configure_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="cfr-compliance-mcp API",
    description=(
        "Production-oriented AI compliance engineering platform - "
        "evidence-grounded, auditable, hybrid RAG CFR compliance checking"
    ),
    version="0.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS middleware - restrict origins in production via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Whether the LLM agent is available for the LLM-fallback step. The
# agent itself reads ATM_API_KEY/OPENAI_API_KEY; this flag only controls
# whether the API attempts an LLM call when deterministic rules are
# inconclusive. LLM recommendation is never treated as authorization --
# see the human-review boundary documented on each endpoint.
LLM_AVAILABLE = os.getenv("ATM_API_KEY") is not None or os.getenv("OPENAI_API_KEY") is not None

# When set, /evaluate-bulk persists an auditable JSON report to this
# directory (typically the repository's reports/). Opt-in: unset means
# no persistence. Persistence failures never fail the request.
REPORTS_DIR = os.getenv("CFR_REPORTS_DIR")

# ---------------------------------------------------------------------------
# OpenTelemetry tracing setup
# ---------------------------------------------------------------------------
# Best-effort: if the Jaeger exporter is unavailable or misconfigured,
# the app must still serve requests. No secrets, API keys or full
# contract text are ever attached to spans.
trace_provider = TracerProvider(
    resource=Resource.create({"service.name": "cfr-compliance-mcp"})
)
trace_api.set_tracer_provider(trace_provider)

if _JAEGER_AVAILABLE:
    try:
        jaeger_exporter = JaegerSpanExporter(
            agent_host_name=os.getenv("JAEGER_AGENT_HOST", "localhost"),
            agent_port=int(os.getenv("JAEGER_AGENT_PORT", "6831")),
        )
        trace_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
    except Exception:  # pragma: no cover - exporter env/version issues
        logger.warning("Jaeger exporter unavailable; traces will not be exported", exc_info=True)

FastAPIInstrumentor().instrument_app(app)


# ---------------------------------------------------------------------------
# Structured, safe error handling
# ---------------------------------------------------------------------------
# Error responses never expose stack traces, exception strings, secrets or
# contract content. Field-level validation context is returned in a
# structured form; internal details are only logged server-side.


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Any, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {
            "loc": [str(p) for p in (e.get("loc") or [])],
            "msg": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=APIError(
            error="validation_error",
            message="Request validation failed",
            details=errors,
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Any, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=500,
        content=APIError(
            error="internal_error",
            message="An unexpected internal error occurred",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class ClauseInput(BaseModel):
    """Input contract clause for compliance evaluation.

    ``clause_title``/``clause_text`` are the authoritative contract
    clause fields. ``title``/``text`` are accepted as aliases for
    backward compatibility. ``cfr_title`` (1-50), ``cfr_citation`` and
    ``cfr_text`` optionally scope the evaluation to a specific
    regulation; when ``cfr_text`` is absent the evaluation cannot be
    evidence-grounded and resolves to "Needs Review".
    """

    model_config = ConfigDict(populate_by_name=True)

    clause_title: str = Field(
        ...,
        max_length=500,
        description="Heading/title of the contract clause.",
        validation_alias=AliasChoices("clause_title", "title"),
    )
    clause_text: str = Field(
        ...,
        max_length=500_000,
        description="Full text of the contract clause.",
        validation_alias=AliasChoices("clause_text", "text"),
    )
    cfr_title: int | None = Field(
        default=None, ge=1, le=50, description="Optional CFR title number (1-50)."
    )
    cfr_citation: str | None = Field(
        default=None,
        max_length=200,
        description="Optional CFR citation, e.g. '40 CFR 257.3'.",
    )
    cfr_text: str | None = Field(
        default=None,
        max_length=2_000_000,
        description="Optional CFR regulation text; required for an evidence-grounded verdict.",
    )

    @field_validator("clause_text")
    @classmethod
    def _text_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("clause_text must not be empty")
        return v

    @field_validator("clause_title")
    @classmethod
    def _title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("clause_title must not be empty")
        return v


class EvidencePassageOut(BaseModel):
    """Output model for an evidence passage (a view over EvidencePassage)."""

    title: int
    citation: str
    text_span: str
    part: str | None = None
    section: str | None = None
    date: str | None = None


class ReviewAuditOut(BaseModel):
    """Output view over the domain ReviewAudit (safe, non-sensitive)."""

    final_status: str = ""
    proposed_status: str = ""
    proposed_confidence: float = 0.0
    review_reason: str = ""
    verifier_recommendation: str = ""
    verifier_notes: str = ""
    deterministic_status: str = ""
    evidence_citations: list[str] = Field(default_factory=list)
    reviewed_at: str = ""


class ComplianceResponse(BaseModel):
    """API response for a single clause compliance evaluation."""

    clause_title: str
    clause_id: str
    status: str = Field(..., description="Compliant / Non-Compliant / Needs Review")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    reason: str
    evidence: list[EvidencePassageOut] = Field(
        default_factory=list,
        description="CFR passages that influenced the decision, with citations.",
    )
    verified: bool = Field(
        default=False,
        description="Whether the result passed verification.",
    )
    verification_notes: str = Field(
        default="",
        description="Verification agent notes or conflict summary.",
    )
    verification_status: str = Field(
        default="not_verified",
        description="verified / needs_review / not_verified.",
    )
    review_audit: ReviewAuditOut | None = Field(
        default=None,
        description="Audit trail explaining the decision, incl. why review is required.",
    )


class BulkClauseInput(BaseModel):
    """Input for bulk compliance evaluation."""

    clauses: list[ClauseInput] = Field(
        default_factory=list,
        max_length=200,
        description="List of contract clauses to evaluate (max 200).",
    )


class APIError(BaseModel):
    """Minimal, structured, safe error representation.

    Never carries stack traces, exception strings, secrets or raw
    contract content. ``details`` is optional structured context
    (e.g. field-level validation errors).
    """

    error: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error summary.")
    details: list[dict[str, Any]] | None = Field(
        default=None, description="Optional structured error context."
    )


class BulkComplianceResponse(BaseModel):
    """API response for bulk compliance evaluation."""

    total_clauses: int
    compliant: int
    non_compliant: int
    needs_review: int
    results: list[ComplianceResponse]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _clause_from_input(clause_input: ClauseInput) -> Clause:
    """Build the internal Clause dataclass from a ClauseInput.

    The CFR title hint is derived from ``cfr_title``, falling back to
    ``clause_title`` when that is a plain number (the legacy API shape
    that used ``title`` as the CFR title).
    """
    title = validate_cfr_title(clause_input.cfr_title)
    if title is None and clause_input.clause_title.strip().isdigit():
        title = validate_cfr_title(clause_input.clause_title)
    return Clause(title=clause_input.clause_title, text=clause_input.clause_text)


def _evidence_to_out(ev: EvidencePassage | dict[str, Any]) -> EvidencePassageOut:
    """Convert an EvidencePassage (or dict) to the output model."""
    if isinstance(ev, EvidencePassage):
        return EvidencePassageOut(
            title=ev.title,
            citation=ev.citation,
            text_span=ev.text_span,
            part=ev.part,
            section=ev.section,
            date=ev.date,
        )
    return EvidencePassageOut(
        title=int(ev.get("title", 0)),
        citation=str(ev.get("citation", "")),
        text_span=str(ev.get("text_span", "")),
        part=ev.get("part"),
        section=ev.get("section"),
        date=ev.get("date"),
    )


def _security_scan(  # noqa: E501
    clause: Clause, *, title: int | None = None, cfr_text: str | None = None
) -> tuple[bool, str]:
    """Run the shared security gates on an untrusted clause.

    Uses `check_clause_security` -- the same gate-check function the CLI
    pipeline uses -- so the FastAPI path and the canonical evaluation
    path cannot drift. Returns (safe, details). A suspicious clause is
    rejected before any deterministic/LLM evaluation runs --
    defense-in-depth, fail-safe.
    """
    gate = check_clause_security(clause, title=title, cfr_text=cfr_text)
    if not gate["safe"]:
        return False, gate["reason"]

    sanitized = sanitize_clause_text(clause.text)
    if sanitized != clause.text:
        # Sanitization stripped/truncated content; continue with the
        # sanitized text but flag it in the audit trail.
        clause.text = sanitized

    return True, "Security checks passed"


def _api_needs_review(
    clause: Clause,
    reason: str,
    *,
    proposed: ComplianceResult | None = None,
    verification=None,
    deterministic_status: str = "",
) -> ComplianceResult:
    """Build a Needs Review result for the API path with the full audit trail."""
    citations = [ev.citation for ev in (proposed.evidence if proposed else []) if ev.citation]

    audit = ReviewAudit(
        final_status="Needs Review",
        proposed_status=proposed.status if proposed else "",
        proposed_confidence=proposed.confidence if proposed else 0.0,
        review_reason=reason,
        verifier_recommendation=(
            verification.recommendation if verification is not None else "not_run"
        ),
        verifier_notes=verification.notes if verification is not None else "",
        deterministic_status=deterministic_status,
        evidence_citations=citations,
        reviewed_at=datetime.now(UTC).isoformat(),
    )
    return ComplianceResult(
        clause_title=clause.title,
        clause_id=clause.clause_id,
        status="Needs Review",
        confidence=0.0,
        reason=reason,
        evidence=proposed.evidence if proposed else [],
        verification_status="needs_review",
        review_reason=reason,
        review_audit=audit,
    )


def _evaluate_clause(
    clause: Clause,
    *,
    cfr_text: str,
    cfr_citation: str,
    cfr_title: int | None,
) -> ComplianceResult:
    """Evaluate one clause through the full pipeline.

    Order (deterministic where possible, LLM only as fallback):
      1. Deterministic rule-based checking (LLM-free, fast, auditable).
      2. If inconclusive, LLM compliance agent evaluation (only when the
         caller supplied regulation text and an LLM is configured).
      3. Verification agent cross-check of the LLM decision.
      4. Conflict/uncertainty routes to "Needs Review" (human review).

    Raises:
        ValueError: if called with no regulation text (caller should
            return a Needs Review result instead of calling this).
    """
    if not cfr_text.strip():
        return _api_needs_review(
            clause,
            (
                "No CFR regulation text was supplied, so no evidence-grounded "
                "verdict is possible. Provide cfr_text or run the full "
                "retrieval pipeline."
            ),
        )

    cfr_text = sanitize_cfr_text(cfr_text)

    # --- Step 1: Deterministic rules (LLM-free) ---
    det_result = evaluate_deterministic(
        clause=clause,
        cfr_text=cfr_text,
        title=cfr_title or 0,
    )
    if det_result.status != "Needs Review":
        return det_result.model_copy(
            update={
                "verification_status": "verified",
                "review_audit": ReviewAudit(
                    final_status=det_result.status,
                    proposed_status=det_result.status,
                    proposed_confidence=det_result.confidence,
                    verifier_recommendation="not_run",
                    deterministic_status=det_result.status,
                    evidence_citations=[
                        ev.citation for ev in det_result.evidence if ev.citation
                    ],
                    reviewed_at=datetime.now(UTC).isoformat(),
                ),
            }
        )

    # --- Step 2: LLM fallback (if available) ---
    if not LLM_AVAILABLE:
        return _api_needs_review(
            clause,
            (
                "Deterministic rules were inconclusive and no LLM is "
                "configured (set ATM_API_KEY or OPENAI_API_KEY). Human "
                "review required."
            ),
            deterministic_status=det_result.status,
        )

    from agent.compliance_agent import evaluate_compliance

    llm_result = evaluate_compliance(
        clause_title=clause.title,
        clause_text=clause.text,
        cfr_citation=cfr_citation,
        cfr_text=cfr_text,
    )

    # --- Step 3: Verification cross-check ---
    verification = verify_compliance(
        result=llm_result,
        clause=clause,
        cfr_text=cfr_text,
        title=cfr_title or 0,
        version_payload=None,
    )

    if verification.recommendation in ("reject", "review"):
        return _api_needs_review(
            clause,
            f"{llm_result.reason} | Verification: {verification.notes}",
            proposed=llm_result,
            verification=verification,
            deterministic_status=det_result.status,
        )

    return llm_result.model_copy(
        update={
            "verification_status": "verified",
            "review_reason": "",
            "review_audit": ReviewAudit(
                final_status=llm_result.status,
                proposed_status=llm_result.status,
                proposed_confidence=llm_result.confidence,
                verifier_recommendation=verification.recommendation,
                verifier_notes=verification.notes,
                deterministic_status=det_result.status,
                evidence_citations=[
                    ev.citation for ev in llm_result.evidence if ev.citation
                ],
                reviewed_at=datetime.now(UTC).isoformat(),
            ),
        }
    )


def _response_from_result(
    result: ComplianceResult,
    *,
    verified: bool,
    verification_notes: str,
) -> ComplianceResponse:
    audit_out: ReviewAuditOut | None = None
    if result.review_audit is not None:
        audit = result.review_audit
        audit_out = ReviewAuditOut(
            final_status=audit.final_status,
            proposed_status=audit.proposed_status,
            proposed_confidence=audit.proposed_confidence,
            review_reason=audit.review_reason,
            verifier_recommendation=audit.verifier_recommendation,
            verifier_notes=audit.verifier_notes,
            deterministic_status=audit.deterministic_status,
            evidence_citations=audit.evidence_citations,
            reviewed_at=audit.reviewed_at,
        )
    return ComplianceResponse(
        clause_title=result.clause_title,
        clause_id=result.clause_id,
        status=result.status,
        confidence=result.confidence,
        reason=result.reason,
        evidence=[_evidence_to_out(ev) for ev in result.evidence],
        verified=verified,
        verification_notes=verification_notes or (audit_out.verifier_notes if audit_out else ""),
        verification_status=result.verification_status,
        review_audit=audit_out,
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Health/readiness check."""
    return {
        "status": "healthy",
        "service": "cfr-compliance-mcp",
        "llm_available": str(LLM_AVAILABLE),
    }


@app.post("/evaluate-clause", response_model=ComplianceResponse)
async def evaluate_single_clause(clause_input: ClauseInput) -> ComplianceResponse:
    """Evaluate a single contract clause against CFR regulations.

    Pipeline: security checks -> deterministic rules (LLM-free) -> LLM
    agent (fallback) -> verification agent -> human-review routing.

    Human-review boundary: an LLM or deterministic verdict is a
    recommendation, never authorization. Any verification conflict or
    insufficient evidence resolves to "Needs Review" (confidence 0.0),
    which callers must route to a human before acting on it.
    """
    clause = _clause_from_input(clause_input)

    cfr_title = validate_cfr_title(clause_input.cfr_title)
    if cfr_title is None and clause_input.clause_title.strip().isdigit():
        cfr_title = validate_cfr_title(clause_input.clause_title)

    safe, security_details = _security_scan(
        clause, title=cfr_title, cfr_text=clause_input.cfr_text
    )
    if not safe:
        raise HTTPException(
            status_code=400,
            detail=f"Clause rejected by security scan: {security_details}",
        )

    try:
        result = _evaluate_clause(
            clause,
            cfr_text=clause_input.cfr_text or "",
            cfr_citation=clause_input.cfr_citation or f"{cfr_title or 0} CFR",
            cfr_title=cfr_title,
        )
    except Exception:
        logger.exception("Failed to evaluate clause via API")
        result = _api_needs_review(
            clause,
            "Evaluation failed due to an internal error; manual review required.",
        )

    return _response_from_result(
        result,
        verified=result.verification_status == "verified",
        verification_notes="",
    )


@app.post("/evaluate-bulk", response_model=BulkComplianceResponse)
async def evaluate_bulk_clauses(
    bulk_input: list[ClauseInput] | BulkClauseInput,
) -> BulkComplianceResponse:
    """Evaluate multiple contract clauses against CFR regulations.

    Accepts either a JSON array of clauses or an object of the form
    ``{"clauses": [...]}``. Same per-clause pipeline as
    /evaluate-clause. Returns a summary plus per-clause results with
    evidence and verification status.
    """
    clauses = bulk_input if isinstance(bulk_input, list) else bulk_input.clauses
    if not clauses:
        raise HTTPException(status_code=400, detail="At least one clause must be provided")

    results: list[ComplianceResponse] = []
    domain_results: list[ComplianceResult] = []
    for clause_input in clauses:
        clause = _clause_from_input(clause_input)

        cfr_title = validate_cfr_title(clause_input.cfr_title)
        if cfr_title is None and clause_input.clause_title.strip().isdigit():
            cfr_title = validate_cfr_title(clause_input.clause_title)

        safe, security_details = _security_scan(
            clause, title=cfr_title, cfr_text=clause_input.cfr_text
        )
        if not safe:
            result = _api_needs_review(
                clause,
                f"Clause rejected by security scan: {security_details}",
            )
            domain_results.append(result)
            results.append(
                _response_from_result(
                    result,
                    verified=False,
                    verification_notes="Security scan failed",
                )
            )
            continue

        try:
            result = _evaluate_clause(
                clause,
                cfr_text=clause_input.cfr_text or "",
                cfr_citation=clause_input.cfr_citation or f"{cfr_title or 0} CFR",
                cfr_title=cfr_title,
            )
        except Exception:
            logger.exception("Failed to evaluate clause %r in bulk", clause.title)
            result = _api_needs_review(
                clause,
                "Evaluation failed due to an internal error; manual review required.",
                deterministic_status="evaluation_error",
            )

        domain_results.append(result)
        results.append(
            _response_from_result(
                result,
                verified=result.verification_status == "verified",
                verification_notes="",
            )
        )

    compliant = sum(1 for r in results if r.status == "Compliant")
    non_compliant = sum(1 for r in results if r.status == "Non-Compliant")
    needs_review = sum(1 for r in results if r.status == "Needs Review")

    response = BulkComplianceResponse(
        total_clauses=len(results),
        compliant=compliant,
        non_compliant=non_compliant,
        needs_review=needs_review,
        results=results,
    )

    if REPORTS_DIR:
        try:
            _persist_bulk_report(clauses, domain_results)
        except Exception:
            logger.exception("Failed to persist bulk compliance report")

    return response


def _persist_bulk_report(
    clauses: list[ClauseInput],
    domain_results: list[ComplianceResult],
) -> None:
    """Persist an auditable JSON report for a bulk evaluation.

    Enabled via ``CFR_REPORTS_DIR``. The report preserves the full
    decision trail (statuses, evidence with retrieval + version
    metadata, review reasons and the complete review audit) -- never the
    full contract body or secrets (see ``agent.reporting``).
    """
    from agent.reporting import new_analysis_id

    clause_models = [Clause(title=i.clause_title, text=i.clause_text) for i in clauses]
    record = build_report(
        clause_models,
        domain_results,
        analysis_id=new_analysis_id(),
    )
    path = save_report(record, reports_dir=REPORTS_DIR)
    logger.info("Persisted bulk compliance report to %s", path)