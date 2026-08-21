"""Report persistence for compliance analyses.

Persists auditable, machine-readable compliance reports to the
repository's ``reports/`` directory as structured JSON.

The report preserves the full decision trail for a human or automated
auditor:

  * analysis identifier
  * contract identifier (when available)
  * per-clause: clause id/title, final status, confidence, reason,
    evidence (with citations + retrieval + version metadata),
    verification status, review reason, and the full review audit
  * summary counts and timestamps

Security/safety properties:

  * deterministic, sanitized filenames -- never raw user input, no path
    traversal (verified against the reports directory after resolution)
  * atomic writes (temp file + ``os.replace``) so a crash or a repeated
    analysis can never corrupt an existing report
  * no secrets and no full sensitive contract text are stored -- the
    report carries clause identifiers, headings, statuses, reasons and
    (public) CFR evidence passages, not the contract body.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ComplianceResult, EvidencePassage, ReviewAudit

logger = logging.getLogger(__name__)

#: Default reports directory: <repo-root>/reports
DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

#: Characters allowed in report filenames.
_SAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9._-]+")


class ClauseReport(BaseModel):
    """Per-clause persisted view of a compliance result."""

    clause_id: str
    clause_title: str
    status: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[EvidencePassage] = Field(default_factory=list)
    verification_status: str = "not_verified"
    review_reason: str = ""
    review_audit: ReviewAudit | None = None
    memory_participated: bool = Field(
        default=False,
        description=(
            "True when historical Compliance Memory influenced this clause's "
            "evaluation. Advisory context only; never regulatory truth."
        ),
    )


class ReportRecord(BaseModel):
    """A complete, persisted compliance analysis report."""

    analysis_id: str
    contract_id: str | None = None
    generated_at: str = ""
    total_clauses: int = 0
    compliant: int = 0
    non_compliant: int = 0
    needs_review: int = 0
    clauses: list[ClauseReport] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def new_analysis_id(prefix: str = "analysis") -> str:
    """Generate a unique, filesystem-safe analysis identifier."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    token = secrets.token_hex(4)
    return f"{prefix}-{stamp}-{token}"


def sanitize_identifier(identifier: str | None) -> str:
    """Sanitize a raw identifier into a safe filename fragment.

    Strips path separators, traversal sequences and control characters.
    Never returns an empty string (callers can safely build a path).
    """
    if not identifier:
        return "analysis"
    cleaned = _SAFE_IDENTIFIER.sub("_", identifier.strip())
    cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.strip("._-")
    return cleaned or "analysis"


def _build_clause_report(result: ComplianceResult) -> ClauseReport:
    return ClauseReport(
        clause_id=result.clause_id,
        clause_title=result.clause_title,
        status=result.status,
        confidence=result.confidence,
        reason=result.reason,
        evidence=result.evidence,
        verification_status=result.verification_status,
        review_reason=result.review_reason,
        review_audit=result.review_audit,
        memory_participated=result.memory_participated,
    )


def build_report(
    clauses: list[Any],
    results: list[ComplianceResult],
    *,
    analysis_id: str | None = None,
    contract_id: str | None = None,
) -> ReportRecord:
    """Assemble a ReportRecord from pipeline inputs/outputs.

    ``clauses`` may be the original ``Clause`` objects or ``None``;
    results alone carry everything the report needs.
    """
    if not results:
        return ReportRecord(
            analysis_id=sanitize_identifier(analysis_id) or new_analysis_id(),
            contract_id=contract_id,
            generated_at=datetime.now(UTC).isoformat(),
        )

    clause_reports: list[ClauseReport] = []
    for result in results:
        clause_reports.append(_build_clause_report(result))

    compliant = sum(1 for r in results if r.status == "Compliant")
    non_compliant = sum(1 for r in results if r.status == "Non-Compliant")
    needs_review = sum(1 for r in results if r.status == "Needs Review")

    return ReportRecord(
        analysis_id=sanitize_identifier(analysis_id) or new_analysis_id(),
        contract_id=contract_id,
        generated_at=datetime.now(UTC).isoformat(),
        total_clauses=len(results),
        compliant=compliant,
        non_compliant=non_compliant,
        needs_review=needs_review,
        clauses=clause_reports,
    )


def resolve_report_path(
    analysis_id: str,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> Path:
    """Resolve (and defend) the target path for a report.

    The analysis id is sanitized before it is joined, and the final path
    is verified to sit inside ``reports_dir`` -- defense in depth against
    path traversal via a crafted identifier.
    """
    base = Path(reports_dir).expanduser().resolve()
    safe = sanitize_identifier(analysis_id)
    target = (base / f"{safe}.json").resolve()

    try:
        common = os.path.commonpath([str(base), str(target)])
    except ValueError:
        common = ""
    if common != str(base):
        raise ValueError(f"Refusing unsafe report path: {target}")

    return target


def save_report(
    record: ReportRecord | dict[str, Any],
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> Path:
    """Write a report as atomic JSON under ``reports_dir``.

    Returns the path written. Overwriting an existing report with the
    SAME analysis_id is intentional (re-running an analysis); it is done
    atomically so a previous report can never be left half-written or
    corrupted. Distinct analysis ids never overwrite one another.
    """
    if isinstance(record, dict):
        record = ReportRecord.model_validate(record)
    elif not isinstance(record, ReportRecord):
        raise TypeError(f"record must be ReportRecord or dict, got {type(record).__name__}")

    target = resolve_report_path(record.analysis_id, reports_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = record.model_dump_json(indent=2)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)

    logger.info("Persisted compliance report %s -> %s", record.analysis_id, target)
    return target


def load_report(
    analysis_id: str,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> ReportRecord:
    """Load a previously persisted report by analysis id."""
    target = resolve_report_path(analysis_id, reports_dir)
    return ReportRecord.model_validate(json.loads(target.read_text(encoding="utf-8")))