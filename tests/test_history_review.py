"""P6.2 analysis-history and human-review tests (offline, deterministic).

Use the in-memory `InMemoryRepository` (which mirrors the PostgreSQL
semantics) to exercise the history/review repository behavior and the
new API endpoints without requiring a database. The file backend's
"review not supported" behavior is also covered.

External services are never required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api as api_module
from agent.models import Clause, ComplianceResult, EvidencePassage, ReviewAudit
from agent.persistence import FileRepository
from agent.persistence.base import (
    AnalysisNotFoundError,
    ConcurrencyError,
    ReviewNotFoundError,
    ReviewStateError,
)
from agent.persistence.inmemory_repository import InMemoryRepository
from agent.reporting import ReportRecord, build_report, new_analysis_id


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


def _make_report(analysis_id: str, contract_id: str = "contract-42") -> ReportRecord:
    nr = _needs_review_result()
    vr = _verified_result()
    return build_report(
        [
            Clause(title=nr.clause_title, text="body-a"),
            Clause(title=vr.clause_title, text="body-b"),
        ],
        [nr, vr],
        analysis_id=analysis_id,
        contract_id=contract_id,
    )


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


def _seed(repo: InMemoryRepository, *, analysis_id: str | None = None) -> str:
    aid = analysis_id or new_analysis_id()
    repo.save_report(_make_report(aid))
    return aid


# ---------------------------------------------------------------------------
# Repository behavior (in-memory mirrors PostgreSQL semantics)
# ---------------------------------------------------------------------------


class TestHistoryRepository:
    def test_save_load_round_trip(self, repo) -> None:
        aid = _seed(repo)
        loaded = repo.load_report(aid)
        assert loaded.analysis_id == aid
        assert [c.clause_id for c in loaded.clauses] == ["abc12345", "def67890"]

    def test_evidence_and_provenance_round_trip(self, repo) -> None:
        aid = _seed(repo)
        loaded = repo.load_report(aid)
        ev = loaded.clauses[0].evidence[0]
        assert ev.citation == "40 CFR 261.10"
        assert ev.source == "eCFR"
        assert ev.retrieval_method == "ecfr_api"
        assert ev.version == "2026-08-01"

    def test_review_audit_round_trip(self, repo) -> None:
        aid = _seed(repo)
        loaded = repo.load_report(aid)
        audit = loaded.clauses[0].review_audit
        assert audit is not None
        assert audit.verifier_recommendation == "review"
        assert audit.evidence_citations == ["40 CFR 261.10"]

    def test_memory_participated_round_trip(self, repo) -> None:
        aid = _seed(repo)
        loaded = repo.load_report(aid)
        assert loaded.clauses[1].memory_participated is True
        assert loaded.clauses[0].memory_participated is False

    def test_no_contract_text_stored(self, repo) -> None:
        aid = _seed(repo)
        loaded = repo.load_report(aid)
        dump = loaded.model_dump_json()
        assert "body-a" not in dump
        assert "body-b" not in dump

    def test_list_and_paginate(self, repo) -> None:
        a1 = _seed(repo)
        a2 = _seed(repo)
        a3 = _seed(repo)
        all_items = repo.list_analyses(limit=50, offset=0)
        ids = {i.analysis_id for i in all_items}
        assert ids == {a1, a2, a3}
        page = repo.list_analyses(limit=2, offset=0)
        assert len(page) == 2
        assert repo.count_analyses() == 3

    def test_status_filter(self, repo) -> None:
        _seed(repo)
        # Every seeded analysis has a needs_review clause, so filtering works.
        assert repo.count_analyses(status="needs_review") >= 1

    def test_missing_analysis_raises(self, repo) -> None:
        with pytest.raises(AnalysisNotFoundError):
            repo.load_report("nope")


class TestReviewRepository:
    def test_queue_defaults_and_filter(self, repo) -> None:
        _seed(repo)
        items = repo.list_review_queue(state="needs_review")
        assert len(items) == 1
        assert items[0].clause_id == "abc12345"
        assert items[0].review_state == "needs_review"
        assert repo.count_review_queue(state="needs_review") == 1
        assert repo.count_review_queue() == 1

    def test_get_review_shows_original_and_audit(self, repo) -> None:
        aid = _seed(repo)
        detail = repo.get_review(aid, "abc12345")
        assert detail.original_status == "Needs Review"
        assert detail.review_audit is not None
        assert detail.review_state == "needs_review"
        assert detail.version == 1
        assert detail.evidence[0].citation == "40 CFR 261.10"

    def test_get_review_verified_clause_has_no_review_state(self, repo) -> None:
        aid = _seed(repo)
        detail = repo.get_review(aid, "def67890")
        assert detail.review_state is None
        assert detail.memory_participated is True

    def test_valid_claim_transition(self, repo) -> None:
        aid = _seed(repo)
        result = repo.transition_review(
            aid, "abc12345", target_state="under_review", reason="claimed",
            reviewer_identity="alice", expected_version=1,
        )
        assert result.review_state == "under_review"
        assert result.version == 2

    def test_approve_then_reject_lifecycle(self, repo) -> None:
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="claim", reviewer_identity="a", expected_version=1)
        result = repo.transition_review(aid, "abc12345", target_state="approved",
                                        reason="ok", reviewer_identity="a", expected_version=2)
        assert result.review_state == "approved"
        assert len(result.events) == 2

    def test_invalid_transition_rejected(self, repo) -> None:
        aid = _seed(repo)
        with pytest.raises(ReviewStateError):
            repo.transition_review(aid, "abc12345", target_state="approved",
                                   reason="skip", reviewer_identity="a", expected_version=1)

    def test_concurrency_success(self, repo) -> None:
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="c1", reviewer_identity="a", expected_version=1)

    def test_concurrency_conflict(self, repo) -> None:
        aid = _seed(repo)
        with pytest.raises(ConcurrencyError):
            repo.transition_review(aid, "abc12345", target_state="under_review",
                                   reason="c1", reviewer_identity="a", expected_version=99)

    def test_decision_never_overwrites_original(self, repo) -> None:
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="c1", reviewer_identity="a", expected_version=1)
        repo.transition_review(aid, "abc12345", target_state="approved",
                               reason="ok", reviewer_identity="a", expected_version=2)
        detail = repo.get_review(aid, "abc12345")
        assert detail.original_status == "Needs Review"
        # The immutable automated clause snapshot is untouched.
        loaded = repo.load_report(aid)
        assert loaded.clauses[0].status == "Needs Review"

    def test_immutable_event_history(self, repo) -> None:
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="c1", reviewer_identity="a", expected_version=1)
        repo.transition_review(aid, "abc12345", target_state="approved",
                               reason="ok", reviewer_identity="b", expected_version=2)
        events = repo.list_review_events(aid, "abc12345")
        assert [e.state for e in events] == ["under_review", "approved"]
        assert events[1].reviewer_identity == "b"

    def test_cannot_review_verified_clause(self, repo) -> None:
        aid = _seed(repo)
        with pytest.raises(ReviewNotFoundError):
            repo.transition_review(aid, "def67890", target_state="under_review",
                                   reason="x", reviewer_identity="a", expected_version=0)

    def test_review_survives_resave(self, repo) -> None:
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="c1", reviewer_identity="a", expected_version=1)
        repo.save_report(_make_report(aid))  # re-save same analysis
        detail = repo.get_review(aid, "abc12345")
        assert detail.review_state == "under_review"
        assert detail.version == 2


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestAnalysisHistoryAPI:
    def _client(self, monkeypatch) -> TestClient:
        repo = InMemoryRepository()
        monkeypatch.setattr(api_module, "PERSISTENCE_REPO", repo)
        return TestClient(api_module.app), repo

    def test_list_analyses(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        _seed(repo)
        r = client.get("/analyses")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["analysis_id"]

    def test_invalid_status_filter_rejected(self, monkeypatch) -> None:
        client, _ = self._client(monkeypatch)
        assert client.get("/analyses?status=not_a_status").status_code == 422

    def test_invalid_pagination_bounds_rejected(self, monkeypatch) -> None:
        client, _ = self._client(monkeypatch)
        assert client.get("/analyses?limit=0").status_code == 422
        assert client.get("/analyses?limit=999999").status_code == 422
        assert client.get("/analyses?offset=-1").status_code == 422

    def test_list_pagination(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        _seed(repo)
        _seed(repo)
        _seed(repo)
        r = client.get("/analyses", params={"limit": 2, "offset": 0})
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3

    def test_get_analysis_detail(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.get(f"/analyses/{aid}")
        assert r.status_code == 200
        body = r.json()
        assert body["analysis_id"] == aid
        clause = body["clauses"][1]
        assert clause["memory_participated"] is True
        assert clause["review_audit"] is None

    def test_get_analysis_missing_404(self, monkeypatch) -> None:
        client, _ = self._client(monkeypatch)
        r = client.get("/analyses/does-not-exist")
        assert r.status_code == 404

    def test_no_contract_text_in_history(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.get(f"/analyses/{aid}")
        assert "body-a" not in r.text
        assert "body-b" not in r.text


class TestReviewAPI:
    def _client(self, monkeypatch) -> TestClient:
        repo = InMemoryRepository()
        monkeypatch.setattr(api_module, "PERSISTENCE_REPO", repo)
        return TestClient(api_module.app), repo

    def test_queue_defaults_to_needs_review(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        _seed(repo)
        r = client.get("/reviews")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["review_state"] == "needs_review"

    def test_queue_filter_by_state(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        repo.transition_review(aid, "abc12345", target_state="under_review",
                               reason="c", reviewer_identity="a", expected_version=1)
        r = client.get("/reviews", params={"state": "under_review"})
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["review_state"] == "under_review"
        r2 = client.get("/reviews", params={"state": "needs_review"})
        assert r2.json()["total"] == 0

    def test_get_review_detail(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.get(f"/reviews/{aid}/abc12345")
        assert r.status_code == 200
        body = r.json()
        assert body["original_status"] == "Needs Review"
        assert body["review_state"] == "needs_review"
        assert body["evidence"][0]["citation"] == "40 CFR 261.10"

    def test_get_review_missing_404(self, monkeypatch) -> None:
        client, _ = self._client(monkeypatch)
        r = client.get("/reviews/nope/abc12345")
        assert r.status_code == 404

    def test_decide_valid(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.post(
            f"/reviews/{aid}/abc12345/decide",
            json={"target_state": "under_review", "reason": "claimed",
                  "reviewer_identity": "alice", "expected_version": 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["review_state"] == "under_review"
        assert body["version"] == 2

    def test_decide_invalid_transition_409(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.post(
            f"/reviews/{aid}/abc12345/decide",
            json={"target_state": "approved", "reason": "skip",
                  "reviewer_identity": "alice", "expected_version": 1},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "invalid_transition"

    def test_decide_concurrency_conflict_409(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.post(
            f"/reviews/{aid}/abc12345/decide",
            json={"target_state": "under_review", "reason": "c",
                  "reviewer_identity": "alice", "expected_version": 99},
        )
        assert r.status_code == 409
        assert r.json()["error"] == "concurrency_conflict"

    def test_decide_missing_404(self, monkeypatch) -> None:
        client, _ = self._client(monkeypatch)
        r = client.post(
            "/reviews/nope/abc12345/decide",
            json={"target_state": "under_review", "reason": "c",
                  "reviewer_identity": "a", "expected_version": 0},
        )
        assert r.status_code == 404

    def test_decide_validation_422(self, monkeypatch) -> None:
        client, repo = self._client(monkeypatch)
        aid = _seed(repo)
        r = client.post(
            f"/reviews/{aid}/abc12345/decide",
            json={"target_state": "bogus", "expected_version": 1},
        )
        assert r.status_code == 422


class TestFileBackendReviewUnsupported:
    def test_review_endpoint_501_on_file_backend(self) -> None:
        # Default PERSISTENCE_REPO is a FileRepository (no monkeypatch).
        assert api_module.PERSISTENCE_REPO.backend == "file"
        client = TestClient(api_module.app)
        r = client.get("/reviews")
        assert r.status_code == 501
        assert r.json()["error"] == "review_not_supported"

    def test_file_repo_review_methods_raise(self, tmp_path: Path) -> None:
        from agent.persistence.base import ReviewNotSupportedError

        repo = FileRepository(reports_dir=str(tmp_path))
        with pytest.raises(ReviewNotSupportedError):
            repo.list_review_queue()
        with pytest.raises(ReviewNotSupportedError):
            repo.transition_review("a", "c", target_state="approved", reason="",
                                   reviewer_identity="", expected_version=1)


class TestReadiness:
    def test_file_backend_ready(self) -> None:
        client = TestClient(api_module.app)  # default file backend
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert "ready" in r.json()["status"]
        assert "file" in r.json()["backend"]

    def test_postgres_unreachable_not_ready(self, monkeypatch) -> None:
        class FakePG:
            backend = "postgres"

            def ping(self):
                raise RuntimeError("database down")

        monkeypatch.setattr(api_module, "PERSISTENCE_REPO", FakePG())
        client = TestClient(api_module.app)
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not_ready"

    def test_postgres_reachable_ready(self, monkeypatch) -> None:
        class FakePG:
            backend = "postgres"

            def ping(self):
                return None

        monkeypatch.setattr(api_module, "PERSISTENCE_REPO", FakePG())
        client = TestClient(api_module.app)
        r = client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["backend"] == "postgres"

    def test_readiness_never_leaks_dsn(self, monkeypatch) -> None:
        class FakePG:
            backend = "postgres"

            def ping(self):
                raise RuntimeError("secret-password@host")

        monkeypatch.setattr(api_module, "PERSISTENCE_REPO", FakePG())
        client = TestClient(api_module.app)
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert "secret-password" not in r.text
