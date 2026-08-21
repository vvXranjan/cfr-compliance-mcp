"""agent/persistence/inmemory_repository.py

Deterministic in-memory implementation of the persistence protocol used
for offline tests (no database required). It mirrors the semantics of
`PostgresRepository` -- including the review lifecycle and optimistic
concurrency -- so the offline suite exercises the same behavior without
PostgreSQL.

Not a production backend; it does not survive restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent.reporting import ClauseReport, ReportRecord

from .base import (
    AnalysisNotFoundError,
    ConcurrencyError,
    ReviewNotFoundError,
    ReviewStateError,
)
from .models import AnalysisSummary, MetricsSummary, ReviewDetail, ReviewEvent, ReviewItem
from .review_lifecycle import is_valid_state, validate_transition


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class InMemoryRepository:
    """Deterministic in-memory backend (tests only)."""

    backend = "inmemory"

    def __init__(self) -> None:
        self._analyses: dict[str, ReportRecord] = {}
        # (analysis_id, clause_id) -> review record dict
        self._reviews: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------
    # Report persistence
    # ------------------------------------------------------------------

    def save_report(
        self,
        record: ReportRecord,
        *,
        reports_dir: str | Path | None = None,
    ) -> str:
        self._analyses[record.analysis_id] = record
        for cr in record.clauses:
            if cr.verification_status == "needs_review" or cr.status == "Needs Review":
                key = (record.analysis_id, cr.clause_id)
                if key not in self._reviews:
                    self._reviews[key] = {
                        "state": "needs_review",
                        "version": 1,
                        "original_status": cr.status,
                        "reviewer_identity": "",
                        "decision_reason": "",
                        "decided_at": "",
                        "events": [],
                    }
        return record.analysis_id

    def get_analysis(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        return self.load_report(analysis_id)

    def load_report(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        record = self._analyses.get(analysis_id)
        if record is None:
            raise AnalysisNotFoundError(f"Analysis {analysis_id!r} not found")
        return record

    def list_analyses(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        reports_dir: str | Path | None = None,
    ) -> list[AnalysisSummary]:
        summaries = []
        for record in self._analyses.values():
            s = AnalysisSummary(
                analysis_id=record.analysis_id,
                generated_at=record.generated_at,
                contract_id=record.contract_id,
                total_clauses=record.total_clauses,
                compliant=record.compliant,
                non_compliant=record.non_compliant,
                needs_review=record.needs_review,
            )
            if status and getattr(s, status, 0) <= 0:
                continue
            summaries.append(s)
        summaries.sort(key=lambda s: s.generated_at, reverse=True)
        return summaries[offset : offset + limit]

    def count_analyses(
        self,
        *,
        status: str | None = None,
        reports_dir: str | Path | None = None,
    ) -> int:
        return len(self.list_analyses(status=status))

    def summarize(
        self,
        *,
        reports_dir: str | Path | None = None,
    ) -> MetricsSummary:
        metrics = MetricsSummary(review_pending=0)
        for record in self._analyses.values():
            metrics.total_analyses += 1
            metrics.total_clauses += record.total_clauses
            metrics.compliant += record.compliant
            metrics.non_compliant += record.non_compliant
            metrics.needs_review += record.needs_review
        metrics.review_pending = sum(
            1 for rec in self._reviews.values() if rec["state"] == "needs_review"
        )
        return metrics

    # ------------------------------------------------------------------
    # Human review
    # ------------------------------------------------------------------

    def list_review_queue(
        self,
        *,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        items: list[ReviewItem] = []
        for (analysis_id, clause_id), rec in self._reviews.items():
            if state and rec["state"] != state:
                continue
            record = self._analyses.get(analysis_id)
            cr = _find_clause(record, clause_id) if record else None
            items.append(
                ReviewItem(
                    analysis_id=analysis_id,
                    clause_id=clause_id,
                    clause_title=cr.clause_title if cr else "",
                    original_status=rec["original_status"],
                    verification_status=cr.verification_status if cr else "",
                    review_state=rec["state"],
                    version=rec["version"],
                    reviewer_identity=rec["reviewer_identity"],
                    decided_at=rec["decided_at"],
                )
            )
        items.sort(key=lambda i: i.analysis_id)
        return items[offset : offset + limit]

    def count_review_queue(self, *, state: str | None = None) -> int:
        return len(self.list_review_queue(state=state))

    def get_review(self, analysis_id: str, clause_id: str) -> ReviewDetail:
        record = self._analyses.get(analysis_id)
        if record is None:
            raise AnalysisNotFoundError(f"Analysis {analysis_id!r} not found")
        cr = _find_clause(record, clause_id)
        if cr is None:
            raise AnalysisNotFoundError(
                f"Clause {clause_id!r} not found in analysis {analysis_id!r}"
            )
        rec = self._reviews.get((analysis_id, clause_id))
        return _detail(analysis_id, cr, rec)

    def list_review_events(self, analysis_id: str, clause_id: str) -> list[ReviewEvent]:
        rec = self._reviews.get((analysis_id, clause_id))
        if rec is None:
            return []
        return list(rec["events"])

    def transition_review(
        self,
        analysis_id: str,
        clause_id: str,
        *,
        target_state: str,
        reason: str,
        reviewer_identity: str,
        expected_version: int,
    ) -> ReviewDetail:
        if not is_valid_state(target_state):
            raise ReviewStateError(f"Unknown review state {target_state!r}")

        record = self._analyses.get(analysis_id)
        if record is None:
            raise AnalysisNotFoundError(f"Analysis {analysis_id!r} not found")
        cr = _find_clause(record, clause_id)
        if cr is None:
            raise AnalysisNotFoundError(
                f"Clause {clause_id!r} not found in analysis {analysis_id!r}"
            )

        rec = self._reviews.get((analysis_id, clause_id))
        if rec is None:
            if cr.verification_status != "needs_review" and cr.status != "Needs Review":
                raise ReviewNotFoundError(
                    f"No review record for clause {clause_id!r}; the clause is not "
                    "in a review-required state."
                )
            current_state = "needs_review"
            current_version = 0
        else:
            current_state = rec["state"]
            current_version = rec["version"]

        if not validate_transition(current_state, target_state):
            raise ReviewStateError(
                f"Invalid review transition {current_state!r} -> {target_state!r}"
            )
        if expected_version != current_version:
            raise ConcurrencyError(
                f"Review version conflict: expected {expected_version}, "
                f"current {current_version}. Refresh and retry."
            )

        new_version = current_version + 1
        decided_at = _now_iso()
        if rec is None:
            rec = {
                "state": target_state,
                "version": new_version,
                "original_status": cr.status,
                "reviewer_identity": reviewer_identity,
                "decision_reason": reason,
                "decided_at": decided_at,
                "events": [],
            }
            self._reviews[(analysis_id, clause_id)] = rec
        else:
            rec.update(
                {
                    "state": target_state,
                    "version": new_version,
                    "reviewer_identity": reviewer_identity,
                    "decision_reason": reason,
                    "decided_at": decided_at,
                }
            )
        rec["events"].append(
            ReviewEvent(
                state=target_state,
                reason=reason,
                reviewer_identity=reviewer_identity,
                created_at=decided_at,
            )
        )
        return _detail(analysis_id, cr, rec)


def _find_clause(record: ReportRecord | None, clause_id: str) -> ClauseReport | None:
    if record is None:
        return None
    for cr in record.clauses:
        if cr.clause_id == clause_id:
            return cr
    return None


def _detail(analysis_id: str, cr: ClauseReport, rec: dict | None) -> ReviewDetail:
    review_state = None
    version = 0
    reviewer_identity = ""
    decision_reason = ""
    decided_at = ""
    events: list[ReviewEvent] = []
    if rec is not None:
        review_state = rec["state"]
        version = rec["version"]
        reviewer_identity = rec["reviewer_identity"]
        decision_reason = rec["decision_reason"]
        decided_at = rec["decided_at"]
        events = list(rec["events"])
    return ReviewDetail(
        analysis_id=analysis_id,
        clause_id=cr.clause_id,
        clause_title=cr.clause_title,
        original_status=cr.status,
        verification_status=cr.verification_status,
        reason=cr.reason,
        confidence=cr.confidence,
        memory_participated=cr.memory_participated,
        evidence=cr.evidence,
        review_audit=cr.review_audit,
        review_state=review_state,
        version=version,
        reviewer_identity=reviewer_identity,
        decision_reason=decision_reason,
        decided_at=decided_at,
        events=events,
    )
