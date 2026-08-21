"""Minimal, deterministic PostgreSQL migration runner.

Applies versioned SQL migrations (``migrations/*.sql``) in filename
order, tracking applied versions in a ``schema_migrations`` table so a
migration runs at most once. Safe to run repeatedly: already-applied
migrations are skipped and every migration is idempotent.

Usage:
    uv run python scripts/migrate.py
    uv run python scripts/migrate.py --dsn postgresql://user:pass@host/db

The DSN is read from ``--dsn`` or ``CFR_DATABASE_URL`` (same variable the
application uses). No ORM, no Alembic -- plain psycopg 3.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _get_dsn(args: argparse.Namespace) -> str:
    dsn = args.dsn or os.getenv("CFR_DATABASE_URL", "")
    if not dsn:
        raise SystemExit(
            "No database URL provided. Pass --dsn or set CFR_DATABASE_URL."
        )
    return dsn


def run_migrations(dsn: str, migrations_dir: Path = _MIGRATIONS_DIR) -> list[str]:
    """Apply all pending migrations; returns the applied version list."""
    applied: list[str] = []
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}

        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem  # e.g. "0001_initial_schema"
            if version in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
            )
            applied.append(version)

        conn.commit()
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply CFR compliance SQL migrations.")
    parser.add_argument("--dsn", default=None, help="PostgreSQL connection string.")
    args = parser.parse_args()

    try:
        applied = run_migrations(_get_dsn(args))
    except psycopg.Error as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
