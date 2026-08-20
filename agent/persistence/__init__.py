"""agent/persistence

Persistence abstraction for compliance analysis reports.

The persistence boundary exists so the write path is backend-agnostic and
a future `PostgresRepository` can be introduced when the
dashboard/review/history layer creates a demonstrated need -- without
touching the pipeline, the API, or the existing JSON report format.

Current backend:
  - "file" -> `FileRepository` (delegates to `agent.reporting`)

Unsupported/not-yet-implemented backends (e.g. "postgres") fail clearly
at repository construction -- never a silent fallback to `file`.
"""

from __future__ import annotations

from typing import Any

from .base import PersistenceRepository
from .file_repository import FileRepository

__all__ = [
    "PersistenceRepository",
    "FileRepository",
    "get_persistence_repository",
]


def get_persistence_repository(
    settings: Any | None = None,
) -> PersistenceRepository:
    """Build the configured persistence backend (fail fast, no fallback).

    Args:
        settings: Optional `Settings` instance. When omitted, the
            process-wide `get_settings()` singleton is used.

    Returns:
        The configured `PersistenceRepository`.

    Raises:
        ValueError: if the configured backend is unsupported or not yet
            implemented (e.g. "postgres"). Backends never silently fall
            back to `file`.
    """
    from cfr_compliance_mcp.config import get_settings

    backend_settings = settings or get_settings()
    backend = backend_settings.cfr_persistence_backend

    if backend == "file":
        return FileRepository()

    raise ValueError(
        f"Persistence backend {backend!r} is not implemented. "
        "Supported backend: 'file'. PostgreSQL will follow only once the "
        "review/dashboard/history layer creates a demonstrated need."
    )