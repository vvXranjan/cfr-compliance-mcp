"""agent/persistence/file_repository.py

Filesystem persistence backend.

`FileRepository` is a thin, faithful delegate over the existing
`agent.reporting` implementation -- it does NOT duplicate the write
logic. Every safety property of `agent.reporting` is preserved because
`save_report`/`load_report` are the same functions the API already used:

  * atomic writes (temp file + ``os.replace``)
  * sanitized identifiers
  * path-traversal protection
  * no secrets / no full contract text (unchanged report format)

Analysis-history listing scans the reports directory and re-parses the
JSON snapshots -- fine for modest on-disk history.

The filesystem backend is NOT relational: review-queue queries and
review transitions require PostgreSQL, so the review methods raise
`ReviewNotSupportedError` rather than pretending to implement a query
layer over flat files.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.reporting import (
    DEFAULT_REPORTS_DIR,
    ReportRecord,
)
from agent.reporting import (
    load_report as _load_report,
)
from agent.reporting import (
    save_report as _save_report,
)

from .base import ReviewNotSupportedError
from .models import AnalysisSummary, MetricsSummary


class FileRepository:
    """Filesystem backend implementing the report/persistence protocol."""

    backend = "file"

    def __init__(
        self,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    ) -> None:
        self.reports_dir = Path(reports_dir)

    # ------------------------------------------------------------------
    # Report persistence
    # ------------------------------------------------------------------

    def save_report(
        self,
        record: ReportRecord,
        *,
        reports_dir: str | Path | None = None,
    ) -> Path:
        """Persist a report via ``agent.reporting.save_report``."""
        return _save_report(record, reports_dir=reports_dir or self.reports_dir)

    def load_report(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        """Load a report via ``agent.reporting.load_report``."""
        return _load_report(analysis_id, reports_dir=reports_dir or self.reports_dir)

    def get_analysis(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        return self.load_report(analysis_id, reports_dir=reports_dir)

    def list_analyses(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        reports_dir: str | Path | None = None,
    ) -> list[AnalysisSummary]:
        """Scan the reports directory for analysis snapshots.

        ``status`` filters on the summary count columns:
        ``compliant`` / ``non_compliant`` / ``needs_review`` (> 0).
        """
        base = Path(reports_dir) if reports_dir else self.reports_dir
        summaries: list[AnalysisSummary] = []
        if not base.is_dir():
            return summaries

        files = sorted(base.glob("*.json"), key=lambda p: p.name, reverse=True)
        for path in files:
            try:
                record = ReportRecord.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            summary = AnalysisSummary(
                analysis_id=record.analysis_id,
                generated_at=record.generated_at,
                contract_id=record.contract_id,
                total_clauses=record.total_clauses,
                compliant=record.compliant,
                non_compliant=record.non_compliant,
                needs_review=record.needs_review,
            )
            if status and getattr(summary, status, 0) <= 0:
                continue
            summaries.append(summary)

        return summaries[offset : offset + limit]

    def count_analyses(
        self,
        *,
        status: str | None = None,
        reports_dir: str | Path | None = None,
    ) -> int:
        base = Path(reports_dir) if reports_dir else self.reports_dir
        count = 0
        if not base.is_dir():
            return 0
        for path in base.glob("*.json"):
            try:
                record = ReportRecord.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            summary = AnalysisSummary(
                analysis_id=record.analysis_id,
                generated_at=record.generated_at,
                contract_id=record.contract_id,
                total_clauses=record.total_clauses,
                compliant=record.compliant,
                non_compliant=record.non_compliant,
                needs_review=record.needs_review,
            )
            if status and getattr(summary, status, 0) <= 0:
                continue
            count += 1
        return count

    def summarize(
        self,
        *,
        reports_dir: str | Path | None = None,
    ) -> MetricsSummary:
        """Aggregate metrics by scanning the reports directory."""
        base = Path(reports_dir) if reports_dir else self.reports_dir
        metrics = MetricsSummary(review_pending=0)
        if not base.is_dir():
            return metrics
        for path in base.glob("*.json"):
            try:
                record = ReportRecord.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            metrics.total_analyses += 1
            metrics.total_clauses += record.total_clauses
            metrics.compliant += record.compliant
            metrics.non_compliant += record.non_compliant
            metrics.needs_review += record.needs_review
        return metrics

    # ------------------------------------------------------------------
    # Human review (not supported by the filesystem backend)
    # ------------------------------------------------------------------

    def list_review_queue(self, *, state=None, limit=50, offset=0):
        raise ReviewNotSupportedError(
            "The review workflow requires the PostgreSQL persistence backend; "
            "set CFR_PERSISTENCE_BACKEND=postgres to use it."
        )

    def count_review_queue(self, *, state=None):
        raise ReviewNotSupportedError(
            "The review workflow requires the PostgreSQL persistence backend; "
            "set CFR_PERSISTENCE_BACKEND=postgres to use it."
        )

    def get_review(self, analysis_id, clause_id):
        raise ReviewNotSupportedError(
            "The review workflow requires the PostgreSQL persistence backend; "
            "set CFR_PERSISTENCE_BACKEND=postgres to use it."
        )

    def transition_review(
        self,
        analysis_id,
        clause_id,
        *,
        target_state,
        reason,
        reviewer_identity,
        expected_version,
    ):
        raise ReviewNotSupportedError(
            "The review workflow requires the PostgreSQL persistence backend; "
            "set CFR_PERSISTENCE_BACKEND=postgres to use it."
        )

    def list_review_events(self, analysis_id, clause_id):
        raise ReviewNotSupportedError(
            "The review workflow requires the PostgreSQL persistence backend; "
            "set CFR_PERSISTENCE_BACKEND=postgres to use it."
        )
