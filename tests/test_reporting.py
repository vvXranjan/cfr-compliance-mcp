"""P2.4 report persistence tests.

Cover successful writes, valid JSON with expected fields, preservation of
NEEDS_REVIEW / evidence / version metadata, safe filenames, path-traversal
defense, atomic overwrite safety, and the API bulk-endpoint integration.
All filesystem tests use pytest temporary directories.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.models import (
    Clause,
    ComplianceResult,
    EvidencePassage,
    ReviewAudit,
)
from agent.reporting import (
    build_report,
    load_report,
    new_analysis_id,
    resolve_report_path,
    sanitize_identifier,
    save_report,
)


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
    )


class TestSanitizeIdentifier:
    def test_strips_traversal(self) -> None:
        assert sanitize_identifier("../../etc/passwd") == "etc_passwd"
        assert sanitize_identifier("..") == "analysis"
        assert sanitize_identifier("../secret") == "secret"
        assert "/" not in sanitize_identifier("a/b\\c")

    def test_empty_becomes_analysis(self) -> None:
        assert sanitize_identifier(None) == "analysis"
        assert sanitize_identifier("") == "analysis"
        assert sanitize_identifier("   ") == "analysis"

    def test_safe_chars_preserved(self) -> None:
        assert sanitize_identifier("report-2026.08.20_1") == "report-2026.08.20_1"


class TestResolveReportPath:
    def test_path_is_within_reports_dir(self, tmp_path: Path) -> None:
        target = resolve_report_path("analysis-123", tmp_path)
        assert target == tmp_path / "analysis-123.json"
        assert target.is_relative_to(tmp_path)

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        # Even a malicious id cannot escape the reports directory.
        try:
            target = resolve_report_path("../../evil", tmp_path)
            assert target.parent == tmp_path.resolve() or tmp_path.resolve() in target.parents
        except ValueError:
            pass  # refusing is also correct


class TestBuildAndSave:
    def test_save_writes_valid_json(self, tmp_path: Path) -> None:
        record = build_report(
            [],
            [_verified_result()],
            analysis_id="analysis-2026-08-20-0001",
        )
        path = save_report(record, tmp_path)
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["analysis_id"] == "analysis-2026-08-20-0001"
        assert payload["total_clauses"] == 1
        assert payload["non_compliant"] == 1

    def test_expected_fields_present(self, tmp_path: Path) -> None:
        record = build_report(
            [],
            [_verified_result()],
            analysis_id="a1",
            contract_id="contract-007",
        )
        path = save_report(record, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "analysis_id", "contract_id", "generated_at", "total_clauses",
            "compliant", "non_compliant", "needs_review", "clauses",
        ):
            assert key in data
        clause = data["clauses"][0]
        for key in (
            "clause_id", "clause_title", "status", "confidence", "reason",
            "evidence", "verification_status", "review_reason", "review_audit",
        ):
            assert key in clause

    def test_needs_review_preserved(self, tmp_path: Path) -> None:
        record = build_report(
            [],
            [_needs_review_result(), _verified_result()],
            analysis_id="a2",
        )
        path = save_report(record, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["needs_review"] == 1
        review = next(c for c in data["clauses"] if c["status"] == "Needs Review")
        assert review["verification_status"] == "needs_review"
        assert review["review_reason"] == "Verifier conflict; human review required"
        audit = review["review_audit"]
        assert audit["verifier_recommendation"] == "review"
        assert audit["deterministic_status"] == "Needs Review"
        assert audit["evidence_citations"] == ["40 CFR 261.10"]
        assert audit["reviewed_at"]

    def test_evidence_and_version_metadata_preserved(self, tmp_path: Path) -> None:
        record = build_report([], [_needs_review_result()], analysis_id="a3")
        path = save_report(record, tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        ev = data["clauses"][0]["evidence"][0]
        assert ev["citation"] == "40 CFR 261.10"
        assert ev["date"] == "2026-08-17"
        assert ev["version"] == "2026-08-01"
        assert ev["source"] == "eCFR"
        assert ev["retrieval_method"] == "ecfr_api"
        assert ev["retrieved_at"]
        assert ev["confidence"] == 0.95

    def test_round_trip_load(self, tmp_path: Path) -> None:
        record = build_report(
            [Clause(title="T", text="body")],
            [_needs_review_result()],
            analysis_id="a4",
        )
        save_report(record, tmp_path)
        loaded = load_report("a4", tmp_path)
        assert loaded.analysis_id == "a4"
        assert loaded.clauses[0].review_audit.review_reason

    def test_no_full_contract_text_stored(self, tmp_path: Path) -> None:
        """Reports must not store the full sensitive contract body."""
        contract_body = "TOP SECRET CONTRACT BODY " * 100
        clauses = [Clause(title="SECTION 1. A", text=contract_body)]
        record = build_report(clauses, [_verified_result()], analysis_id="a5")
        path = save_report(record, tmp_path)
        raw = path.read_text(encoding="utf-8")
        assert "TOP SECRET CONTRACT BODY" not in raw

    def test_repeated_analysis_does_not_corrupt(self, tmp_path: Path) -> None:
        """Re-saving the same analysis id atomically replaces; distinct ids coexist."""
        first = build_report([], [_verified_result()], analysis_id="rep")
        save_report(first, tmp_path)

        second = build_report([], [_needs_review_result()], analysis_id="rep")
        save_report(second, tmp_path)

        # Same id -> fully replaced, valid JSON, latest content.
        data = json.loads((tmp_path / "rep.json").read_text(encoding="utf-8"))
        assert data["needs_review"] == 1

        # A different id never collides with the existing file.
        other = build_report([], [_verified_result()], analysis_id="rep-other")
        save_report(other, tmp_path)
        assert (tmp_path / "rep.json").exists()
        assert (tmp_path / "rep-other.json").exists()
        # No leftover temp files.
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


class TestNewAnalysisId:
    def test_unique_and_safe(self) -> None:
        a = new_analysis_id()
        b = new_analysis_id()
        assert a != b
        assert sanitize_identifier(a) == a
        assert "/" not in a


class TestAPIBulkPersistence:
    def test_bulk_endpoint_persists_report(self, tmp_path: Path, monkeypatch) -> None:
        import api

        monkeypatch.setattr(api, "REPORTS_DIR", str(tmp_path))
        from fastapi.testclient import TestClient

        client = TestClient(api.app)
        r = client.post(
            "/evaluate-bulk",
            json=[{"clause_title": "X", "clause_text": "Contractor shall do a thing."}],
        )
        assert r.status_code == 200
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["total_clauses"] == 1
        assert data["needs_review"] == 1
        assert data["clauses"][0]["review_audit"] is not None

    def test_bulk_persistence_failure_does_not_fail_request(self, tmp_path: Path, monkeypatch) -> None:  # noqa: E501
        import api

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(api, "REPORTS_DIR", str(tmp_path))
        monkeypatch.setattr(api, "_persist_bulk_report", _boom)
        from fastapi.testclient import TestClient

        client = TestClient(api.app)
        r = client.post(
            "/evaluate-bulk",
            json=[{"clause_title": "X", "clause_text": "Contractor shall do a thing."}],
        )
        assert r.status_code == 200
        assert r.json()["total_clauses"] == 1