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

Changing this backend's behavior or the report JSON format is a breaking
change by design; do not do it silently.
"""

from __future__ import annotations

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


class FileRepository:
    """Filesystem backend implementing the persistence protocol."""

    backend = "file"

    def __init__(
        self,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    ) -> None:
        self.reports_dir = Path(reports_dir)

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