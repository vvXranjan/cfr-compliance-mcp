"""agent/persistence/base.py

Stable persistence boundary for compliance analysis reports and the
human review workflow.

The pipeline/API persist and load reports only through these protocols,
never against a concrete backend directly. Two backends implement them:

  * `FileRepository` -- filesystem reports (delegating to
    `agent.reporting`) plus analysis-history listing. It intentionally
    does NOT implement relational review queries; its review methods
    raise `ReviewNotSupportedError`.
  * `PostgresRepository` -- queryable analysis history and the full
    transactional human-review workflow, via explicit SQL (psycopg 3,
    no ORM).

Reuse, never duplicate: the data shape is the existing Pydantic report
model (`ReportRecord` / `ClauseReport` / `EvidencePassage` /
`ReviewAudit`) plus the small boundary models in ``models.py``.

The automated result, evidence, and `ReviewAudit` are IMMUTABLE
historical records. Human review is an additional decision layer and
never overwrites them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from agent.reporting import ReportRecord

from .models import (
    AnalysisSummary,
    MetricsSummary,
    ReviewDetail,
    ReviewEvent,
    ReviewItem,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PersistenceError(Exception):
    """Base class for persistence-layer errors."""


class AnalysisNotFoundError(PersistenceError):
    """Raised when an analysis/report does not exist."""


class ReviewNotFoundError(PersistenceError):
    """Raised when a clause has no review record and none can be opened."""


class ReviewNotSupportedError(PersistenceError):
    """Raised by a backend that cannot support the review workflow.

    Filesystem persistence is not relational; review queue queries and
    transitions require the PostgreSQL backend.
    """


class ReviewStateError(PersistenceError):
    """Raised when a review transition is not explicitly allowed."""


class ConcurrencyError(PersistenceError):
    """Raised when an optimistic-concurrency version check fails."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PersistenceRepository(Protocol):
    """Boundary contract implemented by every persistence backend.

    Report methods are safe to call from the evaluation request path: a
    backend failure is the caller's to handle and must never corrupt an
    otherwise successful compliance analysis (persistence is
    non-blocking for the evaluation pipeline).

    Review methods require a backend that supports the review workflow;
    `FileRepository` raises `ReviewNotSupportedError` for them.
    """

    backend: str

    def save_report(
        self,
        record: ReportRecord,
        *,
        reports_dir: str | Path | None = None,
    ) -> Path | str:
        """Persist a report; returns the written location or analysis id."""
        ...

    def load_report(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        """Load a previously persisted report by analysis id."""
        ...

    def get_analysis(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        """Alias of `load_report` for the history API."""
        ...

    def list_analyses(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        reports_dir: str | Path | None = None,
    ) -> list[AnalysisSummary]:
        """List analysis history, optionally filtered by status."""
        ...

    def count_analyses(
        self,
        *,
        status: str | None = None,
        reports_dir: str | Path | None = None,
    ) -> int:
        """Total number of analyses matching ``status`` (for pagination)."""
        ...

    def summarize(
        self,
        *,
        reports_dir: str | Path | None = None,
    ) -> MetricsSummary:
        """Aggregate compliance metrics across all persisted analyses."""
        ...

    def list_review_queue(
        self,
        *,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        """List review-queue items, optionally filtered by review state."""
        ...

    def count_review_queue(self, *, state: str | None = None) -> int:
        """Total number of review-queue items matching ``state``."""
        ...

    def get_review(
        self,
        analysis_id: str,
        clause_id: str,
    ) -> ReviewDetail:
        """Return the full review view for a clause."""
        ...

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
        """Apply a validated review transition with optimistic concurrency."""
        ...

    def list_review_events(
        self,
        analysis_id: str,
        clause_id: str,
    ) -> list[ReviewEvent]:
        """Return the immutable transition history for a review record."""
        ...
