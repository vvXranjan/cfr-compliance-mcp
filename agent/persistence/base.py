"""agent/persistence/base.py

Stable persistence boundary for compliance analysis reports.

The pipeline/API persist and load reports only through this protocol,
never against a concrete backend directly. Today the only backend is
`FileRepository` (delegating to `agent.reporting`). A future
`PostgresRepository` will implement the same protocol when the
dashboard/review/history layer creates a demonstrated need for
queryable relational persistence.

The interface intentionally supports ONLY what the current architecture
genuinely needs: save an analysis/report and load it by analysis id. No
speculative CRUD, pagination, or search -- those arrive with the consumer
that requires them.

Reuse, never duplicate: the data shape is the existing Pydantic report
model (`ReportRecord` / `ClauseReport` / `EvidencePassage` /
`ReviewAudit`) -- no parallel domain model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from agent.reporting import ReportRecord


@runtime_checkable
class PersistenceRepository(Protocol):
    """Boundary contract implemented by every persistence backend.

    ``save_report`` persists a fully-built report and returns the written
    location. ``load_report`` reads a previously persisted report by its
    (sanitized) analysis id. Both must be safe to call from the API
    request path: a backend failure is the caller's to handle and must
    never corrupt an otherwise successful compliance analysis.
    """

    backend: str

    def save_report(
        self,
        record: ReportRecord,
        *,
        reports_dir: str | Path | None = None,
    ) -> Path:
        ...

    def load_report(
        self,
        analysis_id: str,
        *,
        reports_dir: str | Path | None = None,
    ) -> ReportRecord:
        ...