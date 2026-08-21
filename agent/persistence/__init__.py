"""agent/persistence

Persistence abstraction for compliance analysis reports and the human
review workflow.

The boundary exists so the write/query path is backend-agnostic. Today:

  - "file" -> `FileRepository` (delegates to `agent.reporting`); provides
    analysis history but NOT the review workflow (not relational).
  - "postgres" -> `PostgresRepository` (psycopg 3, explicit SQL);
    queryable analysis history plus the transactional review workflow.

Backend selection is explicit and fails fast -- there is never a silent
fallback. PostgreSQL is optional; "file" remains the default and the
existing filesystem report persistence stays fully functional.
"""

from __future__ import annotations

from typing import Any

from .base import PersistenceRepository
from .file_repository import FileRepository

__all__ = [
    "PersistenceRepository",
    "FileRepository",
    "PostgresRepository",
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
        ValueError: if the configured backend is unsupported, or if
            "postgres" is selected without a valid ``CFR_DATABASE_URL``.
            Backends never silently fall back to `file`.
    """
    from cfr_compliance_mcp.config import get_settings

    backend_settings = settings or get_settings()
    backend = backend_settings.cfr_persistence_backend

    if backend == "file":
        return FileRepository()

    if backend == "postgres":
        if not backend_settings.cfr_database_url:
            raise ValueError(
                "CFR_PERSISTENCE_BACKEND=postgres requires CFR_DATABASE_URL to be set. "
                "No silent fallback to the 'file' backend is performed."
            )
        from .postgres_repository import PostgresRepository

        return PostgresRepository(backend_settings.cfr_database_url)

    raise ValueError(
        f"Persistence backend {backend!r} is not supported. "
        "Supported backends: 'file', 'postgres'."
    )
