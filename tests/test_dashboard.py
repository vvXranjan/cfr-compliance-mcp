"""P6.3 dashboard tests (offline, deterministic).

Exercise the server-rendered Jinja2 dashboard over the persistence/review
layer using the in-memory `InMemoryRepository`. No database or external
services required. Also covers the file-backend "review not supported"
notice and the no-secret / no-contract-text boundary.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api as api_module
import dashboard as dashboard_module
from agent.persistence.file_repository import FileRepository
from agent.persistence.inmemory_repository import InMemoryRepository
from agent.reporting import new_analysis_id
from tests.test_history_review import _make_report


@pytest.fixture()
def client() -> tuple[TestClient, InMemoryRepository, str]:
    repo = InMemoryRepository()
    # The in-memory fake stands in for the PostgreSQL-backed review UI.
    repo.backend = "postgres"
    aid = new_analysis_id()
    repo.save_report(_make_report(aid))
    dashboard_module.set_repository(repo)
    return TestClient(api_module.app), repo, aid


def _get(client, path, *, params=None):
    return client.get(path, params=params)


class TestOverview:
    def test_index_renders(self, client) -> None:
        c, _, _ = client
        r = c.get("/dashboard")
        assert r.status_code == 200
        body = r.text
        assert "Compliance Overview" in body
        assert "1" in body  # one analysis

    def test_index_has_review_count_for_postgres(self, client) -> None:
        c, _, _ = client
        r = c.get("/dashboard")
        assert "Needs Review" in r.text


class TestAnalyses:
    def test_analyses_list(self, client) -> None:
        c, _, _ = client
        r = _get(c, "/dashboard/analyses")
        assert r.status_code == 200
        assert "Analysis History" in r.text

    def test_analysis_detail_renders_evidence_and_memory(self, client) -> None:
        c, _, aid = client
        r = c.get(f"/dashboard/analyses/{aid}")
        assert r.status_code == 200
        body = r.text
        assert "40 CFR 261.10" in body  # authoritative evidence rendered
        assert "historical memory" in body  # memory badge for verified clause
        # No contract body, secrets, or prompts.
        assert "body-a" not in body
        assert "body-b" not in body

    def test_analysis_detail_missing_404(self, client) -> None:
        c, _, _ = client
        r = c.get("/dashboard/analyses/does-not-exist")
        assert r.status_code == 404


class TestReviews:
    def test_reviews_list(self, client) -> None:
        c, _, _ = client
        r = _get(c, "/dashboard/reviews")
        assert r.status_code == 200
        assert "Human Review Queue" in r.text
        assert "Hazardous Waste Disposal" in r.text

    def test_review_detail_renders_audit_and_form(self, client) -> None:
        c, _, aid = client
        r = c.get(f"/dashboard/reviews/{aid}/abc12345")
        assert r.status_code == 200
        body = r.text
        assert "40 CFR 261.10" in body
        assert "Record decision" in body
        assert "under_review" in body  # allowed transition for needs_review
        assert "historical memory" not in body  # this clause not memory-assisted

    def test_review_detail_verified_clause_no_form(self, client) -> None:
        c, _, aid = client
        r = c.get(f"/dashboard/reviews/{aid}/def67890")
        assert r.status_code == 200
        assert "not part of the review workflow" in r.text
        assert "Record decision" not in r.text

    def test_decide_success_redirects_saved(self, client) -> None:
        c, _, aid = client
        r = c.post(
            f"/dashboard/reviews/{aid}/abc12345/decide",
            data={"target_state": "under_review", "reason": "claimed",
                  "reviewer_identity": "alice", "expected_version": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"].endswith("?saved=1")

    def test_decide_invalid_transition_redirects_error(self, client) -> None:
        c, _, aid = client
        r = c.post(
            f"/dashboard/reviews/{aid}/abc12345/decide",
            data={"target_state": "approved", "reason": "skip",
                  "reviewer_identity": "alice", "expected_version": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "error=invalid_transition" in r.headers["location"]

    def test_decide_concurrency_conflict_redirects_error(self, client) -> None:
        c, _, aid = client
        r = c.post(
            f"/dashboard/reviews/{aid}/abc12345/decide",
            data={"target_state": "under_review", "reason": "c",
                  "reviewer_identity": "alice", "expected_version": "99"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "error=concurrency_conflict" in r.headers["location"]

    def test_review_detail_missing_404(self, client) -> None:
        c, _, _ = client
        r = c.get("/dashboard/reviews/nope/abc12345")
        assert r.status_code == 404


class TestFileBackend:
    @pytest.fixture()
    def file_client(self) -> TestClient:
        dashboard_module.set_repository(FileRepository())
        return TestClient(api_module.app)

    def test_reviews_show_unsupported_notice(self, file_client) -> None:
        r = file_client.get("/dashboard/reviews")
        assert r.status_code == 200
        assert "requires the PostgreSQL" in r.text

    def test_review_detail_unsupported_notice(self, file_client) -> None:
        r = file_client.get("/dashboard/reviews/a/b")
        assert r.status_code == 200
        assert "requires the PostgreSQL" in r.text

    def test_analyses_still_work(self, file_client) -> None:
        r = file_client.get("/dashboard/analyses")
        assert r.status_code == 200
        assert "Analysis History" in r.text

    def test_backend_indicator_file(self, file_client) -> None:
        r = file_client.get("/dashboard")
        assert "File Storage" in r.text


class TestOverviewP7:
    def test_metric_cards_rendered(self, client) -> None:
        c, _, _ = client
        body = c.get("/dashboard").text
        for label in (
            "Total Analyses",
            "Clauses Evaluated",
            "Compliant",
            "Non-Compliant",
            "Needs Review",
            "Review Queue",
        ):
            assert label in body

    def test_compliance_distribution(self, client) -> None:
        c, _, _ = client
        assert "Compliance Distribution" in c.get("/dashboard").text

    def test_empty_overview_guidance(self) -> None:
        repo = InMemoryRepository()
        repo.backend = "postgres"
        dashboard_module.set_repository(repo)
        c = TestClient(api_module.app)
        body = c.get("/dashboard").text
        assert "No compliance analyses yet" in body
        assert "POST /evaluate-bulk" in body

    def test_backend_indicator_postgres(self, client) -> None:
        c, _, _ = client
        body = c.get("/dashboard").text
        assert "PostgreSQL Connected" in body
        assert "online" in body  # green dot indicator

    def test_active_nav_highlight(self, client) -> None:
        c, _, _ = client
        body = c.get("/dashboard").text
        # Overview link is marked active.
        assert 'class="active"' in body


class TestAnalysesP7:
    def test_pagination_controls(self, client) -> None:
        c, _, _ = client
        r = c.get("/dashboard/analyses")
        assert "Page 1 of 1" in r.text
        assert "1 total" in r.text

    def test_no_contract_text_in_pages(self, client) -> None:
        c, _, aid = client
        for path in ("/dashboard", "/dashboard/analyses", f"/dashboard/analyses/{aid}"):
            body = c.get(path).text
            assert "body-a" not in body
            assert "body-b" not in body


class TestSummarize:
    def test_in_memory_summarize_aggregates(self) -> None:
        repo = InMemoryRepository()
        a1 = new_analysis_id()
        a2 = new_analysis_id()
        repo.save_report(_make_report(a1))
        repo.save_report(_make_report(a2))
        m = repo.summarize()
        assert m.total_analyses == 2
        assert m.total_clauses == 4
        assert m.compliant == 0
        assert m.non_compliant == 2
        assert m.needs_review == 2
        assert m.review_pending == 2  # one needs_review record per analysis

    def test_empty_summarize_zero(self) -> None:
        m = InMemoryRepository().summarize()
        assert m.total_analyses == 0
        assert m.review_pending == 0
