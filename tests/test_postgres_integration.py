"""Optional PostgreSQL integration tests for P6.2.

These tests require a real PostgreSQL test database and are SKIPPED
cleanly when none is configured (they never silently pass). To run them:

    export CFR_TEST_DATABASE_URL=postgresql://user:pass@localhost/cfr_test
    uv run pytest tests/test_postgres_integration.py -q

They exercise the migration runner and `PostgresRepository` end-to-end:
schema application + idempotent re-run, analysis save/load, evidence /
ReviewAudit / memory_participated round trips, review lifecycle,
optimistic concurrency, listing, filtering, pagination, missing-analysis
errors, and the no-contract-text guarantee.

The main offline suite never requires these; they are isolated here.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from agent.persistence.postgres_repository import PostgresRepository
from agent.reporting import new_analysis_id
from scripts.migrate import run_migrations
from tests.test_history_review import _make_report

pytestmark = pytest.mark.skipif(
    os.getenv("CFR_TEST_DATABASE_URL") is None,
    reason="CFR_TEST_DATABASE_URL is not set; skipping PostgreSQL integration tests",
)

_TABLES = [
    "review_decision_events",
    "review_decisions",
    "review_audits",
    "evidence",
    "clause_analyses",
    "analyses",
]


@pytest.fixture()
def dsn() -> str:
    return os.environ["CFR_TEST_DATABASE_URL"]


@pytest.fixture()
def repo(dsn: str) -> PostgresRepository:
    run_migrations(dsn)
    return PostgresRepository(dsn)


@pytest.fixture(autouse=True)
def _clean(dsn: str):
    yield
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for t in _TABLES:
                cur.execute(f"TRUNCATE TABLE {t} CASCADE")
        conn.commit()


def test_migration_applies_and_reruns_idempotent(dsn: str) -> None:
    first = run_migrations(dsn)
    second = run_migrations(dsn)
    # Re-run applies nothing new.
    assert second == []
    with psycopg.connect(dsn) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
    for t in _TABLES + ["schema_migrations"]:
        assert t in tables
    assert isinstance(first, list)


def test_save_load_round_trip(repo) -> None:
    aid = new_analysis_id()
    repo.save_report(_make_report(aid))
    loaded = repo.load_report(aid)
    assert loaded.analysis_id == aid
    assert [c.clause_id for c in loaded.clauses] == ["abc12345", "def67890"]


def test_evidence_audit_memory_round_trip(repo) -> None:
    aid = new_analysis_id()
    repo.save_report(_make_report(aid))
    loaded = repo.load_report(aid)
    ev = loaded.clauses[0].evidence[0]
    assert ev.citation == "40 CFR 261.10"
    assert ev.retrieval_method == "ecfr_api"
    assert loaded.clauses[0].review_audit.verifier_recommendation == "review"
    assert loaded.clauses[1].memory_participated is True


def test_no_contract_text(repo) -> None:
    aid = new_analysis_id()
    repo.save_report(_make_report(aid))
    dump = repo.load_report(aid).model_dump_json()
    assert "body-a" not in dump
    assert "body-b" not in dump


def test_list_filter_paginate(repo) -> None:
    for _ in range(3):
        repo.save_report(_make_report(new_analysis_id()))
    assert repo.count_analyses() == 3
    assert len(repo.list_analyses(limit=2, offset=0)) == 2
    assert repo.count_analyses(status="needs_review") == 3


def test_missing_analysis_404(repo) -> None:
    from agent.persistence.base import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        repo.load_report("does-not-exist")


def test_review_lifecycle_and_concurrency(repo) -> None:
    from agent.persistence.base import ConcurrencyError, ReviewStateError

    aid = new_analysis_id()
    repo.save_report(_make_report(aid))

    queue = repo.list_review_queue(state="needs_review")
    assert len(queue) == 1
    assert queue[0].clause_id == "abc12345"

    # Concurrency conflict on stale version.
    with pytest.raises(ConcurrencyError):
        repo.transition_review(
            aid, "abc12345", target_state="under_review",
            reason="c", reviewer_identity="a", expected_version=99,
        )

    # Invalid transition (skip the claim step).
    with pytest.raises(ReviewStateError):
        repo.transition_review(
            aid, "abc12345", target_state="approved",
            reason="skip", reviewer_identity="a", expected_version=1,
        )

    result = repo.transition_review(
        aid, "abc12345", target_state="under_review",
        reason="claim", reviewer_identity="a", expected_version=1,
    )
    assert result.version == 2

    result = repo.transition_review(
        aid, "abc12345", target_state="approved",
        reason="ok", reviewer_identity="a", expected_version=2,
    )
    assert result.review_state == "approved"

    events = repo.list_review_events(aid, "abc12345")
    assert [e.state for e in events] == ["under_review", "approved"]

    detail = repo.get_review(aid, "abc12345")
    assert detail.original_status == "Needs Review"
    assert detail.review_state == "approved"


def test_decision_does_not_overwrite_original(repo) -> None:
    aid = new_analysis_id()
    repo.save_report(_make_report(aid))
    repo.transition_review(
        aid, "abc12345", target_state="under_review",
        reason="c", reviewer_identity="a", expected_version=1,
    )
    repo.transition_review(
        aid, "abc12345", target_state="approved",
        reason="ok", reviewer_identity="a", expected_version=2,
    )
    loaded = repo.load_report(aid)
    assert loaded.clauses[0].status == "Needs Review"
    assert repo.get_review(aid, "abc12345").original_status == "Needs Review"


def test_summarize_aggregates(repo) -> None:
    repo.save_report(_make_report(new_analysis_id()))
    repo.save_report(_make_report(new_analysis_id()))
    m = repo.summarize()
    assert m.total_analyses == 2
    assert m.total_clauses == 4
    assert m.compliant == 0
    assert m.non_compliant == 2
    assert m.needs_review == 2
    assert m.review_pending == 2
