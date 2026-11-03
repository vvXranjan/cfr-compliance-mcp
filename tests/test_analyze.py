"""Tests for the server-rendered "Analyze a clause" dashboard page.

Offline and deterministic: the page is a static Jinja2 render plus a small
vanilla-JS client that POSTs to /evaluate-clause. These tests verify the
rendered markup, the example-clause data contract, the absence of any
auth/secret handling, and the safe-DOM (textContent-only) result rendering.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api as api_module
import dashboard as dashboard_module
from agent.persistence.inmemory_repository import InMemoryRepository


@pytest.fixture()
def client() -> tuple[TestClient, InMemoryRepository]:
    repo = InMemoryRepository()
    repo.backend = "postgres"
    dashboard_module.set_repository(repo)
    return TestClient(api_module.app), repo


class TestAnalyzePage:
    def test_page_renders(self, client) -> None:
        c, _ = client
        r = c.get("/dashboard/analyze")
        assert r.status_code == 200
        assert "Analyze a clause" in r.text

    def test_form_fields_present(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        for field_id in ("clause_title", "clause_text", "cfr_title", "cfr_citation", "cfr_text"):
            assert f'id="{field_id}"' in body

    def test_regulation_context_is_collapsible(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "<details" in body
        assert "Regulation context" in body

    def test_help_text_says_endpoint_does_not_lookup(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "look up regulations from eCFR" in body
        assert "is not enough" in body
        assert "cfr_text" in body

    def test_example_buttons_use_labels(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        for label in ("Waste hand-off", "Transport start", "Overseas recycling"):
            assert f">{label}</button>" in body

    def test_example_data_embedded(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "40 CFR 262.18(c)" in body
        assert "40 CFR 263.10(a)" in body
        assert "40 CFR 262.18(e)" in body
        assert "EPA identification number" in body

    def test_calls_evaluate_clause(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert 'fetch("/evaluate-clause"' in body

    def test_no_auth_or_secrets_in_page(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "Authorization" not in body
        assert "api_key" not in body

    def test_needs_review_note_present(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "run a bulk analysis to create a reviewable record" in body

    def test_no_innerhtml_with_response_data(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert "innerHTML" not in body
        assert "textContent" in body
        assert "replaceChildren" in body

    def test_nav_highlight(self, client) -> None:
        c, _ = client
        body = c.get("/dashboard/analyze").text
        assert 'class="active"' in body


class TestFormContract:
    def test_example_payload_evaluates(self, client) -> None:
        c, _ = client
        example = {
            "clause_title": "Waste hand-off",
            "clause_text": (
                "Contractor shall deliver hazardous waste only to transporters and "
                "to treatment, storage, or disposal facilities that hold an EPA "
                "identification number."
            ),
            "cfr_title": 40,
            "cfr_citation": "40 CFR 262.18(c)",
            "cfr_text": (
                "A generator must not offer its hazardous waste to transporters or "
                "to treatment, storage, or disposal facilities that have not "
                "received an EPA identification number."
            ),
        }
        r = c.post("/evaluate-clause", json=example)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("Compliant", "Non-Compliant", "Needs Review")
        assert 0.0 <= data["confidence"] <= 1.0
        assert isinstance(data["evidence"], list)
        assert data["clause_id"]
