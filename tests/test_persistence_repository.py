"""P5.1 persistence repository tests.

Cover the `PersistenceRepository` boundary and its `FileRepository`
implementation: save/load through the abstraction, preservation of the
existing report shape (analysis id, clauses, evidence, ReviewAudit,
VERIFIED/NEEDS_REVIEW), clear rejection of unsupported backends, and
non-blocking write failure semantics. All deterministic, filesystem
tests use pytest temporary directories. No PostgreSQL, no external
services.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api as api_module
from agent.models import Clause, ComplianceResult, EvidencePassage, ReviewAudit
from agent.persistence import FileRepository, PersistenceRepository, get_persistence_repository
from agent.reporting import (
    ReportRecord,
    build_report,
    load_report,
    new_analysis_id,
    save_report,
)
from cfr_compliance_mcp.config import Settings


def _needs_review_result() -> ComplianceResult:
    evidence = EvidencePassage(
        title=40,
        part="261",
        section="10",
        date="2026-08-17",
        text_span="Hazardous waste must be disposed per EPA requirements.",
        citation="40 CFR 261.10",
        source="eCFR",
        retrieved_at="2026-08-19T12:00:00+00:00",
        retrieval_method="ecfr_api",
        version="2026-08-01",
        confidence=0.95,
    )
    audit = ReviewAudit(
        final_status="Needs Review",
        proposed_status="Compliant",
        proposed_confidence=0.9,
        review_reason="Verifier conflict; human review required",
        verifier_recommendation="review",
        verifier_notes="Deterministic rules conflict with LLM verdict",
        deterministic_status="Needs Review",
        evidence_citations=["40 CFR 261.10"],
        reviewed_at="2026-08-20T00:00:00+00:00",
    )
    return ComplianceResult(
        clause_title="Hazardous Waste Disposal",
        clause_id="abc12345",
        status="Needs Review",
        confidence=0.0,
        reason="Needs human review",
        evidence=[evidence],
        verification_status="needs_review",
        review_reason="Verifier conflict; human review required",
        review_audit=audit,
    )


def _verified_result() -> ComplianceResult:
    return ComplianceResult(
        clause_title="Office Cleaning",
        clause_id="def67890",
        status="Non-Compliant",
        confidence=0.8,
        reason="Clause fails to address mandatory obligation",
        evidence=[
            EvidencePassage(
                title=40,
                part="261",
                section="10",
                date="2026-08-17",
                text_span="Hazardous waste must be disposed.",
                citation="40 CFR 261.10",
                source="eCFR",
                retrieved_at="2026-08-19T12:00:00+00:00",
                retrieval_method="ecfr_api",
                version="2026-08-01",
            )
        ],
        verification_status="verified",
        memory_participated=True,
    )


def _record(*, analysis_id: str | None = None) -> tuple[ReportRecord, list[ComplianceResult]]:
    clauses = [Clause(title="Hazardous Waste Disposal", text="body 1")]
    results = [_needs_review_result(), _verified_result()]
    return build_report(
        clauses,
        results,
        analysis_id=analysis_id or new_analysis_id(),
        contract_id="contract-42",
    ), results


def _repo(tmp_path: Path) -> FileRepository:
    return FileRepository(reports_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# Repository behavior
# ---------------------------------------------------------------------------


class TestFileRepository:
    def test_save_produces_existing_report_behavior(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        written = repo.save_report(record)
        # Same JSON as the existing reporting.save_report path.
        expected = save_report(record, reports_dir=str(tmp_path))
        assert written == expected
        assert json.loads(written.read_text(encoding="utf-8"))["analysis_id"] == record.analysis_id

    def test_load_through_repository(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        assert loaded == record

    def test_preserves_analysis_id(self, tmp_path) -> None:
        record, _ = _record(analysis_id="analysis-fixed-id")
        repo = _repo(tmp_path)
        repo.save_report(record)
        assert repo.load_report("analysis-fixed-id").analysis_id == "analysis-fixed-id"

    def test_clause_results_preserved(self, tmp_path) -> None:
        record, results = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        assert [c.clause_id for c in loaded.clauses] == [r.clause_id for r in results]
        assert [c.status for c in loaded.clauses] == [r.status for r in results]

    def test_evidence_metadata_preserved(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        ev = loaded.clauses[0].evidence[0]
        assert ev.title == 40
        assert ev.citation == "40 CFR 261.10"
        assert ev.source == "eCFR"
        assert ev.retrieval_method == "ecfr_api"
        assert ev.version == "2026-08-01"
        assert ev.date == "2026-08-17"

    def test_review_audit_preserved(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        audit = loaded.clauses[0].review_audit
        assert audit is not None
        assert audit.review_reason == "Verifier conflict; human review required"
        assert audit.verifier_recommendation == "review"
        assert audit.deterministic_status == "Needs Review"
        assert audit.evidence_citations == ["40 CFR 261.10"]

    def test_verified_result_persists(self, tmp_path) -> None:
        record, results = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        verified = [c for c in loaded.clauses if c.verification_status == "verified"]
        assert len(verified) == 1
        assert verified[0].clause_id == results[1].clause_id

    def test_needs_review_result_persists(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        loaded = repo.load_report(record.analysis_id)
        nr = [c for c in loaded.clauses if c.verification_status == "needs_review"]
        assert len(nr) == 1
        assert nr[0].review_reason == "Verifier conflict; human review required"

    def test_file_repository_satisfies_protocol(self) -> None:
        assert isinstance(_repo(Path(".")), PersistenceRepository)

    def test_file_repository_is_runtime_checkable(self, tmp_path) -> None:
        from agent.persistence.base import PersistenceRepository as _Proto

        assert isinstance(_repo(tmp_path), _Proto)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_default_backend_is_file(self) -> None:
        assert Settings().cfr_persistence_backend == "file"
        assert get_persistence_repository().backend == "file"

    def test_postgres_backend_without_dsn_fails_clearly(self) -> None:
        settings = Settings(cfr_persistence_backend="postgres", cfr_database_url="")
        with pytest.raises(ValueError, match="CFR_DATABASE_URL"):
            get_persistence_repository(settings)

    def test_unknown_backend_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(cfr_persistence_backend="mysql")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# API integration through the repository (non-blocking)
# ---------------------------------------------------------------------------


class TestAPIIntegration:
    def test_api_uses_file_repository_by_default(self) -> None:
        assert api_module.PERSISTENCE_REPO.backend == "file"

    def test_bulk_persists_through_repository(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(api_module, "REPORTS_DIR", str(tmp_path))
        client = TestClient(api_module.app)
        r = client.post(
            "/evaluate-bulk",
            json=[{"clause_title": "X", "clause_text": "Contractor shall do a thing."}],
        )
        assert r.status_code == 200
        assert len(list(tmp_path.glob("*.json"))) == 1

    def test_write_failure_remains_non_blocking(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(api_module, "REPORTS_DIR", str(tmp_path))

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(api_module.PERSISTENCE_REPO, "save_report", _boom)
        client = TestClient(api_module.app)
        r = client.post(
            "/evaluate-bulk",
            json=[{"clause_title": "X", "clause_text": "Contractor shall do a thing."}],
        )
        assert r.status_code == 200
        assert r.json()["total_clauses"] == 1


# ---------------------------------------------------------------------------
# Regression: existing reporting behavior still valid through the abstraction
# ---------------------------------------------------------------------------


class TestReportingCompatibility:
    def test_repository_round_trip_matches_existing_reporting(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        direct = load_report(record.analysis_id, reports_dir=str(tmp_path))
        via_repo = repo.load_report(record.analysis_id)
        assert via_repo == direct == record

    def test_no_full_contract_text_stored(self, tmp_path) -> None:
        record, _ = _record()
        repo = _repo(tmp_path)
        repo.save_report(record)
        raw = json.loads(repo.load_report(record.analysis_id).model_dump_json())
        assert "body 1" not in json.dumps(raw)
        assert "Clause(text" not in json.dumps(raw)